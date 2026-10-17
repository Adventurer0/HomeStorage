"""Authenticated, fixed-route proxy to the separate NAS update service.

The web application does not inspect Docker, download releases, or execute update
commands. The updater owns those operations and independently validates them.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .version import VERSION_PATTERN
from updater.proxy import ProxySettingsInput, normalize_proxy_url


PHASES = frozenset({
    "idle", "checking", "queued", "downloading", "loading", "stopping", "backing_up",
    "applying", "verifying", "completed", "failed", "rolled_back",
})
INTERNAL_ROUTES = {
    "/v1/status": "GET", "/v1/check": "POST", "/v1/settings": "POST", "/v1/install": "POST",
}
MAX_STATUS_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
DEPLOYMENT_MESSAGE = (
    "尚未启用更新服务。请按新版部署说明添加 updater 服务及更新状态数据卷，然后重新部署一次。"
)
SERVICE_MESSAGE = "更新服务暂时不可用，请检查 NAS 的 updater 容器后重试。"


class UpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckInput(UpdateInput):
    pass


class SettingsInput(ProxySettingsInput):
    pass


class InstallInput(UpdateInput):
    confirmed: StrictBool
    version: str = Field(min_length=5, max_length=64)

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("需要明确确认更新")
        return value

    @field_validator("version")
    @classmethod
    def stable_version(cls, value):
        if not VERSION_PATTERN.fullmatch(value):
            raise ValueError("请选择已检查的正式版本")
        return value


def update_state_dir() -> Path:
    return Path(os.environ.get("UPDATE_STATE_DIR", "") or "/run/homestorage-update").expanduser()


def maintenance_active(state_dir: Path) -> bool:
    try:
        return (state_dir / "maintenance.flag").is_file()
    except OSError:
        # An inaccessible update state must not permit unprotected data writes.
        return True


def validated_service_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not valid_port or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
            or "\\" in value
        ):
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return None


def read_token(state_dir: Path) -> str | None:
    try:
        descriptor = os.open(state_dir / "token", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r", encoding="ascii") as token_file:
            if not stat.S_ISREG(os.fstat(token_file.fileno()).st_mode):
                return None
            value = token_file.read(256).strip()
    except (OSError, UnicodeError):
        return None
    return value if re.fullmatch(r"[a-zA-Z0-9_-]{32,128}", value) else None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _integer(value, maximum):
    return value if type(value) is int and 0 <= value <= maximum else 0


def _release_url(value):
    if not isinstance(value, str) or len(value) > 1024:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https" and parsed.netloc == "github.com"
            and re.fullmatch(r"/Adventurer0/HomeStorage/releases/tag/v[0-9]+\.[0-9]+\.[0-9]+", parsed.path)
            and not parsed.query and not parsed.fragment
        ):
            return value
    except ValueError:
        pass
    return None


def unavailable_status(current_version: str, current_revision: str, message: str):
    return {
        "available": False,
        "current_version": current_version,
        "current_revision": current_revision,
        "auto_update": False,
        "update_available": False,
        "latest_version": None,
        "release_notes": "",
        "release_url": None,
        "last_checked_at": None,
        "last_backup_at": None,
        "next_check_at": None,
        "phase": "idle",
        "message": message,
        "backup_count": 0,
        "progress": {"downloaded_bytes": 0, "total_bytes": 0},
    }


def public_status(payload, current_version: str, current_revision: str, token: str):
    if not isinstance(payload, dict):
        raise ValueError("Invalid updater status")
    result = unavailable_status(current_version, current_revision, SERVICE_MESSAGE)
    # Preserve compatibility with older updaters: absence of these fields tells
    # the browser that the helper itself must be upgraded once.
    if all(key in payload for key in ("proxy_url", "proxy_username", "proxy_password_set", "proxy_source")):
        try:
            proxy_url = normalize_proxy_url(payload["proxy_url"])
            username = payload["proxy_username"]
            if not isinstance(username, str) or len(username) > 255 or any(ord(character) < 32 or ord(character) == 127 for character in username):
                raise ValueError
            if type(payload["proxy_password_set"]) is not bool or payload["proxy_source"] not in {"environment", "web"}:
                raise ValueError
            result.update(proxy_url=proxy_url, proxy_username=username, proxy_password_set=payload["proxy_password_set"], proxy_source=payload["proxy_source"])
        except (ValueError, TypeError):
            pass
    result["available"] = payload.get("available") is True
    result["auto_update"] = payload.get("auto_update") is True
    result["update_available"] = payload.get("update_available") is True
    latest = payload.get("latest_version")
    result["latest_version"] = (
        latest if isinstance(latest, str) and len(latest) <= 64 and VERSION_PATTERN.fullmatch(latest) else None
    )
    for key, limit in (("release_notes", 16_000), ("message", 2000)):
        value = payload.get(key)
        if isinstance(value, str):
            result[key] = value.replace(token, "[已隐藏]")[:limit]
    result["release_url"] = _release_url(payload.get("release_url"))
    for key in ("last_checked_at", "last_backup_at", "next_check_at"):
        result[key] = _timestamp(payload.get(key))
    if isinstance(payload.get("phase"), str) and payload["phase"] in PHASES:
        result["phase"] = payload["phase"]
    result["backup_count"] = _integer(payload.get("backup_count"), 1000)
    progress = payload.get("progress")
    if isinstance(progress, dict):
        result["progress"] = {
            "downloaded_bytes": _integer(progress.get("downloaded_bytes"), MAX_ARCHIVE_BYTES),
            "total_bytes": _integer(progress.get("total_bytes"), MAX_ARCHIVE_BYTES),
        }
    return result


async def call_updater(service_url: str, token: str, method: str, route: str, payload=None):
    if INTERNAL_ROUTES.get(route) != method:
        raise ValueError("Unsupported updater route")
    # A fixed base URL and fixed routes prevent the browser from selecting hosts,
    # paths, headers, credentials, or commands. No redirects or ambient proxies.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(6.0, connect=2.0), follow_redirects=False, trust_env=False,
    ) as client:
        async with client.stream(
            method, service_url + route, json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        ) as response:
            if response.status_code == 409:
                raise HTTPException(409, "当前任务正在执行，或所选版本已发生变化。请刷新更新状态后重试。")
            if response.status_code == 400:
                raise HTTPException(400, "更新请求无效，请检查最新版本后重试。")
            if response.status_code not in {200, 202}:
                raise HTTPException(503, SERVICE_MESSAGE)
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_STATUS_BYTES:
                    raise HTTPException(503, SERVICE_MESSAGE)
            return json.loads(chunks)


def build_updates_router(*, current_version: str, current_revision: str, password_required: bool, state_dir: Path):
    router = APIRouter(prefix="/api/settings/updates", tags=["settings"])
    raw_service_url = os.environ.get("UPDATE_SERVICE_URL", "")
    service_url = validated_service_url(raw_service_url)

    async def proxy(method, route, payload=None, *, optional=False):
        message = DEPLOYMENT_MESSAGE if not raw_service_url else SERVICE_MESSAGE
        if not password_required:
            message = "请先在 NAS Compose 中设置 HOME_STORAGE_PASSWORD 登录密码，再启用网页更新。"
            if not optional:
                raise HTTPException(403, message)
        token = read_token(state_dir) if service_url and password_required else None
        if not token:
            if optional:
                return unavailable_status(current_version, current_revision, message)
            raise HTTPException(503, message)
        try:
            payload = await call_updater(service_url, token, method, route, payload)
            return public_status(payload, current_version, current_revision, token)
        except HTTPException:
            if not optional:
                raise
        except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, TypeError, RecursionError):
            if not optional:
                raise HTTPException(503, SERVICE_MESSAGE) from None
        return unavailable_status(current_version, current_revision, SERVICE_MESSAGE)

    @router.get("")
    async def status():
        return await proxy("GET", "/v1/status", optional=True)

    @router.post("/check")
    async def check(_body: CheckInput):
        return await proxy("POST", "/v1/check", {})

    @router.patch("")
    async def settings(body: SettingsInput):
        return await proxy("POST", "/v1/settings", body.model_dump(exclude_unset=True))

    @router.post("/install")
    async def install(body: InstallInput):
        return await proxy("POST", "/v1/install", body.model_dump())

    return router
