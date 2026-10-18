"""Cross-module contracts exercised through the assembled application.

Provider responses are synthetic. No real provider credentials, NAS storage or
cloud services are required by these integration regressions.
"""

from contextlib import ExitStack
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from app.main import create_app
from app.version import runtime_version


class V3IntegrationTest(unittest.TestCase):
    password = "fixture-v3-bootstrap-password"
    member_password = "fixture-v3-family-password"

    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.stack.enter_context(TemporaryDirectory(prefix="hs-v3-integration-")))
        self.stack.enter_context(patch.dict(os.environ, {
            "AI_API_BASE_URL": "https://provider.example/v1", "AI_API_KEY": "", "AI_MODEL": "",
            "AI_TRANSCRIPTION_MODEL": "", "AI_TRANSCRIPTION_API_KEY": "", "AI_TRANSCRIPTION_BASE_URL": "",
        }))
        self.app = create_app(data_dir=self.root, password=self.password)
        self.admin = self.client(self.app)
        self.ok(self.admin.post("/api/login", json={"password": self.password}))
        self.item = self.ok(self.admin.post("/api/items", json={
            "name": "家庭测试数据线", "location_id": 1, "quantity": 5, "barcode": "V3-001",
        }))

    def client(self, application=None):
        return self.stack.enter_context(TestClient(application or self.app))

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def family(self, username, role):
        user = self.ok(self.admin.post("/api/users", json={
            "username": username, "display_name": username, "password": self.member_password, "role": role,
        }))
        client = self.client()
        self.ok(client.post("/api/login", json={"username": username, "password": self.member_password}))
        return user, client

    def token(self):
        return self.ok(self.admin.post("/api/integrations/tokens", json={"name": "跨模块只读测试"}))

    def test_member_and_viewer_cannot_reach_new_management_modules(self):
        for role in ("member", "viewer"):
            _, client = self.family(role, role)
            calls = [
                ("GET", "/api/backups", None), ("GET", "/api/backups/settings", None),
                ("GET", "/api/backups/unknown/download", None),
                ("PATCH", "/api/backups/settings", {"auto_backup": True}),
                ("POST", "/api/backups", {"confirmed": True}),
                ("POST", "/api/backups/unknown/upload", {"confirmed": True}),
                ("GET", "/api/organize/batches", None),
                ("POST", "/api/organize/batches", {"confirmed": True}),
                ("POST", "/api/organize/batches/unknown/confirm", {"confirmed": True}),
                ("POST", "/api/organize/batches/unknown/rollback", {"confirmed": True}),
                ("GET", "/api/integrations/connection", None),
                ("GET", "/api/integrations/openapi.json", None),
                ("POST", "/api/integrations/tokens", {"name": "不应创建"}),
            ]
            for method, path, payload in calls:
                with self.subTest(role=role, path=path, method=method):
                    response = client.request(method, path, json=payload)
                    self.assertEqual(response.status_code, 403, response.text)
        # The deny results must precede side effects such as local backup jobs.
        self.assertEqual(self.ok(self.admin.get("/api/backups"))["backups"], [])
        self.assertIsNone(self.ok(self.admin.get("/api/backups"))["active_job"])
        self.assertEqual(self.ok(self.admin.get("/api/integrations/tokens"))["tokens"], [])
        self.assertEqual(self.ok(self.admin.get("/api/organize/batches"))["batches"], [])

    def test_real_scan_csv_and_role_downgrade_share_write_authorization(self):
        member, client = self.family("scanner", "member")
        payload = {"code": "V3-001", "item_id": self.item["id"], "operation": "outbound",
                   "quantity": 1, "request_id": "integration-scan-0001", "confirmed": True}
        scanned = self.ok(client.post("/api/scan/commit", json=payload))
        self.assertEqual(scanned["item"]["quantity"], 4)
        log = self.ok(self.admin.get("/api/logs", params={"item_id": self.item["id"]}))[0]
        self.assertEqual(log["actor_user_id"], member["id"])
        csv_data = "name,location_path,quantity,barcode\r\n导入的收纳袋,家,2,V3-CSV-001\r\n".encode()
        preview = self.ok(client.post("/api/transfer/import/preview", files={
            "file": ("inventory.csv", csv_data, "text/csv"),
        }))
        # Session revocation applies to already-prepared module operations too.
        self.ok(self.admin.patch(f"/api/users/{member['id']}", json={"role": "viewer"}))
        self.assertEqual(client.post(f"/api/transfer/import/{preview['id']}/confirm",
                                     json={"confirmed": True}).status_code, 401)
        self.ok(client.post("/api/login", json={"username": "scanner", "password": self.member_password}))
        self.assertEqual(client.post(f"/api/transfer/import/{preview['id']}/confirm",
                                     json={"confirmed": True}).status_code, 403)
        payload["request_id"] = "integration-scan-0002"
        self.assertEqual(client.post("/api/scan/commit", json=payload).status_code, 403)
        self.assertEqual(client.post("/api/transfer/import/preview", files={
            "file": ("inventory.csv", csv_data, "text/csv"),
        }).status_code, 403)
        self.assertEqual(self.ok(self.admin.get(f"/api/items/{self.item['id']}"))["quantity"], 4)
        self.assertEqual(len(self.ok(self.admin.get("/api/items"))), 1)
        self.assertEqual(client.get("/api/transfer/template.csv").status_code, 403)
        for path in ("/api/transfer/export.csv", "/api/scan/lookup?code=V3-001"):
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_external_credential_does_not_gain_cookie_or_write_scope(self):
        created = self.token()
        bearer = {"Authorization": "Bearer " + created["token"]}
        external = self.client()
        self.assertEqual(external.get("/api/external/v1/items", headers=bearer).status_code, 200)
        for path in ("/api/items", "/api/export", "/api/backups", "/api/settings/ai", "/api/handover"):
            self.assertEqual(external.get(path, headers=bearer).status_code, 401, path)
        self.assertEqual(external.post("/api/items", headers=bearer, json={
            "name": "禁止令牌新增", "location_id": 1,
        }).status_code, 401)
        _, viewer = self.family("tokenviewer", "viewer")
        self.assertEqual(viewer.get("/api/external/v1/items", headers=bearer).status_code, 200)
        self.assertEqual(viewer.post("/api/scan/commit", headers=bearer, json={
            "code": "V3-001", "item_id": self.item["id"], "operation": "outbound", "quantity": 1,
            "request_id": "integration-token-0001", "confirmed": True,
        }).status_code, 403)
        self.assertEqual(viewer.post("/api/integrations/tokens", headers=bearer,
                                     json={"name": "不能提升权限"}).status_code, 403)
        # A logged-in administrator cannot make an invalid integration token
        # pass external validation: the three external routes ignore cookies.
        self.assertEqual(self.admin.get("/api/external/v1/items").status_code, 401)
        self.assertEqual(self.admin.get("/api/external/v1/items", headers={
            "Authorization": "Bearer hs_ro_" + "A" * 43,
        }).status_code, 401)
        self.ok(self.admin.delete(f"/api/integrations/tokens/{created['id']}"))
        self.assertEqual(viewer.get("/api/external/v1/items", headers=bearer).status_code, 401)

    def test_existing_household_remains_private_when_bootstrap_environment_is_removed(self):
        old_session = self.admin.cookies.get("homestorage_session")
        restarted = self.client(create_app(data_dir=self.root, password=""))
        auth = self.ok(restarted.get("/api/auth"))
        self.assertTrue(auth["auth_required"])
        self.assertFalse(auth["authenticated"])
        self.assertEqual(restarted.get("/api/items").status_code, 401)
        self.assertEqual(restarted.get("/api/handover").status_code, 401)
        restarted.cookies.set("homestorage_session", old_session)
        self.assertEqual(self.ok(restarted.get("/api/account"))["username"], "admin")
        self.ok(restarted.get("/api/backups/settings"))
        restarted.cookies.clear()
        self.ok(restarted.post("/api/login", json={"password": self.password}))
        self.ok(restarted.post("/api/account/password", json={
            "current_password": self.password, "password": "replacement-fixture-password",
        }))
        self.assertEqual(self.admin.get("/api/items").status_code, 401)
        self.ok(restarted.post("/api/login", json={"password": "replacement-fixture-password"}))
        self.assertEqual(self.ok(restarted.get("/api/items"))[0]["id"], self.item["id"])

    def test_authenticated_handover_matches_running_release(self):
        anonymous = self.client()
        self.assertEqual(anonymous.get("/api/handover").status_code, 401)
        _, viewer = self.family("handoverreader", "viewer")
        response = viewer.get("/api/handover")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/markdown", response.headers["content-type"])
        self.assertIn(f"homestorage-handover-{runtime_version()}.md", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.text.startswith(f"# HomeStorage {runtime_version()} 交接文档\n"))

    def test_real_browser_and_ai_search_events_are_counted_once(self):
        _, viewer = self.family("searchreader", "viewer")
        # Reading filtered inventory renders results but is not a submitted search.
        self.ok(viewer.get("/api/items", params={"q": "数据线"}))
        before = self.ok(viewer.get("/api/analytics/activity?days=7"))
        self.assertEqual(before["totals"]["searches"], 0)
        self.ok(viewer.post("/api/search", json={"query": "数据线"}))
        self.ok(viewer.post("/api/search", json={"query": "不存在的物品"}))
        self.assertEqual(viewer.post("/api/search", json={"query": " "}).status_code, 422)
        self.ok(self.admin.patch("/api/settings/ai", json={
            "api_base_url": "https://provider.example/v1", "api_key": "fixture-synthetic-provider-key",
            "model": "fixture-synthetic-chat-model",
        }))
        tool = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "integration_search", "type": "function",
            "function": {"name": "search_items", "arguments": json.dumps({"query": "数据线"})},
        }]}
        transport = AsyncMock()
        transport.__aenter__.return_value = transport
        transport.post.side_effect = [
            httpx.Response(200, json={"choices": [{"message": tool}]}),
            httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "已查询库存"}}]}),
        ]
        with patch("app.ai.httpx.AsyncClient", return_value=transport):
            result = self.ok(viewer.post("/api/ai/chat", json={"message": "数据线在哪里？"}))
        self.assertEqual([item["id"] for item in result["matches"]], [self.item["id"]])
        activity = self.ok(viewer.get("/api/analytics/activity?days=7"))
        self.assertEqual(activity["totals"]["searches"], 3)
        self.assertEqual(activity["search_sources"], {"web": 2, "ai": 1})
        self.assertEqual(activity["top_items"], [{
            "id": self.item["id"], "name": self.item["name"], "search_count": 2,
        }])
        self.assertEqual(sum(day["searches"] for day in activity["daily"]), 3)


class V25UpgradeIntegrationTest(unittest.TestCase):
    def test_legacy_database_photos_and_history_survive_first_household_bootstrap(self):
        root = Path(self.enterContext(TemporaryDirectory(prefix="hs-25-upgrade-")))
        (root / "uploads").mkdir()
        image_url = "/uploads/" + "a" * 32 + ".jpg"
        original_image = b"retained-photo-fixture"
        (root / image_url.lstrip("/")).write_bytes(original_image)
        key = bytes(range(32))
        (root / "session.key").write_bytes(key)
        path = root / "homestorage.sqlite3"
        with sqlite3.connect(path) as db:
            # A literal 2.5-shaped fixture has neither household tables nor the
            # new barcode/actor columns. It does include v2 archive/category IDs.
            db.executescript("""
                CREATE TABLE schema_migrations(name TEXT PRIMARY KEY);
                INSERT INTO schema_migrations VALUES('categories_archive_v2');
                CREATE TABLE categories(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,sort_order INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1);
                INSERT INTO categories VALUES(77,'原来的自定义分类',0,1);
                CREATE TABLE locations(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,
                    code TEXT NOT NULL DEFAULT '',type TEXT NOT NULL,
                    parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT);
                INSERT INTO locations VALUES(1,'家','','home',NULL);
                INSERT INTO locations VALUES(8,'原来的储物柜','OLD-008','box',1);
                CREATE TABLE items(id INTEGER PRIMARY KEY,name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '其他',category_id INTEGER REFERENCES categories(id),
                    location_id INTEGER NOT NULL REFERENCES locations(id),quantity INTEGER NOT NULL DEFAULT 1,
                    unit TEXT NOT NULL DEFAULT '件',image_url TEXT,brand TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',tags TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',
                    stored_date TEXT NOT NULL,purchase_date TEXT,purchase_price REAL,expires_on TEXT,
                    warning_days INTEGER NOT NULL DEFAULT 7,status TEXT NOT NULL DEFAULT 'in_stock',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived_at TEXT);
                CREATE TABLE logs(id INTEGER PRIMARY KEY,action TEXT NOT NULL,
                    item_id INTEGER REFERENCES items(id),item_name TEXT NOT NULL DEFAULT '',
                    from_location TEXT,to_location TEXT,quantity_delta INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
                INSERT INTO logs VALUES(27,'lend',11,'旧相机','家 / 原来的储物柜',
                    '家 / 原来的储物柜',0,'旧借出历史','2026-01-01T00:00:00Z');
            """)
            stamp = "2026-01-01T00:00:00Z"
            db.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                11, "旧相机", "原来的自定义分类", 77, 8, 3, "台", image_url, "旧品牌", "旧型号",
                "相机,备用", "原来的备注", "2026-01-01", "2025-12-01", 399.5, None, 7, "lent",
                stamp, stamp, "2026-02-01T00:00:00Z",
            ))
            old_item = db.execute("SELECT * FROM items WHERE id=11").fetchone()
            old_log = db.execute("SELECT * FROM logs WHERE id=27").fetchone()
        password = "fixture-old-shared-password"
        legacy_key = hmac.digest(key, password.encode(), "sha256")
        payload = str(int(time.time()) + 3600) + "." + "b" * 32
        old_cookie = payload + "." + hmac.new(legacy_key, payload.encode(), hashlib.sha256).hexdigest()
        with TestClient(create_app(data_dir=root, password=password)) as client:
            client.cookies.set("homestorage_session", old_cookie)
            self.assertEqual(client.get("/api/items?archived=all").status_code, 401)
            self.assertEqual(client.post("/api/login", json={"password": password}).status_code, 200)
            item = client.get("/api/items/11").json()
            self.assertEqual((item["id"], item["category_id"], item["location_id"], item["status"], item["quantity"]),
                             (11, 77, 8, "lent", 3))
            self.assertEqual(item["barcode"], "")
            self.assertEqual(client.get(image_url).content, original_image)
            self.assertEqual(client.get("/api/users").json()[0]["username"], "admin")
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT * FROM items WHERE id=11").fetchone()[:-1], old_item)
            self.assertEqual(db.execute("SELECT * FROM logs WHERE id=27").fetchone()[:len(old_log)], old_log)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual((root / "session.key").read_bytes(), key)


if __name__ == "__main__":
    unittest.main()
