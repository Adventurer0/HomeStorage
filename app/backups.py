"""Private, consistent local backups and opt-in WebDAV delivery.

Only the family administrator may call these routes.  A backup deliberately
contains private SQLite configuration and the session signing key; it is not a
public inventory export.  WebDAV credentials never appear in route responses.
"""

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import unicodedata
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import zipfile
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.background import BackgroundTask

from .db import transaction
from .version import runtime_revision, runtime_version


BACKUP_FORMAT = "homestorage-backup-v1"
CHUNK_SIZE = 256 * 1024
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")
PHOTO = re.compile(r"/uploads/([A-Za-z0-9._-]+)\Z")
DEFAULT_SETTINGS = {
    "auto_backup": False, "auto_upload": False, "webdav_url": "", "username": "",
    "password": "", "time": "03:30", "keep_count": 7,
}


def initialize_backups(connect):
    with closing(connect()) as db:
        with transaction(db):
            db.execute("""CREATE TABLE IF NOT EXISTS backup_configuration (
                id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL,
                last_scheduled_day TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS local_backups (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL, uploaded_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS backup_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                phase TEXT NOT NULL, progress TEXT NOT NULL, error TEXT,
                backup_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")


def _service(services, name, default=None):
    return services.get(name, default) if isinstance(services, dict) else getattr(services, name, default)


def _utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _plain(value):
    return not any(unicodedata.category(char) == "Cc" for char in value)


def validated_webdav_url(value):
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        decoded = unquote(parsed.path)
    except (ValueError, UnicodeError):
        raise ValueError("WebDAV 地址格式不正确") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or "\\" in value or "\\" in decoded
        or not _plain(value) or not _plain(decoded) or any(c.isspace() for c in parsed.netloc)
        or (port is not None and not 1 <= port <= 65535)
        or not parsed.path.startswith("/") or parsed.path in {"", "/"}
        or any(part in {".", ".."} for part in decoded.split("/"))):
        raise ValueError("请填写不含密码、查询参数或片段的 HTTP(S) WebDAV 备份目录")
    path = "/".join(quote(part, safe="-._~") for part in decoded.split("/"))
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/") + "/", "", ""))


class BackupSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    auto_backup: StrictBool | None = None
    auto_upload: StrictBool | None = None
    webdav_url: StrictStr | None = Field(default=None, max_length=2048)
    username: StrictStr | None = Field(default=None, max_length=256)
    password: StrictStr | None = Field(default=None, max_length=4096, repr=False)
    time: StrictStr | None = None
    keep_count: StrictInt | None = Field(default=None, ge=1, le=30)

    @field_validator("auto_backup", "auto_upload", "webdav_url", "username", "time", "keep_count", mode="before")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("此设置不能设为空值")
        return value

    @field_validator("webdav_url")
    @classmethod
    def url(cls, value):
        return validated_webdav_url(value.strip())

    @field_validator("username", "password")
    @classmethod
    def credential(cls, value):
        if value is not None and not _plain(value):
            raise ValueError("凭据不能包含控制字符")
        return value

    @field_validator("time")
    @classmethod
    def schedule_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("备份时间应为 HH:MM")
        return value


class BackupCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    upload: StrictBool = False


class BackupConfirmedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool


class BackupError(Exception):
    """Messages are fixed safe strings, never remote HTTP bodies or URLs."""


def _file_digest(path):
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise BackupError("备份源文件不是普通文件")
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_backup(path):
    """Validate a downloaded backup without extracting or modifying live data.

    The returned manifest is informational; this does not install a database or
    restore a session.  SHA256 detects damage, not authenticity against an
    attacker who can replace both a ZIP and its manifest.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > 100_003 or len(set(names)) != len(names):
                raise BackupError("备份清单无效")
            for info in infos:
                name = info.filename
                allowed = name in {"manifest.json", "homestorage.sqlite3", "session.key"} or bool(re.fullmatch(r"uploads/[A-Za-z0-9._-]+", name))
                if (not allowed or info.is_dir() or name.rsplit("/", 1)[-1] in {".", ".."}
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    raise BackupError("备份包含不安全的文件路径")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > 32 * 1024 * 1024:
                raise BackupError("备份清单过大")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT or not isinstance(manifest.get("files"), list):
                raise BackupError("备份格式不受支持")
            entries = manifest["files"]
            if any(not isinstance(entry, dict) or not isinstance(entry.get("path"), str) for entry in entries):
                raise BackupError("备份清单无效")
            indexed = {entry["path"]: entry for entry in entries}
            if (len(indexed) != len(entries) or set(indexed) != set(names) - {"manifest.json"}
                    or not {"homestorage.sqlite3", "session.key"}.issubset(indexed)):
                raise BackupError("备份清单与文件不一致")
            for name, entry in indexed.items():
                size = entry.get("size")
                digest = entry.get("sha256")
                if (type(size) is not int or size < 0 or archive.getinfo(name).file_size != size
                        or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                        or (name == "session.key" and size != 32)):
                    raise BackupError("备份文件校验失败")
                actual = hashlib.sha256()
                with archive.open(name) as source:
                    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                        actual.update(chunk)
                if actual.hexdigest() != digest:
                    raise BackupError("备份文件校验失败")
            return manifest
    except BackupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError):
        raise BackupError("备份文件无法读取或清单无效") from None


class BackupManager:
    def __init__(self, services):
        self.services = services
        self.connect = _service(services, "connect")
        self.data_dir = Path(_service(services, "data_dir"))
        self.timezone = str(_service(services, "timezone", "Asia/Shanghai"))
        self.zone = ZoneInfo(self.timezone)
        self.directory = self.data_dir / "backups"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tasks = set()
        self._scheduler = None

    def authorize(self, request):
        # External integrations receive inventory-only scopes.  Even a future
        # overly broad token must not acquire full private backup/config access.
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            raise HTTPException(403, "外部 API 令牌不能访问家庭备份")
        callback = _service(self.services, "require_admin")
        if callback is None:
            raise HTTPException(403, "需要家庭管理员登录")
        return callback(request)

    def maintenance_active(self):
        check = _service(self.services, "maintenance_active")
        if check is not None:
            return bool(check())
        return (Path(os.environ.get("UPDATE_STATE_DIR", "/run/homestorage-update")) / "maintenance.flag").is_file()

    def _ensure_directory(self):
        if self.directory.is_symlink():
            raise BackupError("备份目录不能是符号链接")
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        if not self.directory.is_dir():
            raise BackupError("备份目录不可用")

    def settings(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT value FROM backup_configuration WHERE id=1").fetchone()
        return {**DEFAULT_SETTINGS, **(json.loads(row["value"]) if row else {})}

    def public_settings(self, settings=None):
        settings = settings or self.settings()
        return {**{key: value for key, value in settings.items() if key != "password"},
                "password_set": bool(settings["password"]), "timezone": self.timezone,
                "http_warning": settings["webdav_url"].startswith("http://")}

    def save_settings(self, body):
        if not self._lock.acquire(blocking=False):
            raise HTTPException(409, "备份任务进行中，请完成后再修改设置")
        try:
            current = self.settings()
            changes = body.model_dump(exclude_unset=True)
            new_password = changes.pop("password", "")
            target_changed = any(key in changes and changes[key] != current[key]
                                 for key in ("webdav_url", "username"))
            updated = {**current, **changes}
            if new_password is None:
                updated["password"] = ""
            elif new_password:
                updated["password"] = new_password
            elif target_changed:
                updated["password"] = ""
            if updated["auto_upload"] and not updated["webdav_url"]:
                raise HTTPException(422, "自动上传需要先填写 WebDAV 备份目录")
            with closing(self.connect()) as db:
                with transaction(db):
                    db.execute("INSERT INTO backup_configuration(id,value) VALUES(1,?) "
                               "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                               (json.dumps(updated, ensure_ascii=False),))
            return self.public_settings(updated)
        finally:
            self._lock.release()

    def job(self, identifier):
        if not IDENTIFIER.fullmatch(identifier):
            raise HTTPException(404, "备份任务不存在")
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM backup_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise HTTPException(404, "备份任务不存在")
        result = dict(row)
        result["progress"] = json.loads(result["progress"])
        return result

    def listing(self):
        with closing(self.connect()) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM local_backups ORDER BY created_at DESC,id DESC")]
            active = db.execute("SELECT id FROM backup_jobs WHERE status IN ('pending','running') ORDER BY created_at DESC LIMIT 1").fetchone()
            latest = db.execute("SELECT id FROM backup_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
        return {"backups": rows, "active_job": self.job(active["id"]) if active else None,
                "last_job": self.job(latest["id"]) if latest else None}

    def backup(self, identifier):
        if not IDENTIFIER.fullmatch(identifier):
            raise HTTPException(404, "备份不存在")
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM local_backups WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise HTTPException(404, "备份不存在")
        result = dict(row)
        expected = re.fullmatch(r"homestorage-[0-9TZ]+-[a-f0-9]{32}\.zip", result["filename"])
        path = self.directory / result["filename"]
        if not expected or path.is_symlink() or not path.is_file():
            raise HTTPException(404, "备份文件不存在或不可用")
        return result, path

    def delete(self, identifier):
        if not self._lock.acquire(blocking=False):
            raise HTTPException(409, "备份任务进行中，请稍后删除")
        try:
            result, path = self.backup(identifier)
            path.unlink()
            _fsync_directory(self.directory)
            with closing(self.connect()) as db:
                with transaction(db):
                    db.execute("DELETE FROM local_backups WHERE id=?", (result["id"],))
            return {"ok": True}
        finally:
            self._lock.release()

    def download(self, identifier):
        backup, path = self.backup(identifier)
        # Open before returning the response. Retention or an administrator may
        # remove an old archive while it is being downloaded; an already-open
        # descriptor keeps that download consistent on the supported NAS hosts.
        # O_NONBLOCK also prevents a replaced FIFO from blocking this request.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            source = os.fdopen(os.open(path, flags), "rb")
        except OSError:
            raise HTTPException(404, "备份文件不存在或不可用") from None
        information = os.fstat(source.fileno())
        if not stat.S_ISREG(information.st_mode) or information.st_size != backup["size"]:
            source.close()
            raise HTTPException(409, "备份文件发生变化，请重新创建备份")

        def content():
            try:
                yield from iter(lambda: source.read(CHUNK_SIZE), b"")
            finally:
                source.close()

        return StreamingResponse(
            content(), media_type="application/zip", background=BackgroundTask(source.close),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                     "Content-Length": str(information.st_size),
                     "Content-Disposition": f'attachment; filename="{backup["filename"]}"'},
        )

    def _progress(self, job_id, phase, completed=0, total=0, **changes):
        with closing(self.connect()) as db:
            with transaction(db):
                db.execute("UPDATE backup_jobs SET phase=?,progress=?,updated_at=? WHERE id=?",
                           (phase, json.dumps({"completed_bytes": completed, "total_bytes": total}), _utcnow(), job_id))
                for key, value in changes.items():
                    if key in {"status", "error", "backup_id"}:
                        db.execute(f"UPDATE backup_jobs SET {key}=? WHERE id=?", (value, job_id))

    def _check_running(self):
        if self._stop.is_set():
            raise BackupError("服务关闭中，备份任务已停止，请重新执行")
        if self.maintenance_active():
            raise BackupError("程序正在维护，备份任务已停止，请维护后重试")

    async def enqueue(self, *, upload=False, backup_id=None, scheduled_day=None):
        if self.maintenance_active():
            raise HTTPException(503, "程序正在维护，请稍后备份")
        if not self._lock.acquire(blocking=False):
            raise HTTPException(409, "已有备份任务进行中")
        try:
            settings = self.settings()
            if upload and not settings["webdav_url"]:
                raise HTTPException(422, "请先设置 WebDAV 备份目录")
            if backup_id is not None:
                self.backup(backup_id)
            job_id = secrets.token_hex(16)
            timestamp = _utcnow()
            with closing(self.connect()) as db:
                with transaction(db):
                    if scheduled_day:
                        db.execute("INSERT INTO backup_configuration(id,value,last_scheduled_day) VALUES(1,?,?) "
                                   "ON CONFLICT(id) DO UPDATE SET last_scheduled_day=excluded.last_scheduled_day",
                                   (json.dumps(settings, ensure_ascii=False), scheduled_day))
                    db.execute("INSERT INTO backup_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                               (job_id, "upload" if backup_id else "backup", "pending", "queued",
                                json.dumps({"completed_bytes": 0, "total_bytes": 0}), None,
                                backup_id, timestamp, timestamp))
            task = asyncio.create_task(asyncio.to_thread(self._run, job_id, settings, upload, backup_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return self.job(job_id)
        except BaseException:
            self._lock.release()
            raise

    def _run(self, job_id, settings, upload, backup_id):
        try:
            self._progress(job_id, "snapshot", status="running")
            self._check_running()
            if backup_id is None:
                backup_id = self._create_backup(job_id)
                self._progress(job_id, "archive", backup_id=backup_id)
                self._prune(settings["keep_count"], keep_id=backup_id)
            if upload:
                self._upload(job_id, backup_id, settings)
            self._progress(job_id, "complete", status="succeeded", backup_id=backup_id)
        except BackupError as error:
            self._progress(job_id, "failed", status="failed", error=str(error), backup_id=backup_id)
        except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError):
            self._progress(job_id, "failed", status="failed", error="本地备份失败，请检查存储空间、文件和数据卷权限", backup_id=backup_id)
        except Exception:
            self._progress(job_id, "failed", status="failed", error="备份任务未完成，请稍后重试", backup_id=backup_id)
        finally:
            self._lock.release()

    def _create_backup(self, job_id):
        self._ensure_directory()
        identifier = secrets.token_hex(16)
        timestamp = _utcnow()
        filename = f"homestorage-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{identifier}.zip"
        destination = self.directory / filename
        temporary_archive = self.directory / (".pending-" + identifier)
        published = False
        try:
            with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=self.directory) as workspace:
                snapshot = Path(workspace) / "homestorage.sqlite3"
                with closing(self.connect()) as live:
                    page_count = live.execute("PRAGMA page_count").fetchone()[0]
                    page_size = live.execute("PRAGMA page_size").fetchone()[0]
                    if shutil.disk_usage(self.directory).free < page_count * page_size * 2 + 5 * 1024 * 1024:
                        raise BackupError("存储空间不足，未生成备份")
                    with closing(sqlite3.connect(snapshot)) as db:
                        live.backup(db, pages=128, progress=lambda *args: self._check_running())
                with closing(sqlite3.connect(snapshot)) as db:
                    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchone():
                        raise BackupError("数据库校验未通过，未生成备份")
                    photos = sorted({row[0] for row in db.execute("SELECT image_url FROM items WHERE image_url IS NOT NULL") if row[0].startswith("/uploads/")})
                    migrations = [row[0] for row in db.execute("SELECT name FROM schema_migrations ORDER BY name")]
                    schema_version = db.execute("PRAGMA user_version").fetchone()[0]
                files = [("homestorage.sqlite3", snapshot), ("session.key", self.data_dir / "session.key")]
                uploads = self.data_dir / "uploads"
                if uploads.is_symlink():
                    raise BackupError("照片目录不能是符号链接")
                for photo in photos:
                    match = PHOTO.fullmatch(photo)
                    if not match or match[1] in {".", ".."}:
                        raise BackupError("物品照片路径无效，未生成备份")
                    files.append(("uploads/" + match[1], uploads / match[1]))
                total = 0
                for name, path in files:
                    if path.is_symlink() or not path.is_file():
                        raise BackupError("备份引用的照片或会话密钥缺失，请恢复文件后重试")
                    size = path.stat().st_size
                    if name == "session.key" and size != 32:
                        raise BackupError("会话密钥无效，未生成备份")
                    total += size
                if shutil.disk_usage(self.directory).free < total + 5 * 1024 * 1024:
                    raise BackupError("存储空间不足，未生成备份")
                manifest = {
                    "format": BACKUP_FORMAT, "created_at": timestamp,
                    "version": runtime_version(), "revision": runtime_revision(),
                    "schema_migrations": migrations, "sqlite_user_version": schema_version, "files": [],
                    "private": True,
                    "notice": "包含家庭用户、API/AI/WebDAV 凭据和会话密钥，请妥善保存；照片仅包含此数据库快照引用的本地上传文件。",
                }
                self._progress(job_id, "archive", 0, total)
                completed = 0
                with temporary_archive.open("xb") as output:
                    os.chmod(temporary_archive, 0o600)
                    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
                        for name, path in files:
                            self._check_running()
                            digest = hashlib.sha256()
                            size = 0
                            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                            with os.fdopen(descriptor, "rb") as source, archive.open(name, "w", force_zip64=True) as target:
                                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                                    raise BackupError("备份源文件不是普通文件")
                                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                                    self._check_running()
                                    target.write(chunk)
                                    digest.update(chunk)
                                    size += len(chunk)
                                    completed += len(chunk)
                                    self._progress(job_id, "archive", completed, total)
                            manifest["files"].append({"path": name, "size": size, "sha256": digest.hexdigest()})
                        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    output.flush()
                    os.fsync(output.fileno())
                digest = _file_digest(temporary_archive)
                size = temporary_archive.stat().st_size
                verify_backup(temporary_archive)
                os.replace(temporary_archive, destination)
                _fsync_directory(self.directory)
                published = True
                with closing(self.connect()) as db:
                    with transaction(db):
                        db.execute("INSERT INTO local_backups VALUES(?,?,?,?,?,NULL)", (identifier, filename, size, digest, timestamp))
                return identifier
        except BaseException:
            temporary_archive.unlink(missing_ok=True)
            if published:
                destination.unlink(missing_ok=True)
            raise

    def _upload(self, job_id, backup_id, settings):
        backup, path = self.backup(backup_id)
        if path.stat().st_size != backup["size"] or _file_digest(path) != backup["sha256"]:
            raise BackupError("备份文件校验失败，已取消上传")
        verify_backup(path)
        target = settings["webdav_url"]
        if not target:
            raise BackupError("尚未设置 WebDAV 备份目录")
        self._progress(job_id, "upload", 0, backup["size"])
        parsed = urlsplit(target)
        auth = httpx.BasicAuth(settings["username"], settings["password"]) if settings["username"] or settings["password"] else None

        def checked(response, allowed):
            if response.status_code in allowed:
                return
            if response.status_code in {401, 403}:
                raise BackupError("WebDAV 认证失败或没有写入权限，请检查账号与目录权限")
            if 300 <= response.status_code < 400:
                raise BackupError("WebDAV 返回重定向，请填写最终目录地址后重试")
            if response.status_code == 507:
                raise BackupError("WebDAV 网盘空间不足")
            raise BackupError("WebDAV 上传失败，请检查目录和服务状态")

        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), auth=auth, follow_redirects=False, trust_env=False) as client:
                components = [part for part in parsed.path.split("/") if part]
                for index in range(len(components)):
                    self._check_running()
                    directory_url = urlunsplit((parsed.scheme, parsed.netloc, "/" + "/".join(components[:index + 1]) + "/", "", ""))
                    # Do not download an unbounded WebDAV error page into RAM.
                    with client.stream("MKCOL", directory_url) as response:
                        checked(response, {200, 201, 204, 405})

                def chunks():
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    sent = 0
                    with os.fdopen(descriptor, "rb") as source:
                        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                            self._check_running()
                            sent += len(chunk)
                            self._progress(job_id, "upload", sent, backup["size"])
                            yield chunk

                with client.stream("PUT", target + quote(backup["filename"], safe="-._"), content=chunks(),
                                   headers={"Content-Type": "application/zip", "Content-Length": str(backup["size"])}) as response:
                    checked(response, {200, 201, 204})
        except httpx.HTTPError:
            raise BackupError("无法连接 WebDAV，请检查 NAS 网络、地址和 TLS 证书") from None
        with closing(self.connect()) as db:
            with transaction(db):
                db.execute("UPDATE local_backups SET uploaded_at=? WHERE id=?", (_utcnow(), backup_id))

    def _prune(self, keep_count, keep_id):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id FROM local_backups ORDER BY created_at DESC,id DESC").fetchall()
        for row in rows[keep_count:]:
            if row["id"] == keep_id:
                continue
            _, path = self.backup(row["id"])
            path.unlink()
            with closing(self.connect()) as db:
                with transaction(db):
                    db.execute("DELETE FROM local_backups WHERE id=?", (row["id"],))
        _fsync_directory(self.directory)

    async def tick(self, instant=None):
        """One deterministic scheduler check; never replays a missed prior day."""
        if self._stop.is_set() or self.maintenance_active():
            return None
        local = (instant or datetime.now(timezone.utc)).astimezone(self.zone)
        settings = self.settings()
        if not settings["auto_backup"] or local.strftime("%H:%M") != settings["time"]:
            return None
        with closing(self.connect()) as db:
            row = db.execute("SELECT last_scheduled_day FROM backup_configuration WHERE id=1").fetchone()
        day = local.date().isoformat()
        if row and row["last_scheduled_day"] >= day:
            return None
        try:
            return await self.enqueue(upload=settings["auto_upload"], scheduled_day=day)
        except HTTPException as error:
            if error.status_code in {409, 503}:
                return None
            raise

    async def _schedule(self):
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                # No credentials or arbitrary exception strings go to logs/status.
                pass
            await asyncio.sleep(20)

    async def start(self):
        self._stop.clear()
        self._ensure_directory()
        with closing(self.connect()) as db:
            with transaction(db):
                db.execute("UPDATE backup_jobs SET status='failed',phase='failed',error=?,updated_at=? "
                           "WHERE status IN ('pending','running')",
                           ("服务已重启，先前任务未完成；请重新执行", _utcnow()))
        # Crash leftovers are never considered complete backups.  They stay
        # outside the catalogue and can safely be removed on startup.
        for path in self.directory.iterdir():
            if path.name.startswith((".pending-", ".snapshot-")) and not path.is_symlink():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
        with closing(self.connect()) as db:
            known_files = {row["filename"] for row in db.execute("SELECT filename FROM local_backups")}
        for path in self.directory.iterdir():
            if (re.fullmatch(r"homestorage-[0-9TZ]+-[a-f0-9]{32}\.zip", path.name)
                    and path.name not in known_files and not path.is_symlink() and path.is_file()):
                # A crash between atomic rename and SQLite catalogue commit can
                # leave a valid but unpublished ZIP.  Never call it a success.
                path.unlink()
        _fsync_directory(self.directory)
        self._scheduler = asyncio.create_task(self._schedule())

    async def stop(self):
        self._stop.set()
        if self._scheduler:
            self._scheduler.cancel()
            await asyncio.gather(self._scheduler, return_exceptions=True)
            self._scheduler = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


def build_backup_router(services):
    manager = BackupManager(services)
    router = APIRouter(prefix="/api/backups", tags=["private-backups"])
    router.backup_manager = manager

    @router.get("/settings")
    async def get_settings(request: Request):
        manager.authorize(request)
        return manager.public_settings()

    @router.patch("/settings")
    async def patch_settings(body: BackupSettingsInput, request: Request):
        manager.authorize(request)
        if manager.maintenance_active():
            raise HTTPException(503, "程序正在维护，请稍后修改备份设置")
        return manager.save_settings(body)

    @router.get("")
    async def list_backups(request: Request):
        manager.authorize(request)
        return manager.listing()

    @router.post("", status_code=202)
    async def create_backup(body: BackupCreateInput, request: Request):
        manager.authorize(request)
        if not body.confirmed:
            raise HTTPException(400, "请先确认创建私密完整备份")
        return await manager.enqueue(upload=body.upload)

    @router.get("/jobs/{identifier}")
    async def get_job(identifier: str, request: Request):
        manager.authorize(request)
        return manager.job(identifier)

    @router.get("/{identifier}/download")
    async def download(identifier: str, request: Request):
        manager.authorize(request)
        return manager.download(identifier)

    @router.post("/{identifier}/upload", status_code=202)
    async def upload(identifier: str, body: BackupConfirmedInput, request: Request):
        manager.authorize(request)
        if not body.confirmed:
            raise HTTPException(400, "请先确认将完整私密备份上传到所选 WebDAV 目录")
        return await manager.enqueue(upload=True, backup_id=identifier)

    @router.delete("/{identifier}")
    async def delete(identifier: str, body: BackupConfirmedInput, request: Request):
        manager.authorize(request)
        if not body.confirmed:
            raise HTTPException(400, "请先确认删除本地备份")
        if manager.maintenance_active():
            raise HTTPException(503, "程序正在维护，请稍后删除备份")
        return manager.delete(identifier)

    return router
