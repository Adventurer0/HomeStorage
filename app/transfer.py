"""Confirmed, idempotent scanning and bounded CSV interchange.

Scanned URLs are parsed as local identifiers; they are never fetched. CSVs only
create records and cannot overwrite an existing ID or supply an upload path.
"""

import csv
import hashlib
import io
import json
import math
import re
import secrets
import time
import unicodedata
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .db import transaction


MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_ROWS = 5000
PREVIEW_SECONDS = 15 * 60
POSITIVE_ID = Annotated[StrictInt, Field(gt=0)]
POSITIVE_QUANTITY = Annotated[StrictInt, Field(gt=0, le=1_000_000_000)]
CSV_COLUMNS = (
    "name", "category", "location_path", "quantity", "unit", "barcode", "brand", "model",
    "tags", "notes", "stored_date", "purchase_date", "purchase_price", "expires_on",
    "warning_days", "status", "archived_at",
)
EXPORT_COLUMNS = ("id", *CSV_COLUMNS, "image_url")
TEXT_LIMITS = {"name": 200, "unit": 30, "barcode": 128, "brand": 120, "model": 120,
               "tags": 1000, "notes": 10000}
STATUSES = {"in_stock", "in_use", "lent", "depleted", "discarded", "lost"}


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ScanCommit(StrictInput):
    code: str = Field(min_length=1, max_length=2048)
    item_id: POSITIVE_ID
    operation: Literal["inbound", "outbound", "take_out", "return"]
    quantity: POSITIVE_QUANTITY = 1
    request_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    confirmed: StrictBool


class Confirmation(StrictInput):
    confirmed: StrictBool


def initialize_transfer(connect):
    with closing(connect()) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS scan_commits (
            actor TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(actor,request_id)
        );
        CREATE TABLE IF NOT EXISTS csv_previews (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, created_at REAL NOT NULL,
            payload_json TEXT NOT NULL, used_at REAL
        );
        """)


def _fail(message, status=422):
    raise HTTPException(status, message)


def _actor(user):
    if not user:
        _fail("请先登录", 401)
    return str(user.get("id", user.get("username", "household")))


def _qr_identifier(code):
    """Recognize our root-page links, without making a request to their host."""
    try:
        parsed = urlsplit(code)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.fragment:
            return None
        values = parse_qs(parsed.query, strict_parsing=True)
        keys = set(values)
        if keys <= {"item", "scan"} and "item" in keys:
            kind, value = "item", values["item"]
            if "scan" in values and values["scan"] != ["1"]:
                return None
        elif keys == {"location"}:
            kind, value = "location", values["location"]
        else:
            return None
        if len(value) == 1 and re.fullmatch(r"[1-9][0-9]{0,17}", value[0]):
            return kind, int(value[0])
    except (ValueError, UnicodeError):
        pass
    return None


def _origin(value):
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or any(char.isspace() for char in value)):
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return None


def _safe_cell(value, *, barcode=False):
    if value is None:
        return ""
    if isinstance(value, str) and value:
        # Prefix spreadsheet formula markers and preserve leading-zero barcodes.
        # A literal leading apostrophe is doubled, so import can reverse it.
        if (value.startswith("'") or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                or value[0] in "\t\r\n" or (barcode and value.isdecimal())):
            return "'" + value
    return value


def _unescape_cell(value, *, barcode=False):
    if value.startswith("'"):
        remaining = value[1:]
        if (remaining.startswith("'") or remaining.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                or remaining.startswith(("\t", "\r", "\n")) or (barcode and remaining.isdecimal())):
            return remaining
    return value


def _csv_response(columns, rows, filename):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_safe_cell(row.get(key), barcode=key == "barcode") for key in columns])
    return Response(b"\xef\xbb\xbf" + output.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _snapshot(categories, locations):
    return {
        "categories": {str(row["id"]): {"name": row["name"], "active": bool(row["active"])} for row in categories},
        "locations": {str(row["id"]): {key: row.get(key) for key in ("name", "path", "parent_id", "code", "type")}
                      for row in locations},
    }


def _integer(raw, default, maximum=1_000_000_000):
    if not raw:
        return default
    if not re.fullmatch(r"[0-9]{1,10}", raw) or int(raw) > maximum:
        raise ValueError("必须为范围内的非负整数")
    return int(raw)


def _csv_payload(row, categories, locations):
    """Validate deterministic business fields before any DB mutation."""
    def error(field, message):
        raise ValueError(field, message)

    payload = {}
    for field, limit in TEXT_LIMITS.items():
        value = _unescape_cell(row.get(field, ""), barcode=field == "barcode").strip()
        if not value and field in {"name", "unit"}:
            if field == "unit":
                value = "件"
            else:
                error(field, "物品名称不能为空")
        if len(value) > limit or "\x00" in value:
            error(field, f"长度不能超过 {limit} 个字符，且不能含 NUL 字符")
        if field == "barcode" and any(unicodedata.category(char).startswith("C") for char in value):
            error(field, "条码不能包含控制字符或不可见格式字符")
        payload[field] = value
    category_name = _unescape_cell(row.get("category", "")).strip() or "其他"
    category = next((c for c in categories if c["name"].casefold() == category_name.casefold() and c["active"]), None)
    if not category:
        error("category", f"分类“{category_name}”不存在或已停用，请先创建或启用")
    path = _unescape_cell(row.get("location_path", "")).strip()
    matching = [location for location in locations if location["path"] == path]
    if len(matching) != 1:
        error("location_path", "位置完整路径不存在或不唯一，请填写位置页面中的完整路径")
    payload.update(category_id=category["id"], category=category["name"], location_id=matching[0]["id"])
    for field, default, maximum in (("quantity", 1, 1_000_000_000), ("warning_days", 7, 3650)):
        try:
            payload[field] = _integer(row.get(field, "").strip(), default, maximum)
        except ValueError:
            error(field, f"必须为 0 至 {maximum} 的整数")
    for field in ("stored_date", "purchase_date", "expires_on"):
        raw = row.get(field, "").strip()
        if not raw:
            payload[field] = None
            continue
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                raise ValueError()
            payload[field] = date.fromisoformat(raw).isoformat()
        except ValueError:
            error(field, "日期必须是有效的 YYYY-MM-DD 日期")
    raw_price = row.get("purchase_price", "").strip()
    payload["purchase_price"] = None
    if raw_price:
        try:
            value = float(raw_price)
            if not math.isfinite(value) or value < 0:
                raise ValueError()
            payload["purchase_price"] = value
        except ValueError:
            error("purchase_price", "价格必须为有限的非负数字")
    status = row.get("status", "").strip() or ("in_stock" if payload["quantity"] else "depleted")
    if status not in STATUSES:
        error("status", "状态须为 in_stock、in_use、lent、depleted、discarded 或 lost")
    if status in {"depleted", "discarded"} and payload["quantity"] != 0:
        error("quantity", "已用完或已丢弃的物品数量必须为 0")
    if status in {"in_stock", "in_use", "lent"} and payload["quantity"] == 0:
        error("quantity", "在库、使用中或借出的物品数量必须大于 0")
    archived_at = row.get("archived_at", "").strip() or None
    if archived_at:
        try:
            timestamp = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError()
            archived_at = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            error("archived_at", "归档时间必须为含时区的 ISO 日期时间；空值表示未归档")
    return payload, status, archived_at, matching[0]["path"]


def build_transfer_router(services):
    router = APIRouter()

    def lookup(db, code):
        identifier = _qr_identifier(code)
        if identifier:
            kind, row_id = identifier
            if kind == "location":
                location = next((dict(row) for row in services["list_locations"](db) if row["id"] == row_id), None)
                if not location:
                    _fail("此位置二维码对应的位置已不存在", 404)
                return {"kind": "location", "code": code, "location": location}
            return {"kind": "items", "code": code, "items": [services["read_item"](db, row_id)]}
        rows = db.execute("SELECT id FROM items WHERE barcode=? ORDER BY id", (code,)).fetchall()
        return {"kind": "items", "code": code, "items": [services["read_item"](db, row["id"]) for row in rows]}

    @router.get("/api/scan/lookup")
    def scan_lookup(request: Request, code: str = Query(min_length=1, max_length=2048)):
        services["current_user"](request)
        with closing(services["connect"]()) as db:
            return lookup(db, code.strip())

    @router.post("/api/scan/commit")
    def scan_commit(body: ScanCommit, request: Request):
        actor = _actor(services["require_write"](request))
        if body.confirmed is not True:
            _fail("请检查物品和操作后明确确认")
        digest = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id", "confirmed"}),
                                          sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with closing(services["connect"]()) as db, transaction(db):
            existing = db.execute("SELECT * FROM scan_commits WHERE actor=? AND request_id=?", (actor, body.request_id)).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    _fail("此扫码请求编号已用于其他操作，请重新扫描", 409)
                return {"item": json.loads(existing["result_json"]), "replayed": True}
            found = lookup(db, body.code)
            if found["kind"] != "items" or body.item_id not in {item["id"] for item in found["items"]}:
                _fail("所选物品不属于当前扫码结果，请重新扫描并选择", 409)
            action = {"inbound": "restock", "outbound": "consume", "take_out": "take_out", "return": "return"}[body.operation]
            if action in {"take_out", "return"} and body.quantity != 1:
                _fail("取用或归还改变整条物品记录的状态，数量请填 1")
            payload = {"action": action, "note": "扫码经用户确认"}
            if action in {"restock", "consume"}:
                payload["quantity"] = body.quantity
            item = services["item_action"](db, body.item_id, payload)
            db.execute("INSERT INTO scan_commits(actor,request_id,payload_hash,result_json,created_at) VALUES (?,?,?,?,?)",
                       (actor, body.request_id, digest, json.dumps(item, ensure_ascii=False), time.time()))
            return {"item": item, "replayed": False}

    @router.get("/api/items/{item_id}/qr")
    def item_qr(item_id: int, request: Request, base_url: str = Query(min_length=1, max_length=2048)):
        services["current_user"](request)
        origin = _origin(base_url)
        if not origin:
            _fail("二维码地址必须是有效的 HTTP(S) 网站地址")
        with closing(services["connect"]()) as db:
            services["read_item"](db, item_id)
        output = io.BytesIO()
        qrcode.make(f"{origin}/?item={item_id}&scan=1", image_factory=qrcode.image.svg.SvgPathImage,
                    box_size=8, border=4).save(output)
        return Response(output.getvalue(), media_type="image/svg+xml")

    @router.get("/api/transfer/template.csv")
    def csv_template(request: Request):
        services["require_write"](request)
        return _csv_response(CSV_COLUMNS, [], "homestorage-template.csv")

    @router.get("/api/transfer/export.csv")
    def csv_export(request: Request, include_archived: bool = True):
        services["current_user"](request)
        with closing(services["connect"]()) as db:
            rows = services["all_items"](db, include_archived=include_archived)
        return _csv_response(EXPORT_COLUMNS, rows, "homestorage-inventory.csv")

    @router.post("/api/transfer/import/preview")
    async def csv_preview(request: Request, file: UploadFile = File(...)):
        actor = _actor(services["require_write"](request))
        try:
            content = await file.read(MAX_CSV_BYTES + 1)
        finally:
            await file.close()
        if len(content) > MAX_CSV_BYTES:
            _fail("CSV 文件最大为 10 MB", 413)
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            _fail("请使用 UTF-8 编码的 CSV 文件；可先下载模板")
        if "\x00" in text:
            _fail("CSV 文件含无效的 NUL 字符")
        # csv.field_size_limit is process-global; leave it unchanged. Our input
        # envelope and per-field validators bound parsed content independently.
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            fields = reader.fieldnames
            if not fields or len(fields) != len(set(fields)):
                _fail("CSV 表头为空或含重复列")
            unknown = set(fields) - set(EXPORT_COLUMNS)
            if unknown or not {"name", "location_path"} <= set(fields):
                _fail("CSV 表头不正确：必须含 name、location_path，且只允许模板或导出列")
            rows = []
            for number, row in enumerate(reader, 2):
                if len(rows) >= MAX_CSV_ROWS:
                    _fail("每次最多导入 5000 行，请拆分文件", 413)
                if None in row or any(value is None for value in row.values()):
                    _fail(f"第 {number} 行的列数与表头不同")
                rows.append((number, row))
        except csv.Error:
            _fail("CSV 格式无效或单个单元格过大，请使用模板")
        if not rows:
            _fail("CSV 没有物品数据，请在表头下面填写物品")
        with closing(services["connect"]()) as db, transaction(db):
            categories = [dict(row) for row in services["list_categories"](db)]
            locations = [dict(row) for row in services["list_locations"](db)]
            existing = {(row["barcode"], row["location_id"]) for row in db.execute(
                "SELECT barcode,location_id FROM items WHERE barcode<>''")}
            errors, warnings, entries, display = [], [], [], []
            seen = set(existing)
            for number, row in rows:
                try:
                    payload, status, archived_at, location_path = _csv_payload(row, categories, locations)
                except ValueError as error:
                    field, message = error.args if len(error.args) == 2 else ("row", "数据格式不正确")
                    errors.append({"row": number, "field": field, "message": message})
                    continue
                key = (payload["barcode"], payload["location_id"])
                if payload["barcode"] and key in seen:
                    warnings.append({"row": number, "message": "该条码在此位置已有物品或在 CSV 中重复，本行将跳过"})
                    continue
                if payload["barcode"]:
                    seen.add(key)
                entries.append({"row": number, "payload": payload, "status": status, "archived_at": archived_at})
                if len(display) < 100:
                    display.append({"row": number, "payload": {**payload, "location_path": location_path,
                                                              "status": status, "archived_at": archived_at}})
            preview_id, created_at = secrets.token_urlsafe(24), time.time()
            data = {"entries": entries, "errors": errors, "warnings": warnings,
                    "snapshot": _snapshot(categories, locations)}
            db.execute("DELETE FROM csv_previews WHERE created_at<?", (created_at - PREVIEW_SECONDS * 2,))
            db.execute("INSERT INTO csv_previews(id,actor,created_at,payload_json) VALUES (?,?,?,?)",
                       (preview_id, actor, created_at, json.dumps(data, ensure_ascii=False)))
        return {"id": preview_id, "expires_at": datetime.fromtimestamp(created_at + PREVIEW_SECONDS, timezone.utc).isoformat(),
                "rows_count": len(rows), "valid_count": len(entries), "skipped_count": len(warnings),
                "errors": errors, "warnings": warnings, "items": display,
                "can_confirm": bool(entries) and not errors,
                "notice": "只新建物品，不覆盖既有记录；id 和 image_url 不导入，照片请使用完整备份恢复。"}

    @router.post("/api/transfer/import/{preview_id}/confirm")
    def csv_confirm(preview_id: str, body: Confirmation, request: Request):
        actor = _actor(services["require_write"](request))
        if body.confirmed is not True:
            _fail("请检查预览后明确确认导入")
        if len(preview_id) > 128:
            _fail("导入预览不存在", 404)
        with closing(services["connect"]()) as db, transaction(db):
            preview = db.execute("SELECT * FROM csv_previews WHERE id=? AND actor=?", (preview_id, actor)).fetchone()
            if not preview:
                _fail("导入预览不存在或不属于当前用户", 404)
            if preview["used_at"] is not None:
                _fail("此预览已导入，请勿重复确认", 409)
            if preview["created_at"] + PREVIEW_SECONDS <= time.time():
                _fail("导入预览已过期，请重新上传", 409)
            data = json.loads(preview["payload_json"])
            if data["errors"] or not data["entries"]:
                _fail("请先修正 CSV 错误并重新预览", 409)
            current = _snapshot([dict(r) for r in services["list_categories"](db)],
                                [dict(r) for r in services["list_locations"](db)])
            # Only referenced entities matter. An unrelated added category does
            # not invalidate a reviewed import; rename/move/disable/delete does.
            for entry in data["entries"]:
                payload = entry["payload"]
                for group, key in (("categories", str(payload["category_id"])), ("locations", str(payload["location_id"]))):
                    if current[group].get(key) != data["snapshot"][group].get(key):
                        _fail("预览后位置或分类已变化，请重新上传并确认", 409)
                if payload["barcode"] and db.execute(
                    "SELECT 1 FROM items WHERE barcode=? AND location_id=? LIMIT 1",
                    (payload["barcode"], payload["location_id"]),
                ).fetchone():
                    _fail("预览后已有相同位置和条码的物品，请重新上传预览", 409)
            item_ids = []
            for entry in data["entries"]:
                item = services["create_item"](db, entry["payload"])
                item_ids.append(item["id"])
                if item["status"] != entry["status"] or entry["archived_at"]:
                    db.execute("UPDATE items SET status=?,archived_at=? WHERE id=?",
                               (entry["status"], entry["archived_at"], item["id"]))
                    services["audit"](db, "csv_import_state", item=item,
                                      note=json.dumps({"status": entry["status"], "archived_at": entry["archived_at"]}, ensure_ascii=False))
            services["audit"](db, "csv_import", note=f"用户确认导入 {len(item_ids)} 条新物品；跳过 {len(data['warnings'])} 条重复记录")
            db.execute("UPDATE csv_previews SET used_at=? WHERE id=?", (time.time(), preview_id))
        return {"created_count": len(item_ids), "skipped_count": len(data["warnings"]), "item_ids": item_ids}

    return router
