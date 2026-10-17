"""Authenticated internal service; the app never receives a Docker socket."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
import hmac
import logging
import os
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from .releases import ReleaseClient, ReleaseError, REVISION_RE, valid_version
from .state import StateStore, ensure_token
from .proxy import PROXY_FIELDS, ProxySettingsInput, merge_proxy_configuration, proxy_configuration, public_proxy_status


logger = logging.getLogger("homestorage.updater")
PHASES = {"idle", "checking", "queued", "downloading", "loading", "stopping", "backing_up", "applying", "verifying", "completed", "failed", "rolled_back"}
STATUS_FIELDS = {"available", "current_version", "current_revision", "auto_update", "update_available", "latest_version", "release_notes", "release_url", "last_checked_at", "last_backup_at", "next_check_at", "phase", "message", "backup_count", "progress"}


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SettingsRequest(ProxySettingsInput):
    pass


class InstallRequest(EmptyRequest):
    confirmed: StrictBool
    version: StrictStr = Field(min_length=5, max_length=48)

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if value is not True:
            raise ValueError("请明确确认更新")
        return value

    @field_validator("version")
    @classmethod
    def require_version(cls, value):
        if not valid_version(value):
            raise ValueError("版本编号无效")
        return value


def safe_status(engine, store):
    snapshot = engine.status()
    if not isinstance(snapshot, dict):
        raise RuntimeError("更新状态无效")
    result = {key: snapshot.get(key) for key in STATUS_FIELDS}
    state = store.get()
    result.update(public_proxy_status(state))
    result["auto_update"] = state["auto_update"]
    result["next_check_at"] = state.get("next_check_at")
    result["available"] = snapshot.get("available") is True
    result["update_available"] = snapshot.get("update_available") is True
    result["phase"] = snapshot.get("phase") if snapshot.get("phase") in PHASES else "idle"
    result["release_notes"] = str(snapshot.get("release_notes") or "")[:12000]
    result["message"] = str(snapshot.get("message") or "")[:1000]
    if not valid_version(result["current_version"]):
        result["current_version"] = "unknown"
    if not isinstance(result["current_revision"], str) or not REVISION_RE.fullmatch(result["current_revision"]):
        result["current_revision"] = ""
    if not valid_version(result["latest_version"]):
        result["latest_version"] = None
        result["release_url"] = None
    elif result["release_url"] != "https://github.com/Adventurer0/HomeStorage/releases/tag/v" + result["latest_version"]:
        result["release_url"] = None
    result["backup_count"] = snapshot.get("backup_count") if type(snapshot.get("backup_count")) is int and snapshot["backup_count"] >= 0 else 0
    progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
    result["progress"] = {key: progress.get(key) if type(progress.get(key)) is int and progress[key] >= 0 else 0 for key in ("downloaded_bytes", "total_bytes")}
    return result


def next_daily_check(now, timezone):
    local = now.astimezone(timezone)
    next_check = datetime.combine(local.date(), time(3), timezone)
    if next_check <= local:
        next_check = datetime.combine(local.date() + timedelta(days=1), time(3), timezone)
    return next_check


class JobController:
    def __init__(self, engine, store):
        self.engine = engine
        self.store = store
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="homestorage-update")
        self._future = None
        self._owned_release_client = None

    def start(self, operation, *, version=None, automatic=False):
        if not self._lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="更新任务正在执行，请等待完成")
        try:
            self.store.update(phase="queued", message="更新检查已排队。" if operation == "check" else "更新已排队；正在准备下载和备份。")
            self._future = self._executor.submit(self._run, operation, version, automatic)
        except BaseException:
            self._lock.release()
            raise

    def _run(self, operation, version, automatic):
        try:
            if operation == "check":
                self.engine.check()
                if automatic and self.store.get()["auto_update"]:
                    snapshot = self.engine.status()
                    latest = snapshot.get("latest_version")
                    if snapshot.get("update_available") is True and valid_version(latest):
                        self.engine.install(latest, automatic=True)
            else:
                self.engine.install(version, automatic=automatic)
        except Exception as exc:
            # Only our validated release messages are safe to expose. Never expose
            # Docker inspect, HTTP response bodies, environment, or tracebacks.
            message = str(exc) if isinstance(exc, ReleaseError) else "更新未完成，请检查 NAS 网络或更新服务状态后重试。"
            # The engine may already have verified a successful rollback (or
            # persisted a specific failure). Keep that evidence and guidance.
            if self.store.get().get("phase") not in ("rolled_back", "completed", "failed"):
                self.store.update(phase="failed", message=message)
            logger.error("Update task failed (%s)", type(exc).__name__)
        finally:
            self._lock.release()

    def set_settings(self, updates):
        if not PROXY_FIELDS.intersection(updates):
            self.store.update(auto_update=updates["auto_update"])
            return
        if not self._lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="更新任务正在执行，请等待完成后修改代理")
        prepared = None
        try:
            configuration = merge_proxy_configuration(self.store.get(), updates)
            previous = self.engine.releases
            try:
                prepared = ReleaseClient(proxy_configuration=configuration, github_token=getattr(previous, "github_token", None))
            except Exception:
                raise HTTPException(status_code=400, detail="代理设置无效，或当前更新服务不支持该代理类型") from None
            fields = {"proxy_configuration": configuration}
            if "auto_update" in updates:
                fields["auto_update"] = updates["auto_update"]
            try:
                self.store.update(**fields)
            except Exception:
                raise HTTPException(status_code=503, detail="代理设置未能保存，原设置仍然有效") from None
            # No update job can enter until the durable state and active client
            # have both been switched. Failed preparation/persistence leaves the
            # previous client and stored settings intact.
            self.engine.releases = prepared
            self._owned_release_client = prepared
            prepared = None
            try:
                if hasattr(previous, "close"):
                    previous.close()
            except Exception:
                logger.warning("Previous release connection could not be closed")
        finally:
            if prepared is not None:
                try:
                    prepared.close()
                except Exception:
                    logger.warning("Prepared release connection could not be closed")
            self._lock.release()

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=False)
        if self._owned_release_client is not None:
            self._owned_release_client.close()


async def scheduler(controller, store, timezone):
    next_check = next_daily_check(datetime.now(timezone), timezone)
    store.update(next_check_at=next_check.isoformat())
    while True:
        remaining = (next_check - datetime.now(timezone)).total_seconds()
        if remaining > 0:
            await asyncio.sleep(min(remaining, 60))
            continue
        try:
            controller.start("check", automatic=True)
        except HTTPException:
            # An already running manual job must never be interrupted.
            pass
        next_check = next_daily_check(datetime.now(timezone), timezone)
        store.update(next_check_at=next_check.isoformat())


def create_app(*, engine=None, store=None, state_dir=None, data_dir=None, token=None, timezone=None, schedule_enabled=True):
    """Dependency injection is explicit; module import does not access Docker."""
    configured_state_dir = Path(state_dir or os.environ.get("UPDATE_STATE_DIR", "/state"))
    configured_data_dir = Path(data_dir or os.environ.get("UPDATE_DATA_DIR", "/app-data"))
    configured_timezone = timezone or os.environ.get("APP_TIMEZONE", "Asia/Shanghai")

    @asynccontextmanager
    async def lifespan(application):
        nonlocal engine, store
        store = store or getattr(engine, "store", None) or StateStore(configured_state_dir)
        credential = token if token is not None else ensure_token(configured_state_dir, int(os.environ.get("APP_UID", "1000")), int(os.environ.get("APP_GID", "1000")))
        if not isinstance(credential, str) or not 32 <= len(credential) <= 128 or any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in credential):
            raise RuntimeError("更新服务令牌无效")
        release_client = None
        docker = None
        if engine is None:
            from .docker import DockerClient
            from .engine import UpdateEngine
            docker = DockerClient("/var/run/docker.sock")
            release_client = ReleaseClient(proxy_configuration=proxy_configuration(store.get()))
            engine = UpdateEngine(docker, store, release_client, self_id=os.environ.get("HOSTNAME", ""), data_dir=configured_data_dir, state_dir=configured_state_dir)
        application.state.token = credential
        application.state.engine = engine
        application.state.store = store
        try:
            await asyncio.to_thread(engine.recover)
        except Exception as exc:
            store.update(phase="failed", message="上次更新尚未恢复，请保留备份并检查更新服务状态。")
            logger.error("Update recovery failed (%s)", type(exc).__name__)
        controller = JobController(engine, store)
        application.state.controller = controller
        scheduled = asyncio.create_task(scheduler(controller, store, ZoneInfo(configured_timezone))) if schedule_enabled else None
        try:
            yield
        finally:
            if scheduled:
                scheduled.cancel()
                try:
                    await scheduled
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(controller.close)
            if release_client is not None and release_client is getattr(engine, "releases", None):
                release_client.close()
            if docker and hasattr(docker, "close"):
                docker.close()

    application = FastAPI(title="HomeStorage internal updater", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # Pydantic's default response includes the submitted value, which may be
        # a proxy password. Neither API may echo credentials on validation errors.
        return JSONResponse({"detail": "参数格式或取值不正确，请检查填写内容"}, status_code=422)

    async def authenticate(authorization: str | None = Header(default=None)):
        if not authorization or len(authorization) > 256 or not authorization.isascii() or not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], application.state.token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="更新服务认证失败", headers={"WWW-Authenticate": "Bearer"})

    async def current_status():
        try:
            return await asyncio.to_thread(safe_status, application.state.engine, application.state.store)
        except Exception:
            raise HTTPException(status_code=503, detail="暂时无法读取更新状态，请检查更新服务") from None

    @application.get("/v1/status", dependencies=[Depends(authenticate)])
    async def status_endpoint():
        return await current_status()

    @application.post("/v1/check", status_code=202, dependencies=[Depends(authenticate)])
    async def check_endpoint(payload: EmptyRequest):
        application.state.controller.start("check")
        return await current_status()

    @application.post("/v1/settings", dependencies=[Depends(authenticate)])
    async def settings_endpoint(payload: SettingsRequest):
        await asyncio.to_thread(application.state.controller.set_settings, payload.model_dump(exclude_unset=True))
        return await current_status()

    @application.post("/v1/install", status_code=202, dependencies=[Depends(authenticate)])
    async def install_endpoint(payload: InstallRequest):
        snapshot = await current_status()
        if snapshot.get("available") is not True:
            raise HTTPException(status_code=409, detail="更新服务尚未找到可更新的 HomeStorage 容器")
        if snapshot.get("latest_version") != payload.version or snapshot.get("update_available") is not True:
            raise HTTPException(status_code=409, detail="请选择刚刚检查到的新版本；不允许降级或重复安装")
        current = snapshot.get("current_version")
        if valid_version(current) and tuple(map(int, payload.version.split("."))) <= tuple(map(int, current.split("."))):
            raise HTTPException(status_code=409, detail="不允许降级或重复安装")
        application.state.controller.start("install", version=payload.version)
        return await current_status()

    return application


app = create_app()
