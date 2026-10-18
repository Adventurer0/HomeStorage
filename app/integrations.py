"""Revocable, read-only inventory access for external AI clients.

The Bearer token authenticates only the three GET endpoints in this module. It
does not grant a browser session, photo access, arbitrary SQL, or configuration
access. Only a SHA-256 digest of the generated 256-bit credential is persisted.
"""

from collections.abc import Mapping
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import shlex
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from .db import transaction


ITEM_FIELDS = (
    "id", "name", "category", "category_id", "location_id", "location_path",
    "quantity", "unit", "barcode", "brand", "model", "tags", "notes", "stored_date",
    "purchase_date", "purchase_price", "expires_on", "warning_days", "status",
    "created_at", "updated_at", "archived_at", "remaining_days", "expiry_status",
)
LOCATION_FIELDS = ("id", "name", "code", "type", "parent_id", "path", "item_count")
CATEGORY_FIELDS = ("id", "name", "sort_order", "active", "item_count")
TOKEN_FIELDS = ("id", "name", "created_at", "expires_at", "last_used_at", "revoked_at")
TOKEN_PATTERN = re.compile(r"hs_ro_[A-Za-z0-9_-]{43}\Z")
EXTERNAL_PREFIX = "/api/external/v1"
EXTERNAL_GET_PATHS = frozenset(EXTERNAL_PREFIX + suffix for suffix in ("/items", "/locations", "/categories"))


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: StrictStr = Field(min_length=1, max_length=120)
    expires_days: Annotated[StrictInt, Field(ge=1, le=366)] = 90

    @field_validator("name")
    @classmethod
    def printable_name(cls, value):
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("名称不能包含控制字符")
        return value


class InventoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    q: str = Field(default="", max_length=200)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000_000)

    @field_validator("limit", "offset", mode="before")
    @classmethod
    def decimal_integer(cls, value):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            return int(value)
        if type(value) is int:
            return value
        raise ValueError("分页参数必须是整数")


def _pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def initialize_integrations(database):
    with transaction(database):
        database.execute("""CREATE TABLE IF NOT EXISTS external_api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        )""")


def _no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


def _openapi(base_url):
    """An importable OpenAPI document exposing only the fixed read-only scope."""
    def operation(summary, schema, parameters=None):
        return {"get": {
            "summary": summary,
            "operationId": {"查询物品": "searchInventory", "查询位置": "listStorageLocations", "查询分类": "listInventoryCategories"}[summary],
            "security": [{"ReadOnlyToken": []}],
            "parameters": parameters or [],
            "responses": {
                "200": {"description": "只读库存数据", "content": {"application/json": {"schema": schema}}},
                "401": {"description": "令牌无效、过期或已撤销"},
                "422": {"description": "查询参数不符合约束"},
            },
        }}

    item_properties = {name: {} for name in ITEM_FIELDS}
    for name in ("id", "category_id", "location_id", "quantity", "warning_days", "remaining_days"):
        item_properties[name] = {"type": ["integer", "null"]}
    for name in set(ITEM_FIELDS) - set(("id", "category_id", "location_id", "quantity", "warning_days", "remaining_days", "purchase_price")):
        item_properties[name] = {"type": ["string", "null"]}
    item_properties["purchase_price"] = {"type": ["number", "null"]}
    return {
        "openapi": "3.1.0",
        "info": {"title": "HomeStorage Read-only AI API", "version": "1.0.0",
                 "description": "只读物品、位置和分类。不能修改库存、读取配置、执行 SQL 或访问文件。凭据仅通过 Authorization: Bearer 请求头发送；服务器不会替 NAS 开放公网。"},
        "servers": [{"url": base_url}],
        "components": {
            "securitySchemes": {"ReadOnlyToken": {"type": "http", "scheme": "bearer"}},
            "schemas": {"Item": {"type": "object", "properties": item_properties, "additionalProperties": False}},
        },
        "paths": {
            "/items": operation("查询物品", {
                "type": "object", "properties": {
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
                    "total": {"type": "integer"}, "limit": {"type": "integer"}, "offset": {"type": "integer"},
                },
            }, [
                {"name": "q", "in": "query", "schema": {"type": "string", "maxLength": 200, "default": ""}},
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
                {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0, "maximum": 1000000000, "default": 0}},
            ]),
            "/locations": operation("查询位置", {"type": "array", "items": {"type": "object", "properties": {name: {} for name in LOCATION_FIELDS}, "additionalProperties": False}}),
            "/categories": operation("查询分类", {"type": "array", "items": {"type": "object", "properties": {name: {} for name in CATEGORY_FIELDS}, "additionalProperties": False}}),
        },
    }


def build_integrations_router(services):
    service = (lambda key: services[key]) if isinstance(services, Mapping) else (lambda key: getattr(services, key))
    router = APIRouter(tags=["integrations"], dependencies=[Depends(_no_store)])

    def base_url(request):
        # Derive the address from the current request, never from a user-supplied
        # public URL, forwarded-host header, or proxy/tunnel configuration.
        return str(request.base_url).rstrip("/") + EXTERNAL_PREFIX

    def require_token(request, database):
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not TOKEN_PATTERN.fullmatch(token):
            raise HTTPException(401, "只读访问令牌无效或已失效", headers={"WWW-Authenticate": "Bearer"})
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with transaction(database):
            row = database.execute("SELECT * FROM external_api_tokens WHERE token_hash=?", (digest,)).fetchone()
            timestamp = service("now")()
            if row is None or row["revoked_at"] is not None or _timestamp(row["expires_at"]) <= _timestamp(timestamp):
                raise HTTPException(401, "只读访问令牌无效或已失效", headers={"WWW-Authenticate": "Bearer"})
            database.execute("UPDATE external_api_tokens SET last_used_at=? WHERE id=?", (timestamp, row["id"]))

    @router.get("/api/integrations/tokens")
    def tokens(request: Request):
        service("require_admin")(request)
        with closing(service("connect")()) as database:
            rows = database.execute("SELECT * FROM external_api_tokens ORDER BY id DESC").fetchall()
            return {"tokens": [_pick(dict(row), TOKEN_FIELDS) for row in rows]}

    @router.post("/api/integrations/tokens", status_code=201)
    def create_token(body: TokenCreate, request: Request):
        service("require_admin")(request)
        token = "hs_ro_" + secrets.token_urlsafe(32)
        timestamp = service("now")()
        expires = _iso(_timestamp(timestamp) + timedelta(days=body.expires_days))
        with closing(service("connect")()) as database, transaction(database):
            cursor = database.execute(
                "INSERT INTO external_api_tokens(name,token_hash,created_at,expires_at) VALUES(?,?,?,?)",
                (body.name, hashlib.sha256(token.encode("ascii")).hexdigest(), timestamp, expires),
            )
            row = dict(database.execute("SELECT * FROM external_api_tokens WHERE id=?", (cursor.lastrowid,)).fetchone())
            service("audit")(database, "external_token_create", note=f"只读令牌：{body.name}")
        return {**_pick(row, TOKEN_FIELDS), "token": token}

    @router.delete("/api/integrations/tokens/{token_id}")
    def revoke_token(token_id: Annotated[int, Field(gt=0)], request: Request):
        service("require_admin")(request)
        with closing(service("connect")()) as database, transaction(database):
            row = database.execute("SELECT * FROM external_api_tokens WHERE id=?", (token_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "令牌不存在")
            if row["revoked_at"] is None:
                database.execute("UPDATE external_api_tokens SET revoked_at=? WHERE id=?", (service("now")(), token_id))
                service("audit")(database, "external_token_revoke", note=f"只读令牌：{row['name']}")
        return {"ok": True}

    @router.get("/api/integrations/connection")
    def connection(request: Request):
        service("require_admin")(request)
        base = base_url(request)
        return {
            "base_url": base,
            "items_url": base + "/items", "locations_url": base + "/locations", "categories_url": base + "/categories",
            "openapi_url": str(request.base_url).rstrip("/") + "/api/integrations/openapi.json",
            "curl": "curl --header 'Authorization: Bearer YOUR_READ_ONLY_TOKEN' " + shlex.quote(base + "/items?limit=20"),
        }

    @router.get("/api/integrations/openapi.json")
    def openapi(request: Request):
        service("require_admin")(request)
        return _openapi(base_url(request))

    @router.get(EXTERNAL_PREFIX + "/items")
    def external_items(request: Request, query: Annotated[InventoryQuery, Query()]):
        with closing(service("connect")()) as database:
            require_token(request, database)
            items = service("find_items")(database, query.q, False) if query.q else service("all_items")(database, False)
            # The browser and AI share the same search semantics. Only public
            # inventory fields survive; future database columns are not exposed.
            items = [item for item in items if not item.get("archived_at")]
            record = services.get("record_search") if isinstance(services, Mapping) else getattr(services, "record_search", None)
            if query.q.strip() and record is not None:
                # This token cannot mutate inventory. Record explicit searches
                # as access analytics without impersonating its creator, and
                # avoid counting list/pagination requests with no search term.
                with transaction(database):
                    record(database, query.q, [item["id"] for item in items], source="external")
            return {"items": [_pick(item, ITEM_FIELDS) for item in items[query.offset:query.offset + query.limit]],
                    "total": len(items), "limit": query.limit, "offset": query.offset}

    @router.get(EXTERNAL_PREFIX + "/locations")
    def external_locations(request: Request):
        with closing(service("connect")()) as database:
            require_token(request, database)
            return [_pick(item, LOCATION_FIELDS) for item in service("list_locations")(database)]

    @router.get(EXTERNAL_PREFIX + "/categories")
    def external_categories(request: Request):
        with closing(service("connect")()) as database:
            require_token(request, database)
            return [_pick(item, CATEGORY_FIELDS) for item in service("list_categories")(database)]

    return router
