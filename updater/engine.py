"""Transactional replacement of this Compose project's HomeStorage container.

The updater's Docker access is powerful; its public methods deliberately accept
only a checked release version. Container IDs, image names and filesystem paths
are discovered locally or taken from the validated release client, never the API.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .releases import ReleaseError


PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"
ROLE = "io.homestorage.role"
ATTEMPT = "io.homestorage.update-attempt"
VERSION = "org.opencontainers.image.version"
REVISION = "org.opencontainers.image.revision"
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class UpdateError(RuntimeError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def _now():
    return datetime.now(timezone.utc).isoformat()


def _version(value):
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise UpdateError("版本信息无效，请重新检查更新。")
    return tuple(int(part) for part in value.split("."))


class UpdateEngine:
    HEALTH_TIMEOUT = 120
    HEALTH_POLL = 1

    def __init__(self, docker, store, releases, *, self_id, data_dir, state_dir,
                 target_service="homestorage"):
        self.docker = docker
        self.store = store
        self.releases = releases
        self.self_id = self_id
        self.data_dir = Path(data_dir).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.target_service = target_service
        self._lock = threading.Lock()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.state_dir / "backups"
        self.backup_dir.mkdir(mode=0o700, exist_ok=True)
        self.backup_dir.chmod(0o700)

    def _labels(self, container):
        return (container.get("Config") or {}).get("Labels") or {}

    def _identity(self):
        own = self.docker.inspect(self.self_id)
        project = self._labels(own).get(PROJECT)
        if not isinstance(project, str) or not project or self._labels(own).get(ROLE) != "updater":
            raise UpdateError("更新服务必须与 HomeStorage 放在同一个 Compose 项目中。")
        mount = self._mount(own, "/app-data")
        return project, mount

    @staticmethod
    def _mount(container, destination):
        matches = [m for m in container.get("Mounts", []) if m.get("Destination") == destination]
        if len(matches) != 1 or matches[0].get("Type") not in {"bind", "volume"}:
            raise UpdateError("没有找到唯一的 HomeStorage 数据挂载，已拒绝更新。")
        mount = matches[0]
        if not isinstance(mount.get("Source"), str) or not mount["Source"] or mount.get("RW") is not True:
            raise UpdateError("HomeStorage 数据挂载必须可读写，已拒绝更新。")
        return {key: mount.get(key) for key in ("Type", "Source", "Name")}

    def _validate_target(self, target, project, mount):
        labels = self._labels(target)
        if (labels.get(PROJECT) != project or labels.get(SERVICE) != self.target_service
                or labels.get(ROLE) != "app" or self._mount(target, "/data") != mount):
            raise UpdateError("目标容器或数据卷与本项目不一致，已拒绝更新。")
        if target.get("Id") == self.self_id:
            raise UpdateError("更新服务不能替换自身。")
        return target

    def _discover(self):
        project, mount = self._identity()
        candidates = self.docker.list_containers(all=True, filters={"label": [
            f"{PROJECT}={project}", f"{SERVICE}={self.target_service}", f"{ROLE}=app",
        ]})
        active = []
        for candidate in candidates:
            target = self.docker.inspect(candidate["Id"])
            self._validate_target(target, project, mount)
            state = target.get("State") or {}
            if state.get("Running") is True:
                active.append(target)
        if len(active) != 1:
            raise UpdateError("需要恰好一个正在运行的 HomeStorage 容器，已拒绝更新。")
        return active[0], project, mount

    def _current(self, target):
        labels = self._labels(target)
        current = labels.get(VERSION)
        if not current:
            values = dict(entry.split("=", 1) for entry in (target.get("Config") or {}).get("Env", [])
                          if isinstance(entry, str) and "=" in entry)
            current = values.get("HOMESTORAGE_VERSION", "")
        _version(current)
        revision = labels.get(REVISION) or ""
        return current, revision if isinstance(revision, str) and SHA.fullmatch(revision) else ""

    def status(self):
        state = self.store.get()
        available = True
        message = state.get("message", "")
        current, revision = state.get("current_version", ""), state.get("current_revision", "")
        # During a replacement there may briefly be no active app; the recorded
        # journal is authoritative until recovery completes.
        if not state.get("journal"):
            try:
                target, _, _ = self._discover()
                current, revision = self._current(target)
            except Exception:
                available = False
                if state.get("phase") not in {"failed", "rolled_back"}:
                    message = "更新服务尚未就绪，请确认同一 Compose 项目中的更新服务、容器标签及数据卷配置。"
        release = state.get("release_cache") or {}
        latest = release.get("version")
        update_available = False
        try:
            update_available = bool(latest and _version(latest) > _version(current))
        except UpdateError:
            pass
        backups = state.get("backups") or []
        progress = state.get("progress") or {}
        return {
            "available": available, "current_version": current,
            "current_revision": revision, "auto_update": bool(state.get("auto_update", False)),
            "update_available": update_available, "latest_version": latest,
            "release_notes": str(release.get("notes") or ""), "release_url": release.get("url"),
            "last_checked_at": state.get("last_checked_at"),
            "last_backup_at": state.get("last_backup_at"),
            "next_check_at": state.get("next_check_at"),
            "phase": state.get("phase", "idle"), "message": message,
            "backup_count": len(backups),
            "progress": {"downloaded_bytes": int(progress.get("downloaded_bytes", 0)),
                         "total_bytes": int(progress.get("total_bytes", 0))},
        }

    def _arch(self):
        architecture = self.docker.info().get("Architecture")
        architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(architecture, architecture)
        if architecture not in {"amd64", "arm64"}:
            raise UpdateError("目前更新镜像支持 x86_64 和 ARM64 NAS。")
        return architecture

    def check(self):
        if not self._lock.acquire(blocking=False):
            raise UpdateError("正在执行另一项更新任务，请稍后重试。")
        try:
            if self.store.get().get("journal"):
                raise UpdateError("上次更新仍需恢复，请先恢复更新服务。")
            target, _, _ = self._discover()
            current, revision = self._current(target)
            self.store.update(phase="checking", message="正在检查 GitHub 稳定版本。",
                              current_version=current, current_revision=revision)
            arch = self._arch()
            release = self.releases.latest(arch)
            self._validate_release(release)
            self.store.update(release_cache={**release, "arch": arch}, last_checked_at=_now(),
                              phase="idle", message=("发现可安装的新版本。" if _version(release["version"]) > _version(current)
                                                     else "当前已是最新稳定版本。"))
            return self.status()
        except UpdateError as exc:
            self.store.update(phase="failed", message=str(exc))
            raise
        except ReleaseError as exc:
            self.store.update(phase="failed", message=str(exc))
            raise UpdateError(str(exc), 502) from exc
        except Exception as exc:
            self.store.update(phase="failed", message="无法检查更新，请检查 NAS 到 GitHub 的网络连接。")
            raise UpdateError("无法检查更新，请检查 NAS 到 GitHub 的网络连接。", 502) from exc
        finally:
            self._lock.release()

    @staticmethod
    def _validate_release(release):
        if not isinstance(release, dict):
            raise UpdateError("发布信息无效，已拒绝更新。")
        _version(release.get("version"))
        if not isinstance(release.get("revision"), str) or not SHA.fullmatch(release["revision"]):
            raise UpdateError("发布版本的源码标识无效，已拒绝更新。")
        if (not isinstance(release.get("image"), str) or
                not re.fullmatch(r"homestorage:[0-9]+\.[0-9]+\.[0-9]+-(amd64|arm64)", release["image"])):
            raise UpdateError("发布镜像名称无效，已拒绝更新。")

    def _journal(self, journal, stage, phase=None, message=None, **changes):
        journal = {**journal, **changes, "stage": stage}
        updates = {"journal": journal}
        if phase:
            updates["phase"] = phase
        if message:
            updates["message"] = message
        self.store.update(**updates)
        return journal

    def _maintenance(self, enabled):
        flag = self.state_dir / "maintenance.flag"
        if enabled:
            descriptor = os.open(flag, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(descriptor, b"HomeStorage update in progress\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            flag.chmod(0o644)
        else:
            flag.unlink(missing_ok=True)
        self._sync_dir(self.state_dir)

    @staticmethod
    def _sync_dir(directory):
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _sync_tree(directory):
        """Make restored contents durable before the recovery journal is cleared.

        Syncing the mount root alone does not persist restored file contents or
        entries in nested directories. Open children relative to their parent
        descriptor so links cannot redirect this walk outside the restored tree.
        Links are persisted by their parent directory's fsync, never followed.
        """
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

        def sync_directory(descriptor):
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise UpdateError("恢复目录已发生变化，恢复已暂停。")
            for name in os.listdir(descriptor):
                child_metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISLNK(child_metadata.st_mode):
                    continue
                if stat.S_ISDIR(child_metadata.st_mode):
                    child = os.open(name, directory_flags, dir_fd=descriptor)
                    try:
                        sync_directory(child)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(child_metadata.st_mode):
                    # O_NONBLOCK avoids blocking if a file is unexpectedly
                    # replaced by a FIFO between stat and open.
                    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                    dir_fd=descriptor)
                    try:
                        if not stat.S_ISREG(os.fstat(child).st_mode):
                            raise UpdateError("恢复文件已发生变化，恢复已暂停。")
                        os.fsync(child)
                    finally:
                        os.close(child)
                else:
                    raise UpdateError("恢复目录含有不支持的文件，恢复已暂停。")
            # Listing a directory may change its atime. Preserve the restored
            # timestamps before syncing its metadata and child entries.
            os.utime(descriptor, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            os.fsync(descriptor)

        descriptor = os.open(directory, directory_flags)
        try:
            sync_directory(descriptor)
        finally:
            os.close(descriptor)

    def _backup(self, destination):
        if not self.data_dir.is_dir():
            raise UpdateError("数据目录不存在，已停止更新。")
        total = 0
        for parent, dirs, files in os.walk(self.data_dir, followlinks=False):
            for name in dirs + files:
                path = Path(parent) / name
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    # Restoring links pointing outside /data could overwrite the
                    # updater's token or socket; reject such data before archiving.
                    if not path.resolve().is_relative_to(self.data_dir):
                        raise UpdateError("数据目录含有指向外部的链接，请先移除该链接再更新。")
                elif stat.S_ISREG(metadata.st_mode):
                    total += metadata.st_size
                elif not stat.S_ISDIR(metadata.st_mode):
                    raise UpdateError("数据目录含有不支持备份的特殊文件，已停止更新。")
        if shutil.disk_usage(self.backup_dir).free < total + 16 * 1024 * 1024:
            raise UpdateError("更新备份空间不足，已停止更新。")
        temporary = destination.with_suffix(destination.suffix + ".partial")
        try:
            with tarfile.open(temporary, "w:gz", dereference=False) as archive:
                archive.add(self.data_dir, arcname="data", recursive=True)
            temporary.chmod(0o600)
            with temporary.open("rb") as source:
                os.fsync(source.fileno())
            # Parse and verify all member paths before accepting the backup.
            with tarfile.open(temporary, "r:gz") as archive:
                self._validate_members(archive.getmembers())
            os.replace(temporary, destination)
            self._sync_dir(self.backup_dir)
            return {"backup_size": destination.stat().st_size,
                    "backup_sha256": self._file_digest(destination)}
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_members(members):
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or not path.parts or path.parts[0] != "data" or ".." in path.parts:
                raise UpdateError("备份路径无效，已停止恢复。")
            if str(path) in names:
                raise UpdateError("备份包含重复路径，已停止恢复。")
            names.add(str(path))
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise UpdateError("备份包含不支持的文件，已停止恢复。")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute():
                    raise UpdateError("备份链接无效，已停止恢复。")
                combined = (path.parent / link) if member.issym() else link
                depth = 0
                for part in combined.parts:
                    if part == "..":
                        depth -= 1
                    elif part != ".":
                        depth += 1
                    if depth < 1:
                        raise UpdateError("备份链接越过数据目录，已停止恢复。")
                if not combined.parts or combined.parts[0] != "data":
                    raise UpdateError("备份链接无效，已停止恢复。")

    def _backup_path(self, journal):
        filename = journal.get("backup_file")
        if not isinstance(filename, str) or not re.fullmatch(r"backup-[0-9a-f]{32}\.tar\.gz", filename):
            raise UpdateError("恢复日志的备份标识无效。")
        destination = self.backup_dir / filename
        if destination.is_symlink():
            raise UpdateError("备份文件不能是链接。")
        return destination

    def _restore(self, journal):
        destination = self._backup_path(journal)
        if not destination.is_file():
            raise UpdateError("没有找到完整备份，恢复已暂停。")
        if (not isinstance(journal.get("backup_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", journal["backup_sha256"])
                or destination.stat().st_size != journal.get("backup_size")
                or self._file_digest(destination) != journal["backup_sha256"]):
            raise UpdateError("备份校验未通过，恢复已暂停，现有数据未删除。")
        # Tar parsing can stop at tar's end marker before gzip's CRC footer.
        # Fully consume gzip before deleting any live data.
        with gzip.open(destination, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
        with tarfile.open(destination, "r:gz") as archive:
            members = archive.getmembers()
            self._validate_members(members)
            for member in members:
                if member.isfile():
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise UpdateError("备份内容不完整，恢复已暂停。")
                    while stream.read(1024 * 1024):
                        pass
        # Marking the journal 'restoring' is done before entering here. A crash
        # partway through extraction safely repeats deletion and restoration.
        for path in self.data_dir.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        with tarfile.open(destination, "r:gz") as archive:
            members = archive.getmembers()
            # Remove the artificial archive root, preserving original uid/gid,
            # modes and times. Paths and links were strictly validated above.
            transformed = []
            root_metadata = None
            for original in members:
                if original.name.rstrip("/") == "data":
                    root_metadata = original
                    continue
                member = copy.copy(original)
                member.name = str(PurePosixPath(member.name).relative_to("data"))
                if member.islnk():
                    member.linkname = str(PurePosixPath(member.linkname).relative_to("data"))
                transformed.append(member)
            archive.extractall(self.data_dir, members=transformed, filter="fully_trusted")
        if root_metadata is not None:
            os.chown(self.data_dir, root_metadata.uid, root_metadata.gid)
            self.data_dir.chmod(root_metadata.mode & 0o777)
            os.utime(self.data_dir, (root_metadata.mtime, root_metadata.mtime))
        self._sync_tree(self.data_dir)

    @staticmethod
    def _file_digest(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _replacement_config(self, old, image, release, attempt):
        source = old.get("Config") or {}
        config = {key: copy.deepcopy(source[key]) for key in (
            "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr",
            "ExposedPorts", "Tty", "OpenStdin", "StdinOnce", "Cmd", "Healthcheck", "Volumes",
            "WorkingDir", "Entrypoint", "NetworkDisabled", "MacAddress", "StopSignal", "StopTimeout",
            "Shell",
        ) if key in source}
        config["Image"] = image["Id"]
        config["Env"] = [entry for entry in source.get("Env", [])
                         if entry.split("=", 1)[0] not in {"HOMESTORAGE_VERSION", "HOMESTORAGE_REVISION"}]
        config["Env"] += [f"HOMESTORAGE_VERSION={release['version']}",
                          f"HOMESTORAGE_REVISION={release['revision']}"]
        labels = copy.deepcopy(source.get("Labels") or {})
        labels.update({VERSION: release["version"], REVISION: release["revision"],
                       ATTEMPT: attempt, "com.docker.compose.image": image["Id"]})
        config["Labels"] = labels
        config["HostConfig"] = copy.deepcopy(old.get("HostConfig") or {})
        # Inspect includes derived, read-only endpoint fields. Only reapply the
        # requested IPAM, aliases, links and options, preserving all networks.
        endpoints = {}
        old_id = old.get("Id", "")
        for name, endpoint in ((old.get("NetworkSettings") or {}).get("Networks") or {}).items():
            endpoints[name] = {key: copy.deepcopy(endpoint[key]) for key in
                               ("IPAMConfig", "Links", "DriverOpts") if endpoint.get(key) is not None}
            aliases = endpoint.get("Aliases")
            if aliases is not None:
                endpoints[name]["Aliases"] = [alias for alias in aliases
                                             if alias not in {old_id, old_id[:12]}]
        if endpoints:
            config["NetworkingConfig"] = {"EndpointsConfig": endpoints}
        return config

    def _verify_image(self, image, release, arch):
        labels = (image.get("Config") or {}).get("Labels") or {}
        if (image.get("Architecture") != arch or image.get("Os") != "linux"
                or labels.get(VERSION) != release["version"]
                or labels.get(REVISION) != release["revision"] or not image.get("Id")):
            raise UpdateError("镜像架构、版本或源码标识与发布信息不符，已拒绝更新。")

    def _verify_archive(self, destination, release, arch):
        """Verify Docker save structure without extracting any image content."""
        try:
            members = {}
            manifest = None
            with gzip.open(destination, "rb") as compressed:
                with tarfile.open(fileobj=compressed, mode="r|") as archive:
                    for member in archive:
                        name = PurePosixPath(member.name)
                        if (name.is_absolute() or ".." in name.parts or not name.parts
                                or not (member.isdir() or member.isfile())
                                or str(name) in members or len(members) >= 100000
                                or member.size < 0 or member.offset_data + member.size > 4 * 1024 ** 3):
                            raise UpdateError("更新镜像归档结构无效，已拒绝加载。")
                        members[str(name)] = member.size
                        if member.isfile():
                            stream = archive.extractfile(member)
                            if stream is None:
                                raise UpdateError("更新镜像归档不完整。")
                            if str(name) == "manifest.json":
                                if member.size > 1024 * 1024:
                                    raise UpdateError("更新镜像清单过大。")
                                manifest = json.loads(stream.read())
                            else:
                                while stream.read(1024 * 1024):
                                    pass
                        if compressed.tell() > 4 * 1024 ** 3:
                            raise UpdateError("更新镜像解压后过大。")
                # Validate gzip CRC and reject a compressed append bomb too.
                while compressed.read(1024 * 1024):
                    if compressed.tell() > 4 * 1024 ** 3:
                        raise UpdateError("更新镜像解压后过大。")
            if (not isinstance(manifest, list) or len(manifest) != 1
                    or not isinstance(manifest[0], dict)
                    or manifest[0].get("RepoTags") != [release["image"]]):
                raise UpdateError("更新归档不是本版本的单个 HomeStorage 镜像。")
            config_name = manifest[0].get("Config")
            layers = manifest[0].get("Layers")
            if (not isinstance(config_name, str) or config_name not in members
                    or members[config_name] > 1024 * 1024 or not isinstance(layers, list)
                    or not layers or any(not isinstance(layer, str) or layer not in members for layer in layers)):
                raise UpdateError("更新镜像配置或层信息不完整。")
            with tarfile.open(destination, "r:gz") as archive:
                stream = archive.extractfile(config_name)
                if stream is None:
                    raise UpdateError("更新镜像配置不完整。")
                configuration = json.loads(stream.read(1024 * 1024 + 1))
            labels = (configuration.get("config") or {}).get("Labels") or {}
            if (configuration.get("architecture") != arch or configuration.get("os") != "linux"
                    or labels.get(VERSION) != release["version"]
                    or labels.get(REVISION) != release["revision"]):
                raise UpdateError("归档镜像架构、版本或源码标识不符。")
        except UpdateError:
            raise
        except (OSError, EOFError, ValueError, TypeError, AttributeError, tarfile.TarError) as exc:
            raise UpdateError("更新镜像归档损坏，已拒绝加载。") from exc

    def _health(self, container_id, project, mount):
        deadline = time.monotonic() + self.HEALTH_TIMEOUT
        while True:
            container = self.docker.inspect(container_id)
            self._validate_target(container, project, mount)
            state = container.get("State") or {}
            if not state.get("Running") or state.get("Restarting"):
                raise UpdateError("新容器未稳定运行。")
            health = (state.get("Health") or {}).get("Status")
            if health == "healthy":
                return container
            if health == "unhealthy" or health is None or time.monotonic() >= deadline:
                raise UpdateError("新容器未通过健康检查。")
            time.sleep(self.HEALTH_POLL)

    def install(self, version, automatic=False):
        if not self._lock.acquire(blocking=False):
            raise UpdateError("正在执行另一项更新任务，请稍后重试。")
        archive = None
        journal = None
        try:
            state = self.store.get()
            if state.get("journal"):
                raise UpdateError("上次更新仍需恢复，请先恢复更新服务。")
            if automatic and not state.get("auto_update", False):
                self.store.update(phase="idle", message="自动更新已关闭，本次自动安装已取消。")
                return self.status()
            release = state.get("release_cache") or {}
            self._validate_release(release)
            if not isinstance(version, str) or version != release["version"]:
                raise UpdateError("只能安装刚刚检查到的稳定版本，请重新检查更新。")
            old, project, mount = self._discover()
            current, revision = self._current(old)
            if _version(version) <= _version(current):
                raise UpdateError("不能重复安装当前版本或降级。")
            arch = self._arch()
            if release.get("arch") != arch:
                raise UpdateError("发布架构已发生变化，请重新检查更新。")
            attempt = uuid.uuid4().hex
            archive = self.state_dir / f"download-{attempt}.tar.gz"
            self.store.update(phase="downloading", message="正在下载并校验更新镜像。",
                              current_version=current, current_revision=revision,
                              progress={"downloaded_bytes": 0, "total_bytes": release.get("size", 0)})
            last_report = [0.0]

            def progress(downloaded, total):
                if time.monotonic() - last_report[0] >= 0.5 or downloaded == total:
                    self.store.update(progress={"downloaded_bytes": downloaded, "total_bytes": total})
                    last_report[0] = time.monotonic()

            download_release = {key: value for key, value in release.items() if key != "arch"}
            self.releases.download(download_release, archive, progress)
            self._verify_archive(archive, release, arch)
            self.store.update(phase="loading", message="正在加载已验证的 Docker 镜像。")
            self.docker.load_image(archive)
            image = self.docker.inspect_image(release["image"])
            self._verify_image(image, release, arch)
            # Downloads may take minutes. Re-discover and verify the exact old
            # container immediately before stopping it.
            refreshed, new_project, new_mount = self._discover()
            if refreshed["Id"] != old["Id"] or new_project != project or new_mount != mount:
                raise UpdateError("下载期间容器配置发生变化，请重新检查更新。")
            old = refreshed
            if not ((old.get("State") or {}).get("Health") or {}).get("Status"):
                raise UpdateError("原容器没有健康检查，无法保证失败后的安全恢复，请先更新部署配置。")
            if automatic and not self.store.get().get("auto_update", False):
                self.store.update(phase="idle", message="自动更新已关闭，本次自动安装已取消。")
                return self.status()
            old_name = str(old.get("Name") or "").lstrip("/")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,200}", old_name):
                raise UpdateError("原容器名称无效，已拒绝更新。")
            replacement = self._replacement_config(old, image, release, attempt)
            healthcheck = replacement.get("Healthcheck") or (image.get("Config") or {}).get("Healthcheck") or {}
            if not isinstance(healthcheck.get("Test"), list) or healthcheck["Test"][:1] not in [["CMD"], ["CMD-SHELL"]]:
                raise UpdateError("更新镜像缺少有效健康检查，已拒绝替换容器。")
            journal = {"schema": 1, "attempt": attempt, "project": project, "mount": mount,
                       "old_id": old["Id"], "old_name": old_name,
                       "saved_name": f"{old_name}-pre-update-{attempt[:8]}",
                       "new_id": None, "backup_file": f"backup-{attempt}.tar.gz",
                       "backup_complete": False, "version": version,
                       "revision": release["revision"], "old_version": current,
                       "old_revision": revision, "started_at": _now()}
            journal = self._journal(journal, "stopping", "stopping", "正在暂停写入并停止原容器。")
            self._maintenance(True)
            self.docker.stop(old["Id"])
            journal = self._journal(journal, "backing_up", "backing_up", "正在备份全部数据和照片。")
            backup_metadata = self._backup(self._backup_path(journal))
            journal = self._journal(journal, "renaming", "applying", "备份完成，正在替换容器。",
                                    backup_complete=True, backup_at=_now(), **backup_metadata)
            self.store.update(last_backup_at=journal["backup_at"])
            self.docker.rename(old["Id"], journal["saved_name"])
            journal = self._journal(journal, "creating")
            new_id = self.docker.create(replacement, name=old_name)
            journal = self._journal(journal, "starting", new_id=new_id)
            self.docker.start(new_id)
            journal = self._journal(journal, "verifying", "verifying", "正在验证新版本健康状态。")
            self._health(new_id, project, mount)
            journal = self._journal(journal, "committed")
            self._finish(journal)
            return self.status()
        except Exception as exc:
            if journal is not None:
                # Re-read to include a container ID persisted just before a
                # failing daemon call, and resolve ambiguous create responses.
                try:
                    recorded = self.store.get().get("journal") or journal
                    if recorded.get("stage") == "committed":
                        # A previously healthy container can fail immediately
                        # afterwards. Do not infer success from the stage alone,
                        # and do not clear maintenance without fresh validation.
                        self._maintenance(True)
                        self.store.update(phase="failed", message="新版本提交尚未完成，写入仍暂停；重启更新服务将重新验证并完成恢复。")
                    else:
                        self._rollback(recorded)
                except Exception as recovery_error:
                    self.store.update(phase="failed", message="更新失败且自动恢复尚未完成；已保留备份并暂停写入，请恢复更新服务后重试。")
                    raise UpdateError("更新失败且自动恢复尚未完成，数据写入仍暂停。", 503) from recovery_error
            else:
                self.store.update(phase="failed", message=(str(exc) if isinstance(exc, (UpdateError, ReleaseError))
                                                          else "更新未能完成；原容器及数据保持原状。"))
            if isinstance(exc, UpdateError):
                raise
            raise UpdateError("更新未能完成，已尝试安全恢复原版本。", 502) from exc
        finally:
            if archive is not None:
                archive.unlink(missing_ok=True)
            self._lock.release()

    def _recovery_targets(self, journal):
        if journal.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{32}", str(journal.get("attempt", ""))):
            raise UpdateError("恢复日志无效，已暂停恢复。")
        project, mount = self._identity()
        if project != journal.get("project") or mount != journal.get("mount"):
            raise UpdateError("恢复服务的项目或数据挂载已经变化，已暂停恢复。")
        old = self.docker.inspect(journal["old_id"])
        self._validate_target(old, project, mount)
        matches = self.docker.list_containers(all=True, filters={"label": [
            f"{PROJECT}={project}", f"{SERVICE}={self.target_service}", f"{ROLE}=app",
            f"{ATTEMPT}={journal['attempt']}",
        ]})
        found = []
        for match in matches:
            target = self.docker.inspect(match["Id"])
            self._validate_target(target, project, mount)
            if self._labels(target).get(ATTEMPT) != journal["attempt"] or target["Id"] == old["Id"]:
                raise UpdateError("恢复目标容器标识不一致，已暂停恢复。")
            found.append(target)
        if len(found) > 1:
            raise UpdateError("发现多个更新替代容器，已暂停恢复。")
        new = found[0] if found else None
        if new is not None and journal.get("new_id") not in {None, new["Id"]}:
            raise UpdateError("恢复目标容器已发生变化，已暂停恢复。")
        return old, new, project, mount

    def _rollback(self, journal):
        self._maintenance(True)
        old, new, project, mount = self._recovery_targets(journal)
        if new is not None:
            journal = self._journal(journal, "removing_new", "applying", "正在恢复原版本和备份。", new_id=new["Id"])
            if (new.get("State") or {}).get("Running"):
                self.docker.stop(new["Id"])
            self.docker.remove(new["Id"], force=False)
        # Always stop old before restoring, including recovery after a process
        # crash during the prior rollback's start/health-check phase.
        if (old.get("State") or {}).get("Running"):
            self.docker.stop(old["Id"])
        if journal.get("backup_complete"):
            journal = self._journal(journal, "restoring", "applying", "正在恢复更新前的数据备份。")
            self._restore(journal)
        journal = self._journal(journal, "restarting_old")
        if str(old.get("Name") or "").lstrip("/") != journal["old_name"]:
            self.docker.rename(old["Id"], journal["old_name"])
        self.docker.start(old["Id"])
        self._health(old["Id"], project, mount)
        self.store.update(journal=None, phase="rolled_back", current_version=journal["old_version"],
                          current_revision=journal.get("old_revision", ""),
                          message="更新失败，原版本和更新前的数据已恢复。")
        self._maintenance(False)
        # Failed attempts are not successful retention backups. Delete only
        # after the original application and data have been verified healthy.
        self._backup_path(journal).unlink(missing_ok=True)

    def _finish(self, journal):
        # _finish can be re-entered after an updater restart or failed cleanup.
        project, mount = self._identity()
        if project != journal.get("project") or mount != journal.get("mount"):
            raise UpdateError("提交更新时项目或数据卷发生变化。")
        new_id = journal.get("new_id")
        if not isinstance(new_id, str) or not new_id:
            raise UpdateError("提交更新时缺少新容器标识。")
        new = self._health(new_id, project, mount)
        if self._labels(new).get(ATTEMPT) != journal["attempt"]:
            raise UpdateError("提交更新时容器标识不一致。")
        # Listing, rather than treating every inspect failure as 'not found',
        # prevents a daemon outage from being mistaken for successful cleanup.
        all_ids = {entry["Id"] for entry in self.docker.list_containers(all=True)}
        if journal["old_id"] in all_ids:
            old = self.docker.inspect(journal["old_id"])
            self._validate_target(old, project, mount)
            if (old.get("State") or {}).get("Running"):
                raise UpdateError("原容器仍在运行，不能提交更新。")
            self.docker.remove(old["Id"], force=False)
        state = self.store.get()
        backups = list(state.get("backups") or [])
        if not any(entry.get("file") == journal["backup_file"] for entry in backups):
            backups.append({"file": journal["backup_file"], "at": journal.get("backup_at"),
                            "version": journal["old_version"]})
        expired, backups = backups[:-3], backups[-3:]
        # Save the completed journal until maintenance has been cleared. Recovery
        # can then repeat cleanup if power is lost at any point in completion.
        self.store.update(backups=backups, phase="completed", current_version=journal["version"],
                          current_revision=journal["revision"],
                          message="更新完成，新版本已通过健康检查。")
        self._maintenance(False)
        self.store.update(journal=None)
        for entry in expired:
            filename = entry.get("file", "")
            if re.fullmatch(r"backup-[0-9a-f]{32}\.tar\.gz", filename):
                (self.backup_dir / filename).unlink(missing_ok=True)

    def recover(self):
        if not self._lock.acquire(blocking=False):
            raise UpdateError("正在执行另一项更新任务。")
        try:
            state = self.store.get()
            journal = state.get("journal")
            if not journal:
                if (self.state_dir / "maintenance.flag").exists():
                    # A crash after recording a completed rollback can leave
                    # only the flag. Clear it only after validating the app.
                    target, project, mount = self._discover()
                    self._health(target["Id"], project, mount)
                    self._maintenance(False)
                if state.get("phase") in {"queued", "checking", "downloading", "loading"}:
                    self.store.update(phase="idle", message="上次任务在替换容器前中断，原版本未受影响。")
                for archive in self.state_dir.glob("download-*.tar.gz"):
                    if re.fullmatch(r"download-[0-9a-f]{32}\.tar\.gz", archive.name):
                        archive.unlink(missing_ok=True)
                return self.status()
            self._maintenance(True)
            if journal.get("stage") == "committed":
                self._finish(journal)
            else:
                self._rollback(journal)
            return self.status()
        except Exception as exc:
            self.store.update(phase="failed", message="上次更新尚未安全恢复，已保留备份并暂停写入；请检查更新服务日志和数据卷配置。")
            raise UpdateError("上次更新尚未安全恢复，数据写入仍暂停。", 503) from exc
        finally:
            self._lock.release()
