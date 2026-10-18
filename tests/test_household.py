"""Household permissions, identity audit and account/session revocation."""

from contextlib import ExitStack
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from app.security import password_matches


class HouseholdTest(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.stack.enter_context(TemporaryDirectory(prefix="hs-household-")))
        self.password = "fixture-bootstrap-password"
        self.app = create_app(data_dir=self.root, password=self.password)
        self.admin = self.stack.enter_context(TestClient(self.app))
        self.admin_user = self.ok(self.admin.post("/api/login", json={"password": self.password}))["user"]
        self.home = self.ok(self.admin.get("/api/locations"))[0]["id"]

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def make_user(self, username, role="member"):
        return self.ok(self.admin.post("/api/users", json={
            "username": username, "display_name": username + " 家人", "password": "fixture-member-password", "role": role,
        }))

    def client_for(self, username):
        client = self.stack.enter_context(TestClient(self.app))
        self.ok(client.post("/api/login", json={"username": username, "password": "fixture-member-password"}))
        return client

    def test_bootstrap_hash_and_legacy_password_only_login(self):
        self.assertEqual(self.admin_user["username"], "admin")
        self.assertEqual(self.admin_user["role"], "admin")
        with sqlite3.connect(self.root / "homestorage.sqlite3") as db:
            encoded, = db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
        self.assertNotIn(self.password, encoded)
        self.assertTrue(password_matches(self.password, encoded))
        self.assertEqual(self.ok(self.admin.get("/api/account")), self.admin_user)
        response = self.ok(self.admin.get("/api/users"))
        self.assertNotIn("password_hash", str(response))
        self.assertNotIn(self.password, str(response))

    def test_member_can_write_and_audit_records_distinct_household_actor(self):
        member = self.make_user("alice")
        client = self.client_for("alice")
        item = self.ok(client.post("/api/items", json={"name": "家庭共享茶杯", "location_id": self.home}))
        self.assertEqual(self.ok(self.admin.get(f"/api/items/{item['id']}"))["name"], "家庭共享茶杯")
        log = self.ok(self.admin.get("/api/logs", params={"item_id": item["id"]}))[0]
        self.assertEqual(log["actor_user_id"], member["id"])
        self.assertEqual(log["actor_name"], "alice 家人")
        for path in ("/api/users", "/api/settings/ai", "/api/settings/updates", "/api/backups", "/api/organize", "/api/integrations"):
            self.assertEqual(client.get(path).status_code, 403, path)

    def test_viewer_reads_searches_and_queries_ai_but_cannot_write_or_confirm(self):
        self.make_user("reader", "viewer")
        client = self.client_for("reader")
        self.ok(client.get("/api/items"))
        self.ok(client.post("/api/search", json={"query": "茶杯"}))
        # Disabled provider is a service error; permission checks let queries reach it.
        self.assertEqual(client.post("/api/ai/chat", json={"message": "茶杯在哪里"}).status_code, 503)
        for path, payload in (("/api/items", {"name": "禁止新增", "location_id": self.home}),
                              ("/api/locations", {"name": "禁止位置"}),
                              ("/api/categories", {"name": "禁止分类"}),
                              ("/api/ai/drafts/unknown/confirm", {"confirmed": True}),
                              ("/api/uploads", {})):
            self.assertEqual(client.post(path, json=payload).status_code, 403, path)

    def test_last_active_admin_cannot_be_demoted_or_disabled(self):
        target = f"/api/users/{self.admin_user['id']}"
        self.assertEqual(self.admin.patch(target, json={"role": "member"}).status_code, 409)
        self.assertEqual(self.admin.patch(target, json={"active": False}).status_code, 409)
        self.make_user("second", "admin")
        second = self.client_for("second")
        self.ok(self.admin.patch(target, json={"role": "member"}))
        self.assertEqual(self.admin.get("/api/items").status_code, 401)
        self.ok(self.admin.post("/api/login", json={"password": self.password}))
        self.assertEqual(self.admin.get("/api/users").status_code, 403)
        self.assertEqual(self.ok(second.get("/api/users"))[0]["role"], "member")

    def test_disable_reenable_and_password_reset_revoke_all_existing_sessions(self):
        user = self.make_user("revoked")
        first = self.client_for("revoked")
        second = self.client_for("revoked")
        old_token = first.cookies.get("homestorage_session")
        self.ok(self.admin.patch(f"/api/users/{user['id']}", json={"active": False}))
        self.assertEqual(first.get("/api/items").status_code, 401)
        self.assertEqual(second.get("/api/items").status_code, 401)
        self.assertEqual(first.post("/api/login", json={"username": "revoked", "password": "fixture-member-password"}).status_code, 401)
        self.ok(self.admin.patch(f"/api/users/{user['id']}", json={"active": True}))
        first.cookies.set("homestorage_session", old_token)
        self.assertEqual(first.get("/api/items").status_code, 401)
        self.ok(first.post("/api/login", json={"username": "revoked", "password": "fixture-member-password"}))
        self.ok(self.admin.patch(f"/api/users/{user['id']}", json={"password": "new-fixture-password"}))
        self.assertEqual(first.get("/api/items").status_code, 401)
        self.assertEqual(first.post("/api/login", json={"username": "revoked", "password": "fixture-member-password"}).status_code, 401)
        self.ok(first.post("/api/login", json={"username": "revoked", "password": "new-fixture-password"}))

    def test_self_password_change_requires_current_password_and_reauthentication(self):
        user = self.make_user("self")
        client = self.client_for("self")
        self.assertEqual(client.post("/api/account/password", json={
            "current_password": "wrong", "password": "updated-password-value",
        }).status_code, 401)
        value = self.ok(client.post("/api/account/password", json={
            "current_password": "fixture-member-password", "password": "updated-password-value",
        }))
        self.assertTrue(value["reauthenticate"])
        self.assertEqual(client.get("/api/items").status_code, 401)
        self.ok(client.post("/api/login", json={"username": "self", "password": "updated-password-value"}))
        logs = self.ok(self.admin.get("/api/logs"))
        self.assertNotIn("updated-password-value", str(logs))
        self.assertNotIn("fixture-member-password", str(logs))
        self.assertEqual(logs[0]["actor_user_id"], user["id"])

    def test_tokens_are_hashed_and_cookie_forgery_and_logout_replay_are_rejected(self):
        token = self.admin.cookies.get("homestorage_session")
        with sqlite3.connect(self.root / "homestorage.sqlite3") as db:
            stored, = db.execute("SELECT token_hash FROM household_sessions LIMIT 1").fetchone()
        self.assertNotEqual(stored, token)
        self.ok(self.admin.post("/api/logout"))
        self.admin.cookies.set("homestorage_session", token)
        self.assertEqual(self.admin.get("/api/items").status_code, 401)
        self.admin.cookies.clear()
        self.admin.cookies.set("homestorage_session", "hs3." + "A" * 43)
        self.assertEqual(self.admin.get("/api/items").status_code, 401)
        # An external integration token never substitutes for browser login.
        self.assertEqual(self.admin.get("/api/items", headers={"Authorization": "Bearer arbitrary"}).status_code, 401)

    def test_bootstrap_is_only_once_and_session_survives_restart(self):
        token = self.admin.cookies.get("homestorage_session")
        with TestClient(create_app(data_dir=self.root, password="changed-environment-bootstrap")) as restarted:
            restarted.cookies.set("homestorage_session", token)
            self.assertEqual(self.ok(restarted.get("/api/auth"))["user"]["id"], self.admin_user["id"])
            self.ok(restarted.post("/api/login", json={"password": self.password}))
            self.assertEqual(restarted.post("/api/login", json={"password": "changed-environment-bootstrap"}).status_code, 401)
        with sqlite3.connect(self.root / "homestorage.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_empty_development_password_never_creates_a_real_enabled_account(self):
        data = self.root / "open-dev"
        with TestClient(create_app(data_dir=data, password="")) as client:
            self.assertIsNone(self.ok(client.get("/api/auth"))["user"]["id"])
            self.ok(client.post("/api/items", json={"name": "本地开发物品", "location_id": 1}))
            self.assertEqual(client.get("/api/users").status_code, 403)
        with sqlite3.connect(data / "homestorage.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        with TestClient(create_app(data_dir=data, password="fresh-admin-password")) as client:
            self.assertEqual(client.post("/api/login", json={"password": ""}).status_code, 401)
            self.ok(client.post("/api/login", json={"password": "fresh-admin-password"}))

    def test_username_uniqueness_and_invalid_secret_inputs_do_not_leak(self):
        self.make_user("alice")
        self.assertEqual(self.admin.post("/api/users", json={
            "username": "ALICE", "password": "different-long-password",
        }).status_code, 409)
        for body in ({"username": "space name", "password": "secret-test-value"},
                     {"username": "safe", "password": "short"},
                     {"username": "safe", "password": "secret-test-value", "role": "root"}):
            response = self.admin.post("/api/users", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(body["password"], response.text)
        self.assertEqual(self.admin.patch("/api/users/1", json={"active": None}).status_code, 422)

    def test_failed_login_rate_limit_and_same_origin_are_enforced(self):
        client = self.stack.enter_context(TestClient(self.app))
        for _ in range(10):
            self.assertEqual(client.post("/api/login", json={"password": "wrong-password"}).status_code, 401)
        self.assertEqual(client.post("/api/login", json={"password": "wrong-password"}).status_code, 429)
        self.assertEqual(self.admin.post("/api/users", json={"username": "x", "password": "safe-password"},
                                         headers={"Origin": "https://foreign.example"}).status_code, 403)

    def test_barcode_is_searchable_but_duplicates_require_scan_disambiguation(self):
        for name in ("牛奶A", "牛奶B"):
            self.ok(self.admin.post("/api/items", json={"name": name, "location_id": self.home, "barcode": "6901234567892"}))
        found = self.ok(self.admin.get("/api/items", params={"q": "6901234567892"}))
        self.assertEqual({item["name"] for item in found}, {"牛奶A", "牛奶B"})
        for barcode in ("bad\ncode", "\nleading", "bad\x00code", "bad\u200bcode", "x" * 129):
            response = self.admin.post("/api/items", json={"name": "无效条码", "location_id": self.home, "barcode": barcode})
            self.assertEqual(response.status_code, 422)
        item = found[0]
        self.assertEqual(self.admin.patch(f"/api/items/{item['id']}", json={"barcode": "bad\ncode"}).status_code, 422)
        self.ok(self.admin.patch(f"/api/items/{item['id']}", json={"barcode": "OTHER-001"}))
        self.assertEqual(self.ok(self.admin.get("/api/items", params={"q": "OTHER-001"}))[0]["id"], item["id"])


if __name__ == "__main__":
    unittest.main()
