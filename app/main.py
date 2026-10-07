"""A small, local-first inventory API with audited stock changes."""

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import warnings
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone as utc_timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
import qrcode.image.svg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from .db import connect, initialize, transaction


CATEGORIES = [
    "日常用品", "电子设备", "摄影器材", "工具耗材", "骑行用品", "健身用品",
    "商品库存", "食品", "药品", "其他",
]
ACTIVE = {"in_stock", "in_use", "lent"}
COOKIE_NAME = "homestorage_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"
LocationType = Literal["home", "room", "shelf", "drawer", "box", "other"]
StockAction = Literal["move", "take_out", "return", "lend", "consume", "restock", "discard", "lost"]
Quantity = Annotated[StrictInt, Field(ge=0, le=1_000_000_000)]
WarningDays = Annotated[StrictInt, Field(ge=0, le=3650)]


def now() -> str:
    return datetime.now(utc_timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginInput(InputModel):
    password: str = Field(max_length=4096)

    # Password whitespace is significant even though descriptive fields are trimmed.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class LocationCreate(InputModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(default="", max_length=80)
    type: LocationType = "other"
    parent_id: Annotated[StrictInt, Field(gt=0)] | None = None


class LocationPatch(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=80)
    type: LocationType | None = None
    parent_id: Annotated[StrictInt, Field(gt=0)] | None = None


class ItemFields(InputModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = "其他"
    unit: str = Field(default="件", min_length=1, max_length=30)
    image_url: str | None = Field(default=None, max_length=2048)
    brand: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    tags: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=10000)
    stored_date: date | None = None
    purchase_date: date | None = None
    purchase_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expires_on: date | None = None
    warning_days: WarningDays = 7

    @field_validator("category")
    @classmethod
    def known_category(cls, value):
        if value not in CATEGORIES:
            raise ValueError("请选择有效分类")
        return value

    @field_validator("image_url")
    @classmethod
    def safe_image_url(cls, value):
        if not value:
            return None
        if re.fullmatch(r"/uploads/[a-zA-Z0-9_.-]+", value):
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("图片地址必须是上传路径或 HTTP(S) 地址")
        return value


class ItemCreate(ItemFields):
    location_id: Annotated[StrictInt, Field(gt=0)]
    quantity: Quantity = 1


class ItemPatch(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=30)
    image_url: str | None = Field(default=None, max_length=2048)
    brand: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    tags: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=10000)
    stored_date: date | None = None
    purchase_date: date | None = None
    purchase_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expires_on: date | None = None
    warning_days: WarningDays | None = None


class ActionInput(InputModel):
    action: StockAction
    location_id: Annotated[StrictInt, Field(gt=0)] | None = None
    quantity: Annotated[StrictInt, Field(gt=0, le=1_000_000_000)] | None = None
    note: str = Field(default="", max_length=2000)


class SearchInput(InputModel):
    query: str = Field(min_length=1, max_length=200)


def fail(message: str, status=400):
    raise HTTPException(status, message)


def require_row(connection, table, row_id):
    row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        fail("位置不存在" if table == "locations" else "物品不存在", 404)
    return row


def location_tree(connection):
    rows = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM locations ORDER BY id")}

    def path(location_id):
        row = rows[location_id]
        return path(row["parent_id"]) + " / " + row["name"] if row["parent_id"] else row["name"]

    for location_id, row in rows.items():
        row["path"] = path(location_id)
    return rows


def descendants(tree, location_id):
    if location_id not in tree:
        fail("位置不存在", 404)
    result = {location_id}
    pending = [location_id]
    while pending:
        parent_id = pending.pop()
        children = [key for key, row in tree.items() if row["parent_id"] == parent_id]
        result.update(children)
        pending.extend(children)
    return result


def serialize_location(connection, location_id):
    tree = location_tree(connection)
    ids = descendants(tree, location_id)
    count = sum(
        1 for row in connection.execute("SELECT location_id, status FROM items")
        if row["location_id"] in ids and row["status"] in ACTIVE
    )
    return {**tree[location_id], "item_count": count}


def serialize_item(row, tree, today):
    item = dict(row)
    item["location_path"] = tree[item["location_id"]]["path"]
    remaining = (date.fromisoformat(item["expires_on"]) - today).days if item["expires_on"] else None
    item["remaining_days"] = remaining
    item["expiry_status"] = (
        "none" if remaining is None else "expired" if remaining < 0
        else "soon" if remaining <= item["warning_days"] else "safe"
    )
    return item


def audit(connection, action, *, item=None, source=None, destination=None, delta=0, note=""):
    connection.execute(
        "INSERT INTO logs (action,item_id,item_name,from_location,to_location,quantity_delta,note,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (action, item["id"] if item else None, item["name"] if item else "", source, destination, delta, note, now()),
    )


def canonical_origin(value):
    """Compare scheme, hostname, and effective port without trusting forwarded host."""
    try:
        if any(character.isspace() for character in value) or "\\" in value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        if len(parsed.hostname) > 253:
            return None
        return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def same_origin(request, origin):
    actual = canonical_origin(origin)
    if actual is None:
        return False
    host = request.headers.get("host", "")
    expected = canonical_origin(f"{request.url.scheme}://{host}")
    if actual == expected:
        return True
    # A NAS TLS proxy commonly forwards the public Host to an HTTP container.
    # Permit only an HTTPS upgrade with the same host/port authority; never use
    # an arbitrary X-Forwarded-Host value as a browser-origin allowlist.
    return request.url.scheme == "http" and actual[0] == "https" and actual == canonical_origin(f"https://{host}")


def stable_key(data_dir):
    path = data_dir / "session.key"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(key)
    if len(key) != 32:
        raise RuntimeError("session.key 必须是完整的 32 字节密钥；请从备份恢复此文件")
    return key


def create_app(data_dir: str | Path | None = None, password: str | None = None, timezone: str | None = None):
    data_path = Path(data_dir or os.environ.get("DATA_DIR", "./data")).expanduser().resolve()
    configured_password = os.environ.get("HOME_STORAGE_PASSWORD", "") if password is None else password
    timezone_name = timezone or os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
    try:
        app_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"无效 APP_TIMEZONE：{timezone_name}") from error

    @asynccontextmanager
    async def lifespan(application):
        data_path.mkdir(parents=True, exist_ok=True)
        (data_path / "uploads").mkdir(exist_ok=True)
        initialize(data_path / "homestorage.sqlite3")
        key = stable_key(data_path)
        application.state.session_key = hmac.digest(key, configured_password.encode("utf-8"), "sha256")
        yield

    application = FastAPI(title="HomeStorage", version="1.0.0", lifespan=lifespan)
    application.state.data_dir = data_path
    application.state.db_path = data_path / "homestorage.sqlite3"

    def connection():
        database = connect(application.state.db_path)
        try:
            yield database
        finally:
            database.close()

    Database = Annotated[sqlite3.Connection, Depends(connection)]

    def today():
        return datetime.now(app_timezone).date()

    def authenticated(request):
        if not configured_password:
            return True
        token = request.cookies.get(COOKIE_NAME, "")
        try:
            expires, nonce, signature = token.split(".")
            payload = f"{expires}.{nonce}"
            expected = hmac.new(application.state.session_key, payload.encode(), hashlib.sha256).hexdigest()
            return int(expires) > time.time() and hmac.compare_digest(signature, expected)
        except (ValueError, AttributeError):
            return False

    @application.middleware("http")
    async def access_control(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or (
                origin is not None and not same_origin(request, origin)
            ):
                return JSONResponse({"detail": "仅允许来自当前网页的请求"}, status_code=403)
        protected = request.url.path.startswith(("/api/", "/uploads/"))
        if protected and request.url.path not in {"/api/auth", "/api/login", "/api/logout"} and not authenticated(request):
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if protected:
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        return JSONResponse({"detail": "参数格式或取值不正确，请检查填写内容"}, status_code=422)

    @application.get("/health")
    def health(database: Database):
        try:
            database.execute("SELECT 1 FROM locations LIMIT 1").fetchone()
        except sqlite3.Error:
            fail("数据库不可用", 503)
        return {"status": "ok"}

    @application.get("/api/auth")
    def auth(request: Request):
        return {"authenticated": authenticated(request), "auth_required": bool(configured_password)}

    @application.post("/api/login")
    def login(body: LoginInput, request: Request):
        if configured_password and not hmac.compare_digest(body.password.encode(), configured_password.encode()):
            fail("密码不正确", 401)
        expires = int(time.time()) + SESSION_SECONDS
        payload = f"{expires}.{secrets.token_hex(16)}"
        signature = hmac.new(application.state.session_key, payload.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"authenticated": True, "auth_required": bool(configured_password)})
        browser_origin = canonical_origin(request.headers.get("origin", ""))
        secure_cookie = request.url.scheme == "https" or bool(browser_origin and browser_origin[0] == "https")
        response.set_cookie(
            COOKIE_NAME, f"{payload}.{signature}", max_age=SESSION_SECONDS,
            httponly=True, secure=secure_cookie,
            samesite="strict", path="/",
        )
        return response

    @application.post("/api/logout")
    def logout():
        response = JSONResponse({"authenticated": not bool(configured_password), "auth_required": bool(configured_password)})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @application.get("/api/meta")
    def meta():
        return {"categories": CATEGORIES, "timezone": timezone_name}

    @application.get("/api/locations")
    def locations(database: Database):
        tree = location_tree(database)
        quantities = {}
        for item in database.execute("SELECT location_id, status FROM items"):
            if item["status"] in ACTIVE:
                quantities[item["location_id"]] = quantities.get(item["location_id"], 0) + 1
        return [
            {**row, "item_count": sum(quantities.get(child, 0) for child in descendants(tree, key))}
            for key, row in tree.items()
        ]

    @application.post("/api/locations", status_code=201)
    def create_location(body: LocationCreate, database: Database):
        with transaction(database):
            if body.parent_id is not None:
                require_row(database, "locations", body.parent_id)
            cursor = database.execute(
                "INSERT INTO locations (name,code,type,parent_id) VALUES (?,?,?,?)",
                (body.name, body.code, body.type, body.parent_id),
            )
            result = serialize_location(database, cursor.lastrowid)
            audit(database, "location_create", destination=result["path"])
        return result

    @application.patch("/api/locations/{location_id}")
    def update_location(location_id: int, body: LocationPatch, database: Database):
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for key, value in changes.items() if key != "parent_id"):
            fail("位置名称、编号和类型不能设为空值", 422)
        with transaction(database):
            require_row(database, "locations", location_id)
            tree = location_tree(database)
            source = tree[location_id]["path"]
            if "parent_id" in changes and changes["parent_id"] is not None:
                require_row(database, "locations", changes["parent_id"])
                if changes["parent_id"] in descendants(tree, location_id):
                    fail("位置不能放入自身或下级位置")
            if changes:
                assignments = ",".join(f"{key}=?" for key in changes)
                database.execute(f"UPDATE locations SET {assignments} WHERE id=?", (*changes.values(), location_id))
                result = serialize_location(database, location_id)
                audit(database, "location_update", source=source, destination=result["path"])
            else:
                result = serialize_location(database, location_id)
        return result

    @application.delete("/api/locations/{location_id}")
    def delete_location(location_id: int, database: Database):
        with transaction(database):
            require_row(database, "locations", location_id)
            if database.execute("SELECT 1 FROM locations WHERE parent_id=? LIMIT 1", (location_id,)).fetchone():
                fail("请先移除该位置的下级位置", 409)
            if database.execute("SELECT 1 FROM items WHERE location_id=? LIMIT 1", (location_id,)).fetchone():
                fail("该位置仍有关联物品，不能删除", 409)
            source = location_tree(database)[location_id]["path"]
            database.execute("DELETE FROM locations WHERE id=?", (location_id,))
            audit(database, "location_delete", source=source)
        return {"ok": True}

    @application.get("/api/locations/{location_id}/qr")
    def location_qr(location_id: int, database: Database, base_url: str = Query(min_length=1, max_length=2048)):
        require_row(database, "locations", location_id)
        if canonical_origin(base_url) is None:
            fail("二维码地址必须是有效的 HTTP(S) 网站地址")
        output = io.BytesIO()
        qrcode.make(
            f"{base_url.rstrip('/')}/?location={location_id}",
            image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=4,
        ).save(output)
        return Response(output.getvalue(), media_type="image/svg+xml")

    def all_items(database):
        tree = location_tree(database)
        calendar_day = today()
        return [serialize_item(row, tree, calendar_day) for row in database.execute("SELECT * FROM items ORDER BY id DESC")]

    def matched(items, query):
        terms = query.casefold().split()
        return [item for item in items if all(
            term in " ".join(str(item[key] or "") for key in ("name", "brand", "model", "tags", "notes", "location_path")).casefold()
            for term in terms
        )]

    @application.get("/api/items")
    def items(
        database: Database, q: str = Query(default="", max_length=200), location_id: int | None = None,
        category: str | None = None, status: str | None = None, expiry: Literal["soon", "expired"] | None = None,
    ):
        result = matched(all_items(database), q)
        if location_id is not None:
            valid_ids = descendants(location_tree(database), location_id)
            result = [item for item in result if item["location_id"] in valid_ids]
        if category:
            result = [item for item in result if item["category"] == category]
        if status:
            result = [item for item in result if item["status"] == status]
        if expiry:
            result = [item for item in result if item["expiry_status"] == expiry and item["status"] in ACTIVE]
        return result

    @application.post("/api/search")
    def search(body: SearchInput, database: Database):
        with transaction(database):
            result = matched(all_items(database), body.query)
            audit(database, "search", note=body.query)
        return {"count": len(result)}

    @application.post("/api/items", status_code=201)
    def create_item(body: ItemCreate, database: Database):
        values = body.model_dump(mode="json")
        values["stored_date"] = values["stored_date"] or today().isoformat()
        values.update(status="in_stock" if body.quantity else "depleted", created_at=now(), updated_at=now())
        with transaction(database):
            require_row(database, "locations", body.location_id)
            keys = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            cursor = database.execute(f"INSERT INTO items ({keys}) VALUES ({placeholders})", tuple(values.values()))
            tree = location_tree(database)
            result = serialize_item(require_row(database, "items", cursor.lastrowid), tree, today())
            audit(database, "create", item=result, destination=result["location_path"], delta=result["quantity"])
        return result

    @application.get("/api/items/{item_id}")
    def item(item_id: int, database: Database):
        return serialize_item(require_row(database, "items", item_id), location_tree(database), today())

    @application.patch("/api/items/{item_id}")
    def update_item(item_id: int, body: ItemPatch, database: Database):
        changes = body.model_dump(mode="json", exclude_unset=True)
        nullable = {"image_url", "purchase_date", "purchase_price", "expires_on"}
        if any(value is None for key, value in changes.items() if key not in nullable):
            fail("必填物品信息不能设为空值", 422)
        with transaction(database):
            original = dict(require_row(database, "items", item_id))
            # Reuse the full metadata validators after merging an optional patch.
            metadata = {key: value for key, value in original.items() if key in ItemFields.model_fields}
            try:
                validated = ItemFields.model_validate({**metadata, **changes})
            except ValidationError:
                fail("参数格式或取值不正确，请检查填写内容", 422)
            normalized = validated.model_dump(mode="json")
            changes = {key: normalized[key] for key in changes}
            tree = location_tree(database)
            if changes:
                changes["updated_at"] = now()
                assignments = ",".join(f"{key}=?" for key in changes)
                database.execute(f"UPDATE items SET {assignments} WHERE id=?", (*changes.values(), item_id))
                source = tree[original["location_id"]]["path"]
                audit(database, "update", item=original, source=source, destination=source)
            result = serialize_item(require_row(database, "items", item_id), tree, today())
        return result

    @application.post("/api/items/{item_id}/actions")
    def item_action(item_id: int, body: ActionInput, database: Database):
        with transaction(database):
            original = dict(require_row(database, "items", item_id))
            status, quantity, location = original["status"], original["quantity"], original["location_id"]
            action = body.action
            allowed = {
                "move": {"in_stock"}, "take_out": {"in_stock"}, "return": {"in_use", "lent"},
                "lend": {"in_stock", "in_use"}, "consume": {"in_stock"},
                "restock": {"in_stock", "depleted"}, "discard": ACTIVE, "lost": ACTIVE,
            }
            if status not in allowed[action]:
                fail("当前物品状态不允许此操作", 409)
            if body.location_id is not None and action not in {"move", "return"}:
                fail("此操作不能修改位置")
            if body.quantity is not None and action not in {"consume", "restock"}:
                fail("此操作不能修改数量")
            if action == "move" and body.location_id is None:
                fail("请选择目标位置")
            if action in {"consume", "restock"} and body.quantity is None:
                fail("请输入大于零的数量")
            if action in {"move", "return"} and body.location_id is not None:
                require_row(database, "locations", body.location_id)
                location = body.location_id
            if action == "take_out":
                status = "in_use"
            elif action == "return":
                status = "in_stock"
            elif action == "lend":
                status = "lent"
            elif action == "consume":
                if body.quantity > quantity:
                    fail("消耗数量不能超过库存")
                quantity -= body.quantity
                status = "in_stock" if quantity else "depleted"
            elif action == "restock":
                quantity += body.quantity
                if quantity > 1_000_000_000:
                    fail("库存数量过大")
                status = "in_stock"
            elif action == "discard":
                quantity, status = 0, "discarded"
            elif action == "lost":
                status = "lost"
            tree = location_tree(database)
            database.execute(
                "UPDATE items SET status=?,quantity=?,location_id=?,updated_at=? WHERE id=?",
                (status, quantity, location, now(), item_id),
            )
            audit(
                database, action, item=original, source=tree[original["location_id"]]["path"],
                destination=tree[location]["path"], delta=quantity - original["quantity"], note=body.note,
            )
            result = serialize_item(require_row(database, "items", item_id), tree, today())
        return result

    @application.get("/api/logs")
    def logs(database: Database, item_id: int | None = None, limit: int = Query(default=100, ge=1, le=500)):
        if item_id is None:
            rows = database.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = database.execute("SELECT * FROM logs WHERE item_id=? ORDER BY id DESC LIMIT ?", (item_id, limit))
        return [dict(row) for row in rows]

    @application.get("/api/dashboard")
    def dashboard(database: Database):
        active = [item for item in all_items(database) if item["status"] in ACTIVE]
        expiring = sorted(
            [item for item in active if item["expiry_status"] in {"soon", "expired"}],
            key=lambda item: (item["expires_on"], item["id"]),
        )
        return {
            "total_items": len(active), "total_quantity": sum(item["quantity"] for item in active),
            "location_count": database.execute("SELECT count(*) FROM locations").fetchone()[0],
            "in_use_count": sum(item["status"] in {"in_use", "lent"} for item in active),
            "soon_count": sum(item["expiry_status"] == "soon" for item in active),
            "expired_count": sum(item["expiry_status"] == "expired" for item in active),
            "recent_logs": [dict(row) for row in database.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 8")],
            "expiring_items": expiring[:20],
        }

    @application.post("/api/uploads", status_code=201)
    async def upload(file: Annotated[UploadFile, File()]):
        payload = await file.read(MAX_UPLOAD_BYTES + 1)
        await file.close()
        if len(payload) > MAX_UPLOAD_BYTES:
            fail("图片不能超过 10 MB", 413)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(payload)) as candidate:
                    if candidate.format not in {"JPEG", "PNG", "WEBP"}:
                        fail("只支持 JPEG、PNG 或 WebP 图片", 415)
                    candidate.verify()
                with Image.open(io.BytesIO(payload)) as candidate:
                    candidate.load()
                    image = ImageOps.exif_transpose(candidate).convert("RGB")
                    image.thumbnail((2400, 2400))
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=88, optimize=True)
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            fail("图片内容无效或尺寸过大", 415)
        name = f"{secrets.token_hex(16)}.jpg"
        (data_path / "uploads" / name).write_bytes(output.getvalue())
        return {"image_url": f"/uploads/{name}"}

    @application.get("/uploads/{filename}")
    def uploaded_image(filename: str):
        if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
            fail("图片不存在", 404)
        path = data_path / "uploads" / filename
        if not path.is_file():
            fail("图片不存在", 404)
        return FileResponse(path, media_type="image/jpeg")

    @application.get("/api/export")
    def export(database: Database):
        # A read transaction makes locations, items and append-only logs one snapshot.
        database.execute("BEGIN")
        try:
            result = {
                "version": 1, "exported_at": now(), "locations": locations(database),
                "items": all_items(database),
                "logs": [dict(row) for row in database.execute("SELECT * FROM logs ORDER BY id")],
            }
        finally:
            database.rollback()
        filename = f"homestorage-{today().isoformat()}.json"
        return Response(
            json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon():
        path = STATIC_DIR / "icon.svg"
        return FileResponse(path, media_type="image/svg+xml") if path.is_file() else Response(status_code=204)

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return application


app = create_app()
