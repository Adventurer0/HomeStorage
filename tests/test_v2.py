"""V2 data safety and AI permission tests; provider HTTP is mocked, not live."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import AsyncMock, patch
import wave

from fastapi.testclient import TestClient
import httpx
from PIL import Image

from app.main import create_app


AI_ENV = {
    "AI_API_KEY": "",
    "AI_MODEL": "",
    "AI_API_BASE_URL": "https://provider.example/v1",
    "AI_TRANSCRIPTION_MODEL": "",
    "AI_TRANSCRIPTION_API_KEY": "",
    "AI_TRANSCRIPTION_BASE_URL": "",
}


class APIHelpers:
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="homestorage-v2-test-")
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        settings = {**AI_ENV, **getattr(self, "ai_settings", {})}
        self.enterContext(patch.dict(os.environ, settings))
        self.app = create_app(data_dir=self.data_dir, password="", timezone="Asia/Shanghai")
        self.client = self.enterContext(TestClient(self.app))
        self.home = next(row for row in self.ok(self.client.get("/api/locations")) if row["name"] == "家")

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def rejected(self, response):
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertLess(response.status_code, 500, response.text)
        self.assertIn("detail", response.json())

    def item(self, name="库存物品", **fields):
        return self.ok(self.client.post("/api/items", json={
            "name": name, "location_id": self.home["id"], **fields,
        }))

    def category(self, name="自定义分类", **fields):
        return self.ok(self.client.post("/api/categories", json={"name": name, **fields}))

    def current(self, item):
        return self.ok(self.client.get(f"/api/items/{item['id']}"))

    def logs(self, item):
        return self.ok(self.client.get("/api/logs", params={"item_id": item["id"]}))

    def action(self, item, action, **fields):
        return self.ok(self.client.post(f"/api/items/{item['id']}/actions", json={"action": action, **fields}))

    def archive(self, item, **fields):
        return self.ok(self.client.post(f"/api/items/{item['id']}/archive", json={"confirmed": True, **fields}))

    def restore(self, item):
        return self.ok(self.client.post(f"/api/items/{item['id']}/restore", json={"confirmed": True}))


class CategoryArchiveTest(APIHelpers, unittest.TestCase):
    def test_custom_category_rename_keeps_identity_and_item_history(self):
        category = self.category("露营用品", sort_order=15)
        stock = self.item("帐篷", category_id=category["id"], quantity=2)
        history = self.logs(stock)
        changed = self.ok(self.client.patch(f"/api/categories/{category['id']}", json={"name": "户外装备"}))
        self.assertEqual(changed["id"], category["id"])
        current = self.current(stock)
        self.assertEqual((current["category_id"], current["category"], current["quantity"]),
                         (category["id"], "户外装备", 2))
        self.assertEqual(self.logs(stock), history)
        filtered = self.ok(self.client.get("/api/items", params={"category_id": category["id"]}))
        self.assertEqual([item["id"] for item in filtered], [stock["id"]])
        self.assertIn("户外装备", self.ok(self.client.get("/api/meta"))["categories"])
        legacy_name = self.item("折叠椅", category="户外装备")
        self.assertEqual(legacy_name["category_id"], category["id"])

    def test_category_names_order_and_invalid_values(self):
        first = self.category("优先用品", sort_order=-10)
        last = self.category("最后用品", sort_order=100)
        ordered = self.ok(self.client.get("/api/categories"))
        ids = [row["id"] for row in ordered]
        self.assertLess(ids.index(first["id"]), ids.index(last["id"]))
        for body in ({"name": "优先用品"}, {"name": "  "}, {"name": "X", "sort_order": True}):
            with self.subTest(body=body):
                self.rejected(self.client.post("/api/categories", json=body))
        self.rejected(self.client.patch(f"/api/categories/{last['id']}", json={"name": first["name"]}))
        self.rejected(self.client.post("/api/items", json={"name": "错误分类", "location_id": self.home["id"], "category_id": 987654}))

    def test_disabled_category_preserves_existing_items_but_rejects_new_assignment(self):
        category = self.category("停用品类")
        stock = self.item("历史物品", category_id=category["id"])
        self.ok(self.client.patch(f"/api/categories/{category['id']}", json={"active": False}))
        self.assertNotIn(category["name"], self.ok(self.client.get("/api/meta"))["categories"])
        self.assertEqual(self.current(stock)["category_id"], category["id"])
        existing = self.ok(self.client.patch(f"/api/items/{stock['id']}", json={
            "name": "历史物品新名称", "category_id": category["id"],
        }))
        self.assertEqual(existing["name"], "历史物品新名称")
        self.rejected(self.client.post("/api/items", json={
            "name": "新物品", "location_id": self.home["id"], "category_id": category["id"],
        }))
        other = self.item("其他现有物品")
        self.rejected(self.client.patch(f"/api/items/{other['id']}", json={"category_id": category["id"]}))
        self.ok(self.client.patch(f"/api/categories/{category['id']}", json={"active": True}))
        self.assertEqual(self.item("再次启用", category_id=category["id"])["category_id"], category["id"])

    def test_category_deletion_rejects_linked_archived_and_terminal_items(self):
        category = self.category("历史档案分类")
        stock = self.item(category_id=category["id"])
        self.archive(stock)
        self.rejected(self.client.delete(f"/api/categories/{category['id']}"))
        self.restore(stock)
        self.action(stock, "discard")
        self.rejected(self.client.delete(f"/api/categories/{category['id']}"))
        unused = self.category("空分类")
        self.ok(self.client.delete(f"/api/categories/{unused['id']}"))
        self.assertNotIn(unused["id"], [row["id"] for row in self.ok(self.client.get("/api/categories"))])

    def test_unknown_legacy_category_still_rejected_without_implicit_creation(self):
        stock = self.item()
        history = self.logs(stock)
        self.rejected(self.client.patch(f"/api/items/{stock['id']}", json={"category": "不存在的分类"}))
        self.rejected(self.client.post("/api/items", json={
            "name": "错误分类", "location_id": self.home["id"], "category": "不存在的分类",
        }))
        self.assertEqual(self.current(stock)["category_id"], stock["category_id"])
        self.assertEqual(self.logs(stock), history)

    def test_deleted_unused_default_category_stays_deleted_after_restart(self):
        category = next(row for row in self.ok(self.client.get("/api/categories")) if row["name"] == "摄影器材")
        self.ok(self.client.delete(f"/api/categories/{category['id']}"))
        with TestClient(create_app(data_dir=self.data_dir, password="", timezone="Asia/Shanghai")) as reopened:
            names = [row["name"] for row in self.ok(reopened.get("/api/categories"))]
            self.assertNotIn("摄影器材", names)

    def test_legacy_category_migration_preserves_custom_names_photos_and_audit(self):
        legacy_dir = self.data_dir / "legacy"
        legacy_dir.mkdir()
        (legacy_dir / "uploads").mkdir()
        image_url = "/uploads/" + "a" * 32 + ".jpg"
        photo = BytesIO()
        Image.new("RGB", (3, 2), (50, 100, 150)).save(photo, format="JPEG")
        (legacy_dir / image_url.lstrip("/")).write_bytes(photo.getvalue())
        timestamp = "2025-01-01T00:00:00.000000Z"
        database_path = legacy_dir / "homestorage.sqlite3"
        with sqlite3.connect(database_path) as db:
            db.executescript("""
                CREATE TABLE locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    code TEXT NOT NULL DEFAULT '', type TEXT NOT NULL,
                    parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT);
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '其他',
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
                    quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>=0),
                    unit TEXT NOT NULL DEFAULT '件', image_url TEXT,
                    brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    stored_date TEXT NOT NULL, purchase_date TEXT, purchase_price REAL,
                    expires_on TEXT, warning_days INTEGER NOT NULL DEFAULT 7,
                    status TEXT NOT NULL DEFAULT 'in_stock', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE logs (
                    id INTEGER PRIMARY KEY, action TEXT NOT NULL,
                    item_id INTEGER REFERENCES items(id) ON DELETE RESTRICT,
                    item_name TEXT NOT NULL DEFAULT '', from_location TEXT,
                    to_location TEXT, quantity_delta INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
                INSERT INTO locations (id,name,type) VALUES(1,'家','home');
                INSERT INTO locations(id,name,type,parent_id) VALUES(2,'旧收纳箱','box',1);
            """)
            db.execute("""INSERT INTO items (
                id,name,category,location_id,quantity,unit,image_url,brand,model,tags,notes,
                stored_date,purchase_date,purchase_price,expires_on,warning_days,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (11, "历史镜头", "用户以前的分类", 2, 3, "个", image_url, "旧品牌", "型号A", "摄影,备用",
                 "保留备注", "2025-01-01", "2024-12-01", 399.5, "2030-01-01", 30, "lent", timestamp, timestamp))
            db.execute("INSERT INTO logs(id,action,item_id,item_name,from_location,to_location,quantity_delta,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (4, "lend", 11, "历史镜头", "家 / 旧收纳箱", "家 / 旧收纳箱", 0, "借给家人", timestamp))
            original_log = db.execute("SELECT * FROM logs").fetchone()
        with TestClient(create_app(data_dir=legacy_dir, password="", timezone="Asia/Shanghai")) as upgraded:
            current = self.ok(upgraded.get("/api/items/11"))
            for key, expected in {
                "name": "历史镜头", "category": "用户以前的分类", "location_id": 2,
                "quantity": 3, "unit": "个", "image_url": image_url, "brand": "旧品牌",
                "model": "型号A", "tags": "摄影,备用", "notes": "保留备注",
                "purchase_price": 399.5, "status": "lent", "created_at": timestamp,
                "updated_at": timestamp, "archived_at": None,
            }.items():
                self.assertEqual(current[key], expected, key)
            category = next(row for row in self.ok(upgraded.get("/api/categories")) if row["name"] == current["category"])
            self.assertEqual(current["category_id"], category["id"])
            self.assertEqual(upgraded.get(image_url).content, photo.getvalue())
            self.assertEqual(tuple(self.ok(upgraded.get("/api/logs", params={"item_id": 11}))[0].values()), original_log)
        with TestClient(create_app(data_dir=legacy_dir, password="", timezone="Asia/Shanghai")) as reopened:
            self.assertEqual(self.ok(reopened.get("/api/items/11"))["category_id"], category["id"])
        with sqlite3.connect(database_path) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_archive_requires_explicit_confirmation_and_preserves_metadata(self):
        stock = self.item("待归档相机", quantity=3, image_url="https://images.example/camera.jpg", notes="家庭备用")
        self.action(stock, "lend", note="借给家人")
        before = self.current(stock)
        before_logs = self.logs(stock)
        for body in ({}, {"confirmed": False}, {"confirmed": "true"}, {"confirmed": 1}):
            self.rejected(self.client.post(f"/api/items/{stock['id']}/archive", json=body))
        self.assertEqual(self.current(stock), before)
        self.assertEqual(self.logs(stock), before_logs)
        archived = self.archive(stock, note="长期不用")
        self.assertTrue(archived["archived_at"])
        for key in ("quantity", "status", "location_id", "image_url", "notes", "created_at", "category_id"):
            self.assertEqual(archived[key], before[key], key)
        record = self.logs(stock)[0]
        self.assertEqual((record["action"], record["quantity_delta"], record["note"]), ("archive", 0, "长期不用"))
        for action in ("return", "restock", "discard"):
            self.rejected(self.client.post(f"/api/items/{stock['id']}/actions", json={"action": action, "quantity": 1} if action == "restock" else {"action": action}))
        self.assertEqual(len(self.logs(stock)), len(before_logs) + 1)

    def test_archive_hidden_from_default_search_counts_expiry_and_export_keeps_it(self):
        stock = self.item("归档食品", quantity=4, expires_on=(date.today() - timedelta(days=2)).isoformat())
        active = self.item("在用食品", quantity=2)
        self.archive(stock)
        self.assertEqual([row["id"] for row in self.ok(self.client.get("/api/items"))], [active["id"]])
        self.assertEqual(self.ok(self.client.get("/api/items", params={"q": "归档食品"})), [])
        self.assertEqual(self.ok(self.client.post("/api/search", json={"query": "归档食品"}))["count"], 0)
        self.assertEqual([row["id"] for row in self.ok(self.client.get("/api/items", params={"archived": "true"}))], [stock["id"]])
        self.assertEqual({row["id"] for row in self.ok(self.client.get("/api/items", params={"archived": "all"}))}, {stock["id"], active["id"]})
        dashboard = self.ok(self.client.get("/api/dashboard"))
        self.assertEqual((dashboard["total_items"], dashboard["total_quantity"], dashboard["expired_count"]), (1, 2, 0))
        self.assertNotIn(stock["id"], [row["id"] for row in dashboard["expiring_items"]])
        home = next(row for row in self.ok(self.client.get("/api/locations")) if row["id"] == self.home["id"])
        self.assertEqual(home["item_count"], 1)
        exported = self.ok(self.client.get("/api/export"))
        self.assertTrue(exported["categories"])
        self.assertEqual({row["id"] for row in exported["items"]}, {stock["id"], active["id"]})
        self.assertTrue(next(row for row in exported["items"] if row["id"] == stock["id"])["archived_at"])

    def test_restore_requires_confirmation_restores_stock_visibility_and_audit(self):
        stock = self.item(quantity=5)
        self.archive(stock)
        self.rejected(self.client.post(f"/api/items/{stock['id']}/restore", json={"confirmed": False}))
        restored = self.restore(stock)
        self.assertIsNone(restored["archived_at"])
        self.assertEqual((restored["quantity"], restored["status"]), (5, "in_stock"))
        self.assertEqual([row["id"] for row in self.ok(self.client.get("/api/items"))], [stock["id"]])
        self.assertEqual(self.logs(stock)[0]["action"], "restore")
        self.assertEqual(self.action(stock, "consume", quantity=1)["quantity"], 4)


class AIPermissionTest(APIHelpers, unittest.TestCase):
    """All provider responses here are synthetic; no provider credentials needed."""

    secret_marker = "provider-key-NEVER-RETURN-THIS"
    ai_settings = {"AI_API_KEY": secret_marker, "AI_MODEL": "mock-chat-model", "AI_TRANSCRIPTION_MODEL": "mock-transcription"}

    @staticmethod
    def completion(*tools, reply=None):
        message = {"role": "assistant", "content": reply}
        if tools:
            message["tool_calls"] = [
                {"id": f"call_{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
                for index, (name, arguments) in enumerate(tools)
            ]
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls" if tools else "stop"}]})

    @contextmanager
    def provider(self, *responses):
        transport = AsyncMock()
        transport.__aenter__.return_value = transport
        transport.post.side_effect = list(responses)
        with patch("app.ai.httpx.AsyncClient", return_value=transport):
            yield transport

    def chat(self, *responses, message="帮我整理物品"):
        with self.provider(*responses):
            return self.ok(self.client.post("/api/ai/chat", json={"message": message}))

    def draft_create(self, **overrides):
        category = next(row for row in self.ok(self.client.get("/api/categories")) if row["name"] == "电子设备")
        payload = {"name": "USB-C 数据线", "category_id": category["id"], "location_id": self.home["id"], "quantity": 2, **overrides}
        result = self.chat(
            self.completion(("list_locations", {}), ("list_categories", {})),
            self.completion(("propose_create", payload)),
            self.completion(reply="请核对物品信息并确认保存。"),
        )
        self.assertEqual(len(result["drafts"]), 1)
        return result["drafts"][0]

    def draft_action(self, stock, name="propose_restock", **arguments):
        args = {"item_ids": [stock["id"]], **arguments} if name == "propose_archive" else {"item_id": stock["id"], **arguments}
        lookups = [("search_items", {"query": stock["name"]})]
        if name == "propose_move":
            lookups.append(("list_locations", {}))
        result = self.chat(
            self.completion(*lookups),
            self.completion((name, args)),
            self.completion(reply="请确认这次操作。"),
        )
        self.assertEqual(len(result["drafts"]), 1)
        return result["drafts"][0]

    def confirm(self, draft, **body):
        return self.client.post(f"/api/ai/drafts/{draft['id']}/confirm", json={"confirmed": True, **body})

    def test_status_reports_capabilities_without_exposing_api_key(self):
        response = self.client.get("/api/ai/status")
        status = self.ok(response)
        self.assertTrue(status["enabled"])
        self.assertTrue(status["voice_enabled"])
        self.assertEqual(status["model"], "mock-chat-model")
        self.assertEqual(status["timezone"], "Asia/Shanghai")
        self.assertNotIn(self.secret_marker, response.text)

    def test_ai_search_returns_actual_locations_and_archived_inventory_only_when_requested(self):
        stock = self.item("USB-C 数据线", quantity=4)
        retired = self.item("USB-C 旧线", quantity=1)
        self.archive(retired)
        before_logs = self.ok(self.client.get("/api/logs"))
        result = self.chat(self.completion(("search_items", {"query": "USB-C"})), self.completion(reply="这些是找到的线材。"))
        self.assertEqual([row["id"] for row in result["matches"]], [stock["id"]])
        self.assertEqual(result["matches"][0]["location_path"], "家")
        self.assertEqual(result["matches"][0]["quantity"], 4)
        self.assertEqual(result["drafts"], [])
        self.assertEqual(self.ok(self.client.get("/api/logs")), before_logs)
        all_result = self.chat(self.completion(("search_items", {"query": "USB-C", "include_archived": True})), self.completion(reply="包含已归档记录。"))
        self.assertEqual({row["id"] for row in all_result["matches"]}, {stock["id"], retired["id"]})

    def test_create_proposal_is_read_only_until_true_confirmation_and_can_only_run_once(self):
        before = self.ok(self.client.get("/api/logs"))
        draft = self.draft_create()
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        self.assertEqual(self.ok(self.client.get("/api/logs")), before)
        self.rejected(self.confirm(draft, confirmed=False))
        self.rejected(self.confirm(draft, confirmed=1))
        self.rejected(self.confirm(draft, payload={"quantity": 999, "location_id": 999}))
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        response = self.ok(self.confirm(draft))
        self.assertEqual(len(response["items"]), 1)
        created = response["items"][0]
        self.assertEqual((created["name"], created["quantity"], created["location_id"]), ("USB-C 数据线", 2, self.home["id"]))
        self.rejected(self.confirm(draft))
        self.assertEqual(len(self.ok(self.client.get("/api/items"))), 1)
        self.assertEqual(len([row for row in self.logs(created) if row["action"] == "create"]), 1)

    def test_model_claiming_write_complete_is_overridden_until_user_confirmation(self):
        category_id = next(row["id"] for row in self.ok(self.client.get("/api/categories")) if row["name"] == "电子设备")
        false_claim = "我已经保存好了数据线，不需要你再确认。"
        before_logs = self.ok(self.client.get("/api/logs"))
        result = self.chat(
            self.completion(("list_locations", {}), ("list_categories", {})),
            self.completion(("propose_create", {
                "name": "待确认线材", "category_id": category_id, "location_id": self.home["id"], "quantity": 2,
            })),
            self.completion(reply=false_claim),
        )
        self.assertEqual(len(result["drafts"]), 1)
        self.assertNotIn(false_claim, result["reply"])
        self.assertIn("尚未", result["reply"])
        self.assertIn("确认", result["reply"])
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        self.assertEqual(self.ok(self.client.get("/api/logs")), before_logs)

    def test_model_inventing_location_after_empty_search_is_overridden_by_database_result(self):
        real_item = self.item("真实相机")
        invented_location = "卧室秘密柜第七层"
        before_logs = self.ok(self.client.get("/api/logs"))
        result = self.chat(
            self.completion(("search_items", {"query": "数据库不存在的数据线"})),
            self.completion(reply=f"你的数据线在{invented_location}，我已经找到了。"),
        )
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["drafts"], [])
        self.assertNotIn(invented_location, result["reply"])
        self.assertIn("没有找到", result["reply"])
        self.assertEqual(self.current(real_item)["location_path"], "家")
        self.assertEqual(self.ok(self.client.get("/api/logs")), before_logs)

    def test_restock_confirmation_is_atomic_under_simultaneous_requests(self):
        stock = self.item("电池", quantity=2)
        draft = self.draft_action(stock, quantity=3)
        self.assertEqual(self.current(stock)["quantity"], 2)
        start = Barrier(2)

        def confirm():
            start.wait(timeout=10)
            return self.confirm(draft)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(sum(200 <= response.status_code < 300 for response in responses), 1, [response.text for response in responses])
        self.assertEqual(sum(400 <= response.status_code < 500 for response in responses), 1, [response.text for response in responses])
        self.assertEqual(self.current(stock)["quantity"], 5)
        self.assertEqual(len([row for row in self.logs(stock) if row["action"] == "restock"]), 1)

    def test_move_proposal_waits_for_confirmation_and_uses_real_location(self):
        destination = self.ok(self.client.post("/api/locations", json={"name": "线材箱", "type": "box", "parent_id": self.home["id"]}))
        stock = self.item("连接线")
        draft = self.draft_action(stock, name="propose_move", location_id=destination["id"])
        self.assertEqual(self.current(stock)["location_id"], self.home["id"])
        result = self.ok(self.confirm(draft))
        self.assertEqual(result["items"][0]["location_path"], "家 / 线材箱")
        self.assertEqual(self.logs(stock)[0]["action"], "move")

    def test_archive_ai_draft_identifies_exact_items_and_never_archives_automatically(self):
        stock = self.item("不用的线材", quantity=3, notes="需要时可恢复")
        draft = self.draft_action(stock, name="propose_archive", note="长期不用")
        self.assertEqual([row["id"] for row in draft["items"]], [stock["id"]])
        self.assertIsNone(self.current(stock)["archived_at"])
        self.rejected(self.confirm(draft, confirmed=False))
        result = self.ok(self.confirm(draft))
        archived = result["items"][0]
        self.assertTrue(archived["archived_at"])
        self.assertEqual((archived["quantity"], archived["notes"]), (3, "需要时可恢复"))
        self.assertEqual(self.logs(stock)[0]["action"], "archive")

    def test_draft_is_bound_to_browser_and_cancel_blocks_owner_confirmation(self):
        draft = self.draft_create()
        with TestClient(self.app) as other:
            self.rejected(other.post(f"/api/ai/drafts/{draft['id']}/confirm", json={"confirmed": True}))
            self.rejected(other.post(f"/api/ai/drafts/{draft['id']}/cancel"))
        self.assertEqual(self.ok(self.client.post(f"/api/ai/drafts/{draft['id']}/cancel")), {"ok": True})
        self.rejected(self.confirm(draft))
        self.assertEqual(self.ok(self.client.get("/api/items")), [])

    def test_stale_target_rejects_confirmation_without_partial_mutation(self):
        stock = self.item("待补货电池", quantity=2)
        draft = self.draft_action(stock, quantity=3)
        self.ok(self.client.patch(f"/api/items/{stock['id']}", json={"notes": "另一人已编辑"}))
        before_logs = self.logs(stock)
        self.rejected(self.confirm(draft))
        self.assertEqual(self.current(stock)["quantity"], 2)
        self.assertEqual(self.logs(stock), before_logs)

    def test_batch_archive_stale_member_does_not_partially_archive_other_members(self):
        first = self.item("批量线材一")
        second = self.item("批量线材二")
        result = self.chat(
            self.completion(("search_items", {"query": "批量线材"})),
            self.completion(("propose_archive", {"item_ids": [first["id"], second["id"]]})),
            self.completion(reply="请确认归档这两件物品。"),
        )
        draft = result["drafts"][0]
        self.assertEqual({row["id"] for row in draft["items"]}, {first["id"], second["id"]})
        self.ok(self.client.patch(f"/api/items/{second['id']}", json={"notes": "已变化"}))
        before = self.ok(self.client.get("/api/logs"))
        self.rejected(self.confirm(draft))
        self.assertIsNone(self.current(first)["archived_at"])
        self.assertIsNone(self.current(second)["archived_at"])
        self.assertEqual(self.ok(self.client.get("/api/logs")), before)

    def test_authenticated_sessions_cannot_confirm_each_others_drafts(self):
        password = "local-test-password"
        authenticated_app = create_app(data_dir=self.data_dir / "authenticated", password=password, timezone="Asia/Shanghai")
        with TestClient(authenticated_app) as owner, TestClient(authenticated_app) as other:
            self.ok(owner.post("/api/login", json={"password": password}))
            self.ok(other.post("/api/login", json={"password": password}))
            category_id = next(row["id"] for row in self.ok(owner.get("/api/categories")) if row["name"] == "电子设备")
            with self.provider(
                self.completion(("list_locations", {}), ("list_categories", {})),
                self.completion(("propose_create", {"name": "独立会话物品", "category_id": category_id, "location_id": 1})),
                self.completion(reply="请确认保存。"),
            ):
                result = self.ok(owner.post("/api/ai/chat", json={"message": "录入一个物品"}))
            draft = result["drafts"][0]
            path = f"/api/ai/drafts/{draft['id']}/confirm"
            self.rejected(other.post(path, json={"confirmed": True}))
            self.assertEqual(self.ok(owner.get("/api/items")), [])
            self.ok(owner.post(path, json={"confirmed": True}))
            self.assertEqual(len(self.ok(owner.get("/api/items"))), 1)
            with sqlite3.connect(self.data_dir / "authenticated" / "homestorage.sqlite3") as db:
                row = db.execute("SELECT actor,payload FROM ai_drafts WHERE id=?", (draft["id"],)).fetchone()
                self.assertEqual(len(row[0]), 64)
                self.assertNotIn(owner.cookies.get("homestorage_session"), row[0] + row[1])

    def test_expired_draft_is_rejected_and_cannot_write(self):
        draft = self.draft_create()
        with sqlite3.connect(self.data_dir / "homestorage.sqlite3") as db:
            db.execute("UPDATE ai_drafts SET expires_at=0 WHERE id=?", (draft["id"],))
        self.rejected(self.confirm(draft))
        self.assertEqual(self.ok(self.client.get("/api/items")), [])

    def test_category_disabled_after_proposal_rejects_confirmation(self):
        draft = self.draft_create()
        category_id = next(row["id"] for row in self.ok(self.client.get("/api/categories")) if row["name"] == "电子设备")
        self.ok(self.client.patch(f"/api/categories/{category_id}", json={"active": False}))
        self.rejected(self.confirm(draft))
        self.assertEqual(self.ok(self.client.get("/api/items")), [])

    def test_existing_items_in_disabled_categories_allow_confirmed_ai_stock_operations(self):
        destination = self.ok(self.client.post("/api/locations", json={"name": "旧物箱", "type": "box", "parent_id": self.home["id"]}))
        for name in ("propose_restock", "propose_move", "propose_archive"):
            with self.subTest(action=name):
                category = self.category(f"旧分类-{name}")
                stock = self.item(f"旧分类物品-{name}", category_id=category["id"])
                self.ok(self.client.patch(f"/api/categories/{category['id']}", json={"active": False}))
                fields = {"quantity": 2} if name == "propose_restock" else {"location_id": destination["id"]} if name == "propose_move" else {}
                draft = self.draft_action(stock, name=name, **fields)
                self.ok(self.confirm(draft))
                current = self.current(stock)
                self.assertEqual(current["category_id"], category["id"])
                if name == "propose_restock":
                    self.assertEqual(current["quantity"], 3)
                elif name == "propose_move":
                    self.assertEqual(current["location_id"], destination["id"])
                else:
                    self.assertTrue(current["archived_at"])

    def test_unknown_sql_tool_and_unsupported_write_fields_cannot_execute(self):
        stock = self.item("安全物品", quantity=4)
        before = self.current(stock)
        for name, payload in (("execute_sql", {"sql": "DELETE FROM items"}),
                              ("propose_restock", {"item_id": stock["id"], "quantity": 8, "command": "rm -rf /app"})):
            with self.subTest(tool=name), self.provider(
                self.completion(("search_items", {"query": stock["name"]})),
                self.completion((name, payload)), self.completion(reply="已处理"),
            ):
                response = self.client.post("/api/ai/chat", json={"message": "试图绕过权限"})
                if response.status_code < 300:
                    self.assertEqual(response.json()["drafts"], [])
                else:
                    self.assertIn(response.status_code, {400, 422, 502})
                self.assertEqual(self.current(stock), before)
        with sqlite3.connect(self.data_dir / "homestorage.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_drafts").fetchone()[0], 0)

    def test_model_cannot_propose_invented_or_unresolved_database_ids(self):
        stock = self.item("真实物品")
        with self.provider(self.completion(("propose_archive", {"item_ids": [stock["id"]]})), self.completion(reply="请确认")):
            response = self.client.post("/api/ai/chat", json={"message": "归档某物"})
            if response.status_code < 300:
                self.assertEqual(response.json()["drafts"], [])
            else:
                self.assertIn(response.status_code, {400, 422, 502})
        with self.provider(
            self.completion(("list_locations", {}), ("list_categories", {})),
            self.completion(("propose_create", {"name": "虚构位置物品", "category_id": 987654, "location_id": 987654})),
            self.completion(reply="请确认"),
        ):
            response = self.client.post("/api/ai/chat", json={"message": "存入未知位置"})
            if response.status_code < 300:
                self.assertEqual(response.json()["drafts"], [])
            else:
                self.assertIn(response.status_code, {400, 422, 502})
        self.assertIsNone(self.current(stock)["archived_at"])
        self.assertEqual(len(self.ok(self.client.get("/api/items"))), 1)

    def test_provider_failures_do_not_return_provider_secret_or_raw_error(self):
        raw_error = "upstream-sensitive-response-marker"
        with self.provider(httpx.Response(401, json={"error": f"{raw_error}: {self.secret_marker}"})):
            response = self.client.post("/api/ai/chat", json={"message": "查询物品"})
            self.assertIn(response.status_code, {502, 503})
            self.assertNotIn(raw_error, response.text)
            self.assertNotIn(self.secret_marker, response.text)
        with self.provider(httpx.ConnectError(f"Cannot connect: {self.secret_marker}")):
            response = self.client.post("/api/ai/chat", json={"message": "查询物品"})
            self.assertIn(response.status_code, {502, 503, 504})
            self.assertNotIn(self.secret_marker, response.text)

    def test_voice_transcription_is_review_only_and_audio_is_not_persisted(self):
        audio = BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 160)
        before_files = {path.relative_to(self.data_dir) for path in self.data_dir.rglob("*") if path.is_file()}
        with self.provider(httpx.Response(200, json={"text": "我把两根线放在家里"})) as provider:
            response = self.client.post("/api/ai/transcribe", files={"file": ("recording.wav", audio.getvalue(), "audio/wav")})
            self.assertEqual(self.ok(response), {"text": "我把两根线放在家里"})
            self.assertEqual(provider.post.await_count, 1)
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        self.assertEqual(before_files, {path.relative_to(self.data_dir) for path in self.data_dir.rglob("*") if path.is_file()})

    def test_oversized_voice_upload_and_user_input_rejected_before_provider_request(self):
        with self.provider() as provider:
            self.rejected(self.client.post("/api/ai/chat", json={"message": "大" * 20001}))
            self.rejected(self.client.post("/api/ai/transcribe", files={"file": ("too-big.wav", bytes(10 * 1024 * 1024 + 1), "audio/wav")}))
            provider.post.assert_not_awaited()


class AIDisabledTest(APIHelpers, unittest.TestCase):
    def test_missing_provider_configuration_disables_ai_without_breaking_inventory(self):
        status = self.ok(self.client.get("/api/ai/status"))
        self.assertFalse(status["enabled"])
        self.assertFalse(status["voice_enabled"])
        stock = self.item("无AI照常使用")
        self.assertEqual(self.current(stock)["name"], "无AI照常使用")
        self.assertEqual(self.client.post("/api/ai/chat", json={"message": "查询"}).status_code, 503)
        self.assertEqual(self.client.post("/api/ai/transcribe", files={"file": ("voice.wav", b"voice", "audio/wav")}).status_code, 503)


if __name__ == "__main__":
    unittest.main()
