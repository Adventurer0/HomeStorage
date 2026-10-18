"""Independent tests for the externally shared, fixed read-only API scope."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.db import connect, initialize
from app.analytics import activity, initialize_analytics, record_search
from app.integrations import (
    CATEGORY_FIELDS, EXTERNAL_GET_PATHS, ITEM_FIELDS, LOCATION_FIELDS,
    build_integrations_router, initialize_integrations,
)


class ExternalIntegrationTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix="homestorage-integrations-"))
        self.path = Path(directory) / "homestorage.sqlite3"
        initialize(self.path)
        with closing(connect(self.path)) as database:
            initialize_integrations(database)
        self.clock = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
        self.audits = []
        self.items = [
            {"id": 1, "name": "USB 数据线", "quantity": 3, "unit": "根", "category": "电子设备", "archived_at": None,
             "location_path": "家 / 抽屉", "notes": "短线", "password_hash": "NEVER-EXPOSE-PASSWORD", "api_key": "NEVER-EXPOSE-KEY", "data_dir": "/private/nas/data"},
            {"id": 2, "name": "USB 充电器", "quantity": 2, "archived_at": None, "location_path": "家 / 抽屉"},
            {"id": 3, "name": "已归档 USB", "quantity": 1, "archived_at": "2026-10-16T00:00:00Z"},
            {"id": 4, "name": "创可贴", "quantity": 9, "archived_at": None, "location_path": "家 / 药箱"},
        ]

        def require_admin(request):
            if request.headers.get("x-fixture-role") != "admin":
                raise HTTPException(403, "需要管理员")
            return {"id": 1, "username": "fixture-admin"}

        def all_items(database, include_archived=False):
            return [dict(item) for item in self.items if include_archived or item["archived_at"] is None]

        def find_items(database, query, include_archived=False):
            terms = query.casefold().split()
            return [item for item in all_items(database, include_archived)
                    if all(term in (item["name"] + " " + item.get("location_path", "")).casefold() for term in terms)]

        self.services = SimpleNamespace(
            connect=lambda: connect(self.path), now=lambda: self.clock.isoformat().replace("+00:00", "Z"),
            all_items=all_items, find_items=find_items,
            list_locations=lambda db: [{"id": 1, "name": "家", "parent_id": None, "path": "家", "type": "home", "secret": "NEVER-EXPOSE-LOCATION"}],
            list_categories=lambda db: [{"id": 1, "name": "电子设备", "active": True, "secret": "NEVER-EXPOSE-CATEGORY"}],
            require_admin=require_admin,
            audit=lambda db, action, **kwargs: self.audits.append((action, kwargs)),
        )
        application = FastAPI()
        application.include_router(build_integrations_router(self.services))
        self.client = self.enterContext(TestClient(application))
        self.admin = {"x-fixture-role": "admin"}

    def create_token(self, **body):
        response = self.client.post("/api/integrations/tokens", headers=self.admin, json={"name": "外部助手", **body})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    @staticmethod
    def bearer(token):
        return {"Authorization": "Bearer " + token}

    def test_token_is_256_bit_once_only_and_stored_as_digest(self):
        created = self.create_token()
        token = created["token"]
        self.assertRegex(token, r"^hs_ro_[A-Za-z0-9_-]{43}$")
        with closing(connect(self.path)) as database:
            row = dict(database.execute("SELECT * FROM external_api_tokens").fetchone())
            self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(token, json.dumps(row))
        listed = self.client.get("/api/integrations/tokens", headers=self.admin)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(token, listed.text)
        self.assertNotIn("token_hash", listed.text)
        self.assertEqual(len(listed.json()["tokens"]), 1)
        self.assertEqual(datetime.fromisoformat(created["expires_at"].replace("Z", "+00:00")) - self.clock, timedelta(days=90))
        self.assertNotIn(token, json.dumps(self.audits))

    def test_search_pagination_and_archived_default(self):
        token = self.create_token()["token"]
        response = self.client.get("/api/external/v1/items?q=USB&limit=1&offset=1", headers=self.bearer(token))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"items": [{"id": 2, "name": "USB 充电器", "quantity": 2, "archived_at": None, "location_path": "家 / 抽屉"}], "total": 2, "limit": 1, "offset": 1})
        all_response = self.client.get("/api/external/v1/items", headers=self.bearer(token))
        self.assertEqual(all_response.json()["total"], 3)
        self.assertNotIn(3, [item["id"] for item in all_response.json()["items"]])
        self.assertEqual(all_response.headers["cache-control"], "no-store")

    def test_all_responses_allowlist_inventory_fields_only(self):
        token = self.create_token()["token"]
        for path, fields in (("items", ITEM_FIELDS), ("locations", LOCATION_FIELDS), ("categories", CATEGORY_FIELDS)):
            response = self.client.get("/api/external/v1/" + path, headers=self.bearer(token))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("NEVER-EXPOSE", response.text)
            self.assertNotIn("/private/nas", response.text)
            values = response.json()["items"] if path == "items" else response.json()
            for value in values:
                self.assertLessEqual(set(value), set(fields))

    def test_last_used_is_updated_but_credential_never_returned(self):
        created = self.create_token()
        self.clock += timedelta(hours=1)
        response = self.client.get("/api/external/v1/locations", headers=self.bearer(created["token"]))
        self.assertEqual(response.status_code, 200)
        metadata = self.client.get("/api/integrations/tokens", headers=self.admin).json()["tokens"][0]
        self.assertEqual(metadata["last_used_at"], self.services.now())
        self.assertNotIn("token", metadata)

    def test_explicit_external_queries_contribute_to_search_heatmap_without_inventory_writes(self):
        initialize_analytics(lambda: connect(self.path))
        self.services.record_search = record_search
        token = self.create_token()["token"]
        with closing(connect(self.path)) as database:
            original_inventory = database.execute("SELECT * FROM items").fetchall()
        for query in ("", "?q=", "?q=USB"):
            response = self.client.get("/api/external/v1/items" + query, headers=self.bearer(token))
            self.assertEqual(response.status_code, 200, response.text)
        with closing(connect(self.path)) as database:
            statistics = activity(database)
            events = database.execute("SELECT source,actor_user_id,actor_name FROM search_events").fetchall()
            self.assertEqual(database.execute("SELECT * FROM items").fetchall(), original_inventory)
        self.assertEqual(statistics["totals"]["searches"], 1)
        self.assertEqual(statistics["search_sources"], {"external": 1})
        self.assertEqual([tuple(row) for row in events], [("external", None, "")])

    def test_revoke_immediately_invalidates_token_and_is_idempotent(self):
        created = self.create_token()
        headers = self.bearer(created["token"])
        self.assertEqual(self.client.get("/api/external/v1/items", headers=headers).status_code, 200)
        for _ in range(2):
            response = self.client.delete(f"/api/integrations/tokens/{created['id']}", headers=self.admin)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/external/v1/items", headers=headers).status_code, 401)
        self.assertEqual([entry[0] for entry in self.audits].count("external_token_revoke"), 1)

    def test_expiry_and_invalid_credentials_share_generic_error(self):
        created = self.create_token(expires_days=1)
        self.clock += timedelta(days=1)
        responses = [
            self.client.get("/api/external/v1/items", headers=headers)
            for headers in ({}, self.admin, self.bearer("bad"), self.bearer("hs_ro_" + "A" * 43), self.bearer(created["token"]))
        ]
        self.assertTrue(all(response.status_code == 401 for response in responses))
        self.assertEqual(len({response.text for response in responses}), 1)
        self.assertTrue(all(response.headers["www-authenticate"] == "Bearer" for response in responses))

    def test_management_requires_admin_even_with_valid_read_token(self):
        token = self.create_token()["token"]
        for headers in ({}, {"x-fixture-role": "member"}, self.bearer(token)):
            for path in ("tokens", "connection", "openapi.json"):
                response = self.client.get("/api/integrations/" + path, headers=headers)
                self.assertEqual(response.status_code, 403)
            self.assertEqual(self.client.post("/api/integrations/tokens", headers=headers, json={"name": "no"}).status_code, 403)
            self.assertEqual(self.client.delete("/api/integrations/tokens/1", headers=headers).status_code, 403)

    def test_token_creation_strict_validation(self):
        for body in (
            {"name": ""}, {"name": " "}, {"name": 123}, {"name": "bad\x00name"},
            {"name": "x", "expires_days": 0}, {"name": "x", "expires_days": 367},
            {"name": "x", "expires_days": True}, {"name": "x", "expires_days": "3"},
            {"name": "x", "expires_days": 3.0}, {"name": "x", "scope": "write"},
        ):
            with self.subTest(body=body):
                response = self.client.post("/api/integrations/tokens", headers=self.admin, json=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.create_token(expires_days=366)

    def test_pagination_rejects_invalid_or_unrecognized_parameters(self):
        headers = self.bearer(self.create_token()["token"])
        for query in ("limit=0", "limit=101", "limit=-1", "limit=1.5", "limit=1.0", "limit=true", "offset=-1", "offset=1.0", "offset=1000000001", "scope=write", "include_archived=true", "q=" + "a" * 201):
            with self.subTest(query=query):
                response = self.client.get("/api/external/v1/items?" + query, headers=headers)
                self.assertEqual(response.status_code, 422, response.text)

    def test_only_three_get_endpoints_are_credential_scope(self):
        token = self.create_token()["token"]
        self.assertEqual(EXTERNAL_GET_PATHS, frozenset({"/api/external/v1/items", "/api/external/v1/locations", "/api/external/v1/categories"}))
        for path in sorted(EXTERNAL_GET_PATHS):
            for method in ("post", "patch", "put", "delete"):
                response = getattr(self.client, method)(path, headers=self.bearer(token))
                self.assertEqual(response.status_code, 405)
        for suffix in ("settings", "sql", "files", "items/1", "items/1/actions"):
            self.assertEqual(self.client.get("/api/external/v1/" + suffix, headers=self.bearer(token)).status_code, 404)

    def test_openapi_and_connection_are_copyable_without_credential_leak(self):
        created = self.create_token()
        connection = self.client.get("/api/integrations/connection", headers={**self.admin, "x-forwarded-host": "evil.example", "x-forwarded-proto": "https"}).json()
        self.assertEqual(connection["base_url"], "http://testserver/api/external/v1")
        self.assertEqual(connection["openapi_url"], "http://testserver/api/integrations/openapi.json")
        self.assertIn("YOUR_READ_ONLY_TOKEN", connection["curl"])
        self.assertNotIn(created["token"], json.dumps(connection))
        spec = self.client.get("/api/integrations/openapi.json", headers=self.admin).json()
        self.assertEqual(set(spec["paths"]), {"/items", "/locations", "/categories"})
        self.assertTrue(all(set(route) == {"get"} for route in spec["paths"].values()))
        self.assertEqual(spec["servers"], [{"url": connection["base_url"]}])
        self.assertNotIn(created["token"], json.dumps(spec))

    def test_module_supports_existing_dict_service_container(self):
        other = FastAPI()
        other.include_router(build_integrations_router(vars(self.services)))
        with TestClient(other) as client:
            self.assertEqual(client.get("/api/integrations/tokens", headers=self.admin).status_code, 200)


if __name__ == "__main__":
    unittest.main()
