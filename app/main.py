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
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator

from .db import DEFAULT_CATEGORIES, connect, initialize, transaction
from .ai_settings import AISettingsStore, build_ai_settings_router
from .updates import build_updates_router, maintenance_active, update_state_dir
from .version import runtime_revision, runtime_version


CATEGORIES = DEFAULT_CATEGORIES
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


class CategoryCreate(InputModel):
    name: str = Field(min_length=1, max_length=120)
    sort_order: Annotated[StrictInt, Field(ge=-1_000_000, le=1_000_000)] | None = None


class CategoryPatch(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: Annotated[StrictInt, Field(ge=-1_000_000, le=1_000_000)] | None = None
    active: StrictBool | None = None


class ArchiveInput(InputModel):
    confirmed: StrictBool
    note: str = Field(default="", max_length=2000)


class ItemFields(InputModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="其他", min_length=1, max_length=120)
    category_id: Annotated[StrictInt, Field(gt=0)] | None = None
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
    category: str | None = Field(default=None, min_length=1, max_length=120)
    category_id: Annotated[StrictInt, Field(gt=0)] | None = None
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
        fail({"locations": "位置不存在", "categories": "分类不存在"}.get(table, "物品不存在"), 404)
    return row


def location_tree(connection):
    rows = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM locations ORDER BY id")}
    for location_id in rows:
        pending = []
        visited = set()
        ancestor = location_id
        while ancestor is not None and "path" not in rows[ancestor]:
            if ancestor in visited:
                fail("位置层级数据无效，请检查数据备份", 503)
            visited.add(ancestor)
            pending.append(ancestor)
            ancestor = rows[ancestor]["parent_id"]
        prefix = rows[ancestor]["path"] if ancestor is not None else ""
        for pending_id in reversed(pending):
            row = rows[pending_id]
            prefix = f"{prefix} / {row['name']}" if row["parent_id"] is not None else row["name"]
            row["path"] = prefix
    return rows


def descendants(tree, location_id):
    if location_id not in tree:
        fail("位置不存在", 404)
    children = {}
    for key, row in tree.items():
        children.setdefault(row["parent_id"], []).append(key)
    result = {location_id}
    pending = [location_id]
    while pending:
        parent_id = pending.pop()
        for child_id in children.get(parent_id, ()):
            if child_id not in result:
                result.add(child_id)
                pending.append(child_id)
    return result


def location_item_counts(connection, tree):
    """Count each active record once, then roll child totals up to their parents."""
    counts = dict.fromkeys(tree, 0)
    for row in connection.execute(
        "SELECT location_id,COUNT(*) AS item_count FROM items "
        "WHERE archived_at IS NULL AND status IN ('in_stock','in_use','lent') GROUP BY location_id"
    ):
        counts[row["location_id"]] = row["item_count"]
    remaining_children = dict.fromkeys(tree, 0)
    for row in tree.values():
        if row["parent_id"] is not None:
            remaining_children[row["parent_id"]] += 1
    pending = [key for key, count in remaining_children.items() if not count]
    while pending:
        location_id = pending.pop()
        parent_id = tree[location_id]["parent_id"]
        if parent_id is not None:
            counts[parent_id] += counts[location_id]
            remaining_children[parent_id] -= 1
            if not remaining_children[parent_id]:
                pending.append(parent_id)
    return counts


def serialize_location(connection, location_id):
    tree = location_tree(connection)
    ids = descendants(tree, location_id)
    count = sum(
        1 for row in connection.execute("SELECT location_id, status FROM items WHERE archived_at IS NULL")
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


def list_categories(connection):
    return [
        {**dict(row), "active": bool(row["active"])}
        for row in connection.execute(
            "SELECT categories.*,COUNT(items.id) AS item_count FROM categories "
            "LEFT JOIN items ON items.category_id=categories.id "
            "GROUP BY categories.id ORDER BY categories.sort_order,categories.id"
        )
    ]


def resolve_category(connection, *, category_id=None, name=None, current_id=None):
    if category_id is not None:
        row = require_row(connection, "categories", category_id)
        if name is not None and name != row["name"]:
            fail("分类名称与分类编号不一致", 422)
    else:
        row = connection.execute("SELECT * FROM categories WHERE name=?", (name or "其他",)).fetchone()
        if row is None:
            fail("请选择有效分类", 422)
    if not row["active"] and row["id"] != current_id:
        fail("该分类已停用，请选择其他分类", 409)
    return dict(row)


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
        ai_settings.initialize()
        key = stable_key(data_path)
        application.state.session_key = hmac.digest(key, configured_password.encode("utf-8"), "sha256")
        yield

    current_version = runtime_version()
    current_revision = runtime_revision()
    updates_path = update_state_dir()
    application = FastAPI(title="HomeStorage", version=current_version, lifespan=lifespan)
    application.state.data_dir = data_path
    application.state.db_path = data_path / "homestorage.sqlite3"
    ai_settings = AISettingsStore(lambda: connect(application.state.db_path))

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
        update_controls = request.url.path in {
            "/api/settings/updates", "/api/settings/updates/check", "/api/settings/updates/install",
        }
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path not in {"/api/login", "/api/logout"}
            and not update_controls and maintenance_active(updates_path)
        ):
            return JSONResponse({"detail": "系统正在备份或更新，请稍后再修改数据。"}, status_code=503)
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
    def meta(database: Database):
        return {
            "categories": [row["name"] for row in list_categories(database) if row["active"]],
            "timezone": timezone_name, "version": current_version, "revision": current_revision,
        }

    @application.get("/api/categories")
    def categories(database: Database):
        return list_categories(database)

    @application.post("/api/categories", status_code=201)
    def create_category(body: CategoryCreate, database: Database):
        with transaction(database):
            order = body.sort_order
            if order is None:
                order = database.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM categories").fetchone()[0]
            try:
                cursor = database.execute("INSERT INTO categories(name,sort_order) VALUES (?,?)", (body.name, order))
            except sqlite3.IntegrityError:
                fail("分类名称已存在", 409)
            audit(database, "category_create", note=json.dumps({"id": cursor.lastrowid, "name": body.name}, ensure_ascii=False))
            result = next(row for row in list_categories(database) if row["id"] == cursor.lastrowid)
        return result

    @application.patch("/api/categories/{category_id}")
    def update_category(category_id: int, body: CategoryPatch, database: Database):
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            fail("分类信息不能设为空值", 422)
        with transaction(database):
            original = dict(require_row(database, "categories", category_id))
            if changes:
                try:
                    assignments = ",".join(f"{key}=?" for key in changes)
                    database.execute(f"UPDATE categories SET {assignments} WHERE id=?", (*changes.values(), category_id))
                except sqlite3.IntegrityError:
                    fail("分类名称已存在", 409)
                if "name" in changes and changes["name"] != original["name"]:
                    database.execute(
                        "UPDATE items SET category=?,updated_at=? WHERE category_id=?", (changes["name"], now(), category_id)
                    )
                audit(database, "category_update", note=json.dumps(
                    {"id": category_id, "before": original, "changes": changes}, ensure_ascii=False
                ))
            result = next(row for row in list_categories(database) if row["id"] == category_id)
        return result

    @application.delete("/api/categories/{category_id}")
    def delete_category(category_id: int, database: Database):
        with transaction(database):
            original = dict(require_row(database, "categories", category_id))
            if database.execute("SELECT 1 FROM items WHERE category_id=? LIMIT 1", (category_id,)).fetchone():
                fail("该分类仍有关联物品（包括已归档物品），请先调整分类", 409)
            database.execute("DELETE FROM categories WHERE id=?", (category_id,))
            audit(database, "category_delete", note=json.dumps(original, ensure_ascii=False))
        return {"ok": True}

    @application.get("/api/locations")
    def locations(database: Database):
        tree = location_tree(database)
        counts = location_item_counts(database, tree)
        return [{**row, "item_count": counts[key]} for key, row in tree.items()]

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

    def all_items(
        database, include_archived=False, *, tree=None, location_ids=None,
        category=None, category_id=None, status=None, archived_only=False,
    ):
        if tree is None:
            tree = location_tree(database)
        calendar_day = today()
        conditions = []
        parameters = []
        if not include_archived:
            conditions.append("archived_at IS NULL")
        elif archived_only:
            conditions.append("archived_at IS NOT NULL")
        if location_ids is not None:
            conditions.append(f"location_id IN ({','.join('?' for _ in location_ids)})")
            parameters.extend(location_ids)
        for key, value in (("category", category), ("category_id", category_id), ("status", status)):
            if value is not None and (value or key == "category_id"):
                conditions.append(f"{key}=?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return [
            serialize_item(row, tree, calendar_day)
            for row in database.execute(f"SELECT * FROM items{where} ORDER BY id DESC", parameters)
        ]

    def matched(items, query):
        terms = query.casefold().split()
        if not terms:
            return items
        result = []
        for item in items:
            text = " ".join(
                str(item[key] or "") for key in ("name", "brand", "model", "tags", "notes", "location_path")
            ).casefold()
            if all(term in text for term in terms):
                result.append(item)
        return result

    @application.get("/api/items")
    def items(
        database: Database, q: str = Query(default="", max_length=200), location_id: int | None = None,
        category: str | None = None, status: str | None = None, expiry: Literal["soon", "expired"] | None = None,
        category_id: int | None = None, archived: Literal["false", "true", "all"] = "false",
    ):
        tree = location_tree(database)
        valid_ids = descendants(tree, location_id) if location_id is not None else None
        if category_id is not None:
            require_row(database, "categories", category_id)
        result = matched(all_items(
            database, include_archived=archived != "false", tree=tree, location_ids=valid_ids,
            category=category, category_id=category_id, status=status, archived_only=archived == "true",
        ), q)
        if expiry:
            result = [item for item in result if item["expiry_status"] == expiry and item["status"] in ACTIVE]
        return result

    @application.post("/api/search")
    def search(body: SearchInput, database: Database):
        with transaction(database):
            result = matched(all_items(database), body.query)
            audit(database, "search", note=body.query)
        return {"count": len(result)}

    def create_item_data(body: ItemCreate, database, *, audit_note=""):
        values = body.model_dump(mode="json")
        values["stored_date"] = values["stored_date"] or today().isoformat()
        values.update(status="in_stock" if body.quantity else "depleted", created_at=now(), updated_at=now())
        with transaction(database):
            require_row(database, "locations", body.location_id)
            selected = resolve_category(
                database, category_id=body.category_id,
                name=body.category if "category" in body.model_fields_set or body.category_id is None else None,
            )
            values.update(category_id=selected["id"], category=selected["name"])
            keys = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            cursor = database.execute(f"INSERT INTO items ({keys}) VALUES ({placeholders})", tuple(values.values()))
            tree = location_tree(database)
            result = serialize_item(require_row(database, "items", cursor.lastrowid), tree, today())
            audit(database, "create", item=result, destination=result["location_path"], delta=result["quantity"], note=audit_note)
        return result

    @application.post("/api/items", status_code=201)
    def create_item(body: ItemCreate, database: Database):
        return create_item_data(body, database)

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
            if "category" in changes or "category_id" in changes:
                selected = resolve_category(
                    database, category_id=changes.get("category_id"), name=changes.get("category"),
                    current_id=original["category_id"],
                )
                changes.update(category_id=selected["id"], category=selected["name"])
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
            if original["archived_at"] is not None:
                fail("物品已归档，请先恢复再操作库存", 409)
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

    def change_archive(database, item_id, note="", *, restore=False):
        with transaction(database):
            original = dict(require_row(database, "items", item_id))
            if restore and original["archived_at"] is None:
                fail("该物品未归档，无需恢复", 409)
            if not restore and original["archived_at"] is not None:
                fail("该物品已归档", 409)
            timestamp = now()
            database.execute(
                "UPDATE items SET archived_at=?,updated_at=? WHERE id=?",
                (None if restore else timestamp, timestamp, item_id),
            )
            tree = location_tree(database)
            path = tree[original["location_id"]]["path"]
            audit(database, "restore" if restore else "archive", item=original, source=path, destination=path, note=note)
            result = serialize_item(require_row(database, "items", item_id), tree, today())
        return result

    @application.post("/api/items/{item_id}/archive")
    def archive_item(item_id: int, body: ArchiveInput, database: Database):
        if body.confirmed is not True:
            fail("归档需要用户明确确认")
        return change_archive(database, item_id, body.note)

    @application.post("/api/items/{item_id}/restore")
    def restore_item(item_id: int, body: ArchiveInput, database: Database):
        if body.confirmed is not True:
            fail("恢复需要用户明确确认")
        return change_archive(database, item_id, body.note, restore=True)

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
                "version": 2, "exported_at": now(), "locations": locations(database),
                "categories": list_categories(database), "items": all_items(database, include_archived=True),
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

    def validated_payload(model, payload):
        try:
            return model.model_validate(payload)
        except ValidationError:
            fail("参数格式或取值不正确，请检查填写内容", 422)

    def ai_actor(request):
        if not configured_password:
            return None
        token = request.cookies.get(COOKIE_NAME, "")
        return hashlib.sha256(token.encode()).hexdigest() if token else None

    # Keep AI within the same validated and audited data operations as the UI.
    # No database path, filesystem, shell, or configuration operations are tools.
    from .ai import build_ai_router
    application.include_router(build_ai_router({
        "connect": lambda: connect(application.state.db_path),
        "today": today,
        "list_categories": list_categories,
        "list_locations": locations,
        "find_items": lambda database, query, include_archived=False: matched(
            all_items(database, include_archived=include_archived), query
        ),
        "read_item": lambda database, item_id: item(item_id, database),
        "create_item": lambda database, payload: create_item_data(
            validated_payload(ItemCreate, payload), database, audit_note="AI 草稿经用户确认录入"
        ),
        "item_action": lambda database, item_id, payload: item_action(item_id, validated_payload(ActionInput, payload), database),
        "archive_item": lambda database, item_id, note: change_archive(database, item_id, note),
        "actor": ai_actor,
        "timezone": timezone_name,
        "settings": ai_settings,
    }))
    application.include_router(build_ai_settings_router(
        ai_settings, password_required=bool(configured_password),
    ))
    application.include_router(build_updates_router(
        current_version=current_version, current_revision=current_revision,
        password_required=bool(configured_password), state_dir=updates_path,
    ))

    application.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return application


app = create_app()
