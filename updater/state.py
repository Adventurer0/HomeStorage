"""Small durable state store for the isolated HomeStorage updater."""

from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
import stat
import threading


DEFAULT_STATE = {
    "schema_version": 1,
    "auto_update": False,
    "phase": "idle",
    "message": "自动更新默认关闭，可先检查新版本。",
    "last_checked_at": None,
    "last_backup_at": None,
    "next_check_at": None,
    "backup_count": 0,
    "progress": {"downloaded_bytes": 0, "total_bytes": 0},
}


class StateStore:
    def __init__(self, state_dir):
        self.directory = Path(state_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        if self.directory.is_symlink():
            raise RuntimeError("更新状态目录不能是符号链接")
        os.chmod(self.directory, 0o755)
        self.path = self.directory / "state.json"
        self.backups_dir = self.directory / "backups"
        self.backups_dir.mkdir(mode=0o700, exist_ok=True)
        if self.backups_dir.is_symlink():
            raise RuntimeError("备份目录不能是符号链接")
        os.chmod(self.backups_dir, 0o700)
        self._lock = threading.RLock()
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > 2 * 1024 * 1024:
                raise RuntimeError("更新状态文件无效")
            try:
                with self.path.open("r", encoding="utf-8") as stream:
                    existing = json.load(stream)
            except (OSError, ValueError) as exc:
                raise RuntimeError("更新状态文件无法读取；请保留文件以便恢复") from exc
            if not isinstance(existing, dict) or type(existing.get("auto_update", False)) is not bool:
                raise RuntimeError("更新状态格式无效")
            if type(existing.get("schema_version", 1)) is not int or existing.get("schema_version", 1) != 1:
                raise RuntimeError("更新状态版本不受支持")
            self._state = {**deepcopy(DEFAULT_STATE), **existing}
            os.chmod(self.path, 0o600)
        else:
            self._state = deepcopy(DEFAULT_STATE)
            self._write(self._state)

    def get(self):
        with self._lock:
            return deepcopy(self._state)

    def update(self, **fields):
        with self._lock:
            next_state = {**self._state, **deepcopy(fields)}
            if type(next_state.get("auto_update")) is not bool:
                raise ValueError("自动更新设置必须是布尔值")
            self._write(next_state)
            self._state = next_state
            return deepcopy(next_state)

    def _write(self, state):
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("更新状态过大")
        temporary = self.directory / (".state-" + secrets.token_hex(12))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
            sync_directory(self.directory)
        finally:
            temporary.unlink(missing_ok=True)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_token(state_dir, uid=1000, gid=1000):
    """Create once; the web app can read this credential, never modify it."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    path = directory / "token"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o640)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RuntimeError("更新服务令牌文件无效")
            encoded = stream.read(257)
        try:
            token = encoded.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise RuntimeError("更新服务令牌文件无效") from exc
        if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
            raise RuntimeError("更新服务令牌文件无效")
    else:
        token = secrets.token_hex(32)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((token + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(directory)
    os.chmod(path, 0o640, follow_symlinks=False)
    if os.geteuid() == 0:
        os.chown(path, uid, gid, follow_symlinks=False)
    return token
