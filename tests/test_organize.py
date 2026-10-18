"""Category organization is a proposal first, then an atomic, reversible write."""

from contextlib import asynccontextmanager, closing
from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.ai_settings import AIConfiguration
from app.db import connect, initialize, transaction
from app.organize import Assignment, CreatePlan, OrganizationManager, PlanError, build_organize_router


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class OrganizationTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="homestorage-organize-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "inventory.sqlite3"
        initialize(self.path)
        self.configuration = AIConfiguration("https://provider.example/v1", "secret-provider-key", "configured-real-model", "", "", "")

        def require_admin(request):
            if request.headers.get("x-test-role", "admin") != "admin":
                raise HTTPException(403, "需要管理员权限")

        def audit(db, action, *, item=None, note="", **_):
            db.execute("INSERT INTO logs(action,item_id,item_name,note,created_at) VALUES(?,?,?,?,?)",
                       (action, item["id"] if item else None, item["name"] if item else "", note, now()))

        self.services = {"connect": lambda: connect(self.path), "now": now, "audit": audit,
                         "require_admin": require_admin, "current_user": lambda _: {"id": 1, "role": "admin"},
                         "settings": SimpleNamespace(snapshot=lambda: self.configuration)}
        self.manager = OrganizationManager(self.services)

        @asynccontextmanager
        async def lifespan(_):
            self.manager.initialize()
            yield
            await self.manager.shutdown()

        self.app = FastAPI(lifespan=lifespan)
        self.app.include_router(build_organize_router(self.services, manager=self.manager))
        self.client = self.enterContext(TestClient(self.app))

    def items(self, count=1):
        with closing(connect(self.path)) as db:
            with transaction(db):
                for index in range(count):
                    db.execute("INSERT INTO items(name,category_id,category,location_id,quantity,stored_date,created_at,updated_at,notes,image_url) "
                               "VALUES(?,1,'日常用品',1,7,'2026-10-17',?,?,?,?)",
                               (f"物品{index}", now(), now(), "private instruction and notes", "/uploads/private-photo.jpg"))
            return [dict(row) for row in db.execute("SELECT * FROM items ORDER BY id")]

    async def assign(self, configuration, items, categories):
        return [Assignment(item_id=item["id"], category="整理后的分类", reason="按用途整理") for item in items]

    def create(self, **body):
        response = self.client.post("/api/organize/batches", json={"confirmed": True, **body})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["batch"]["id"]

    def wait(self, batch_id, states=("ready", "failed", "interrupted", "cancelled")):
        for _ in range(200):
            response = self.client.get(f"/api/organize/batches/{batch_id}")
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result["state"] in states:
                return result
            time.sleep(.005)
        self.fail("organization job did not finish")

    def ready(self, count=1):
        self.items(count)
        with patch.object(self.manager, "_provider_assign", side_effect=self.assign):
            batch_id = self.create()
            result = self.wait(batch_id)
        self.assertEqual(result["state"], "ready", result)
        return batch_id

    def confirm(self, batch_id):
        return self.client.post(f"/api/organize/batches/{batch_id}/confirm", json={"confirmed": True})

    def rollback(self, batch_id):
        return self.client.post(f"/api/organize/batches/{batch_id}/rollback", json={"confirmed": True})

    def raw(self):
        with closing(connect(self.path)) as db:
            return [dict(row) for row in db.execute("SELECT * FROM items ORDER BY id")]

    def test_requires_admin_and_explicit_boolean_consent(self):
        self.items()
        for body in ({}, {"confirmed": False}, {"confirmed": "true"}, {"confirmed": 1}, {"confirmed": True, "sql": "DROP TABLE items"}):
            self.assertEqual(self.client.post("/api/organize/batches", json=body).status_code, 422)
        for path in ("", "/unknown", "/unknown/rollback-preview"):
            self.assertEqual(self.client.get("/api/organize/batches" + path, headers={"x-test-role": "viewer"}).status_code, 403)
        self.assertEqual(self.client.post("/api/organize/batches", json={"confirmed": True}, headers={"x-test-role": "member"}).status_code, 403)

    def test_unconfigured_model_is_not_simulated(self):
        self.items()
        self.configuration = AIConfiguration("https://provider.example/v1", "", "", "", "", "")
        with patch.object(self.manager, "_provider_assign", new_callable=AsyncMock) as provider:
            response = self.client.post("/api/organize/batches", json={"confirmed": True})
        self.assertEqual(response.status_code, 503)
        provider.assert_not_called()
        self.assertIn("配置", response.text)

    def test_full_inventory_is_processed_and_no_write_until_confirmed(self):
        before = self.items(123)
        with patch.object(self.manager, "_provider_assign", side_effect=self.assign) as provider:
            batch_id = self.create()
            batch = self.wait(batch_id)
        self.assertEqual(batch["state"], "ready", batch)
        self.assertEqual((batch["total_items"], batch["processed_items"], batch["total_chunks"], batch["completed_chunks"]), (123, 123, 3, 3))
        self.assertEqual([len(call.args[1]) for call in provider.call_args_list], [50, 50, 23])
        self.assertEqual(batch["model"], "configured-real-model")
        self.assertEqual(len(batch["changes"]), 123)
        self.assertEqual(self.raw(), before)
        with closing(connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT id FROM categories WHERE name='整理后的分类'").fetchone())
        self.assertNotIn("secret-provider-key", json.dumps(batch))
        self.assertNotIn("private instruction", json.dumps(batch))

    def test_confirm_and_rollback_are_atomic_idempotent_and_only_change_categories(self):
        batch_id = self.ready(3)
        before = self.raw()
        applied = self.confirm(batch_id)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        after = self.raw()
        for old, new in zip(before, after):
            self.assertEqual(new["category"], "整理后的分类")
            self.assertEqual({k: v for k, v in old.items() if k not in {"category", "category_id", "updated_at"}},
                             {k: v for k, v in new.items() if k not in {"category", "category_id", "updated_at"}})
        preview = self.client.get(f"/api/organize/batches/{batch_id}/rollback-preview").json()
        self.assertTrue(preview["can_rollback"])
        self.assertEqual(self.rollback(batch_id).status_code, 200)
        self.assertEqual(self.rollback(batch_id).status_code, 200)
        for old, reverted in zip(before, self.raw()):
            self.assertEqual({k: v for k, v in old.items() if k != "updated_at"},
                             {k: v for k, v in reverted.items() if k != "updated_at"})
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM logs WHERE action='ai_organize'").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM logs WHERE action='ai_organize_rollback'").fetchone()[0], 3)
            self.assertIsNotNone(db.execute("SELECT id FROM categories WHERE name='整理后的分类'").fetchone())

    def test_simultaneous_confirm_and_rollback_only_apply_each_change_once(self):
        batch_id = self.ready(3)

        def together(operation):
            barrier = threading.Barrier(2)

            def invoke():
                barrier.wait(timeout=5)
                return operation(batch_id)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(invoke) for _ in range(2)]
                return [future.result(timeout=10) for future in futures]

        for response in together(self.confirm):
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({item["category"] for item in self.raw()}, {"整理后的分类"})
        for response in together(self.rollback):
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({item["category"] for item in self.raw()}, {"日常用品"})
        with closing(connect(self.path)) as db:
            counts = dict(db.execute("SELECT action,COUNT(*) FROM logs WHERE action IN ('ai_organize','ai_organize_rollback') GROUP BY action"))
        self.assertEqual(counts, {"ai_organize": 3, "ai_organize_rollback": 3})

    def test_applied_plan_can_be_rolled_back_after_manager_restart(self):
        batch_id = self.ready(2)
        before = self.raw()
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        second = OrganizationManager(self.services)
        second.initialize()
        try:
            self.assertTrue(second.rollback_preview(batch_id)["can_rollback"])
            self.assertEqual(second.rollback(batch_id)["state"], "rolled_back")
        finally:
            self.client.portal.call(second.shutdown)
        self.assertEqual(
            [{key: value for key, value in item.items() if key != "updated_at"} for item in self.raw()],
            [{key: value for key, value in item.items() if key != "updated_at"} for item in before],
        )

    def test_concurrent_edit_aborts_whole_apply_without_creating_category(self):
        batch_id = self.ready(2)
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE items SET quantity=8 WHERE id=1")
        response = self.confirm(batch_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual([item["category_id"] for item in self.raw()], [1, 1])
        with closing(connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT id FROM categories WHERE name='整理后的分类'").fetchone())

    def test_inventory_addition_invalidates_whole_plan(self):
        batch_id = self.ready()
        self.items()
        self.assertEqual(self.confirm(batch_id).status_code, 409)
        self.assertTrue(all(item["category_id"] == 1 for item in self.raw()))

    def test_rollback_refuses_all_when_one_item_was_edited(self):
        batch_id = self.ready(2)
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE items SET notes='newer important data' WHERE id=2")
        after_edit = self.raw()
        preview = self.client.get(f"/api/organize/batches/{batch_id}/rollback-preview").json()
        self.assertFalse(preview["can_rollback"])
        self.assertEqual(preview["conflicts"][0]["item_id"], 2)
        response = self.rollback(batch_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.raw(), after_edit)
        self.assertEqual(self.manager.get(batch_id)["state"], "applied")

    def test_rollback_detects_original_category_rename(self):
        batch_id = self.ready()
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE categories SET name='后续改名' WHERE id=1")
        self.assertEqual(self.rollback(batch_id).status_code, 409)
        self.assertEqual(self.raw()[0]["category"], "整理后的分类")

    def test_audit_failure_reverts_items_categories_and_batch_marker(self):
        batch_id = self.ready(2)
        before = self.raw()
        with patch.dict(self.services, {"audit": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail audit"))}):
            with self.assertRaises(RuntimeError):
                self.confirm(batch_id)
        self.assertEqual(self.raw(), before)
        self.assertEqual(self.manager.get(batch_id)["state"], "ready")
        with closing(connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT id FROM categories WHERE name='整理后的分类'").fetchone())

    def test_missing_duplicate_and_unknown_ids_are_rejected_as_a_whole(self):
        self.items(2)
        cases = [[], [Assignment(item_id=1, category="食品", reason="a")] * 2,
                 [Assignment(item_id=1, category="食品", reason="a"), Assignment(item_id=999, category="食品", reason="b")]]
        for assignments in cases:
            with self.subTest(assignments=assignments), patch.object(self.manager, "_provider_assign", new=AsyncMock(return_value=assignments)):
                batch = self.wait(self.create())
                self.assertEqual(batch["state"], "failed", batch)
        self.assertEqual([item["category_id"] for item in self.raw()], [1, 1])

    def test_disabled_assignment_rejected(self):
        self.items()
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE categories SET active=0 WHERE name='食品'")
        with patch.object(self.manager, "_provider_assign", new=AsyncMock(return_value=[Assignment(item_id=1, category="食品", reason="food")])):
            batch = self.wait(self.create())
        self.assertEqual(batch["state"], "failed")
        self.assertIn("停用", batch["error"])

    def test_unicode_casefold_collision_keeps_reviewed_stable_category_id(self):
        self.items()
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("INSERT INTO categories(name) VALUES('Straße')")
            second = db.execute("INSERT INTO categories(name) VALUES('STRASSE')").lastrowid
        with patch.object(self.manager, "_provider_assign", new=AsyncMock(return_value=[Assignment(item_id=1, category="STRASSE", reason="precise category")])):
            batch_id = self.create()
            batch = self.wait(batch_id)
        self.assertEqual(batch["changes"][0]["after_category_id"], second)
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        self.assertEqual((self.raw()[0]["category_id"], self.raw()[0]["category"]), (second, "STRASSE"))

    def test_ambiguous_unicode_category_without_exact_match_is_rejected(self):
        self.items()
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("INSERT INTO categories(name) VALUES('Straße')")
            db.execute("INSERT INTO categories(name) VALUES('STRASSE')")
        with patch.object(self.manager, "_provider_assign", new=AsyncMock(return_value=[Assignment(item_id=1, category="strasse", reason="ambiguous")])):
            batch = self.wait(self.create())
        self.assertEqual(batch["state"], "failed")
        self.assertIn("歧义", batch["error"])

    def test_provider_failure_is_sanitized_and_not_automatically_retried(self):
        self.items()
        with patch.object(self.manager, "_provider_assign", new=AsyncMock(side_effect=RuntimeError("secret-provider-key https://sensitive-url"))) as provider:
            batch = self.wait(self.create())
        self.assertEqual(batch["state"], "failed")
        self.assertEqual(provider.await_count, 1)
        self.assertNotIn("secret-provider-key", json.dumps(batch))
        self.assertNotIn("sensitive-url", json.dumps(batch))

    def test_expired_preview_cannot_apply(self):
        batch_id = self.ready()
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE organization_batches SET expires_at=0 WHERE id=?", (batch_id,))
        self.assertEqual(self.manager.get(batch_id)["state"], "expired")
        self.assertEqual(self.confirm(batch_id).status_code, 409)
        self.assertEqual(self.raw()[0]["category_id"], 1)

    def test_cancellation_never_mutates_inventory(self):
        batch_id = self.ready()
        response = self.client.post(f"/api/organize/batches/{batch_id}/cancel", json={"confirmed": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "cancelled")
        self.assertEqual(self.confirm(batch_id).status_code, 409)
        self.assertEqual(self.raw()[0]["category_id"], 1)

    def test_cancel_in_flight_does_not_publish_partial_results_or_continue_calls(self):
        self.items(101)
        started = threading.Event()

        async def slow_provider(*_):
            started.set()
            await asyncio.Event().wait()

        with patch.object(self.manager, "_provider_assign", side_effect=slow_provider) as provider:
            batch_id = self.create()
            self.assertTrue(started.wait(1))
            response = self.client.post(f"/api/organize/batches/{batch_id}/cancel", json={"confirmed": True})
            self.assertEqual(response.status_code, 200, response.text)
            batch = self.wait(batch_id)
        self.assertEqual(batch["state"], "cancelled")
        self.assertEqual(batch["changes"], [])
        self.assertEqual(provider.await_count, 1)
        self.assertEqual({item["category_id"] for item in self.raw()}, {1})

    def test_rollback_audit_failure_preserves_applied_state_and_all_categories(self):
        batch_id = self.ready(2)
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        applied = self.raw()
        with patch.dict(self.services, {"audit": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failed"))}):
            with self.assertRaises(RuntimeError):
                self.rollback(batch_id)
        self.assertEqual(self.raw(), applied)
        self.assertEqual(self.manager.get(batch_id)["state"], "applied")

    def test_new_category_collision_after_preview_aborts_apply(self):
        batch_id = self.ready()
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("INSERT INTO categories(name,active) VALUES('整理后的分类',1)")
        self.assertEqual(self.confirm(batch_id).status_code, 409)
        self.assertEqual(self.raw()[0]["category_id"], 1)

    def test_unknown_or_control_character_model_fields_are_rejected(self):
        from pydantic import ValidationError
        for entry in ({"item_id": True, "category": "食品", "reason": "x"},
                      {"item_id": 1, "category": "食品\n注入", "reason": "x"},
                      {"item_id": 1, "category": "食品", "reason": "x", "quantity": 999},
                      {"item_id": 1, "category": "X" * 121, "reason": "x"}):
            with self.subTest(entry=entry), self.assertRaises(ValidationError):
                Assignment.model_validate(entry)

    def test_too_many_items_is_rejected_without_calling_provider(self):
        self.items(3)
        with patch("app.organize.MAX_ITEMS", 2), patch.object(self.manager, "_provider_assign", new_callable=AsyncMock) as provider:
            response = self.client.post("/api/organize/batches", json={"confirmed": True})
        self.assertEqual(response.status_code, 413)
        provider.assert_not_called()

    def test_archived_scope_explicit_and_total_count_matches(self):
        self.items(2)
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE items SET archived_at=? WHERE id=2", (now(),))
        with patch.object(self.manager, "_provider_assign", side_effect=self.assign):
            default = self.wait(self.create())
            inclusive = self.wait(self.create(include_archived=True))
        self.assertEqual(default["total_items"], 1)
        self.assertEqual(inclusive["total_items"], 2)

    def test_restart_marks_orphans_interrupted_but_keeps_other_live_manager(self):
        batch_id = self.ready()
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE organization_batches SET state='generating',owner='old-crashed-process' WHERE id=?", (batch_id,))
        second = OrganizationManager(self.services)
        second.initialize()
        self.addCleanup(lambda: self.client.portal.call(second.shutdown))
        self.assertEqual(second.get(batch_id)["state"], "interrupted")
        with closing(connect(self.path)) as db, transaction(db):
            db.execute("UPDATE organization_batches SET state='generating',owner=? WHERE id=?", (self.manager.owner, batch_id))
        third = OrganizationManager(self.services)
        third.initialize()
        self.addCleanup(lambda: self.client.portal.call(third.shutdown))
        self.assertEqual(third.get(batch_id)["state"], "generating")

    def test_provider_request_never_contains_notes_photos_or_secrets_in_payload(self):
        item = self.items()[0]
        captured = {}

        async def handler(request):
            captured["payload"] = json.loads(request.content)
            return __import__("httpx").Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"assignments": [{"item_id": item["id"], "category": "食品", "reason": "test"}]})}}]})

        import httpx
        original = httpx.AsyncClient
        with patch("app.organize.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)):
            batch = self.wait(self.create())
        self.assertEqual(batch["state"], "ready", batch)
        payload = json.dumps(captured["payload"])
        self.assertNotIn("private instruction", payload)
        self.assertNotIn("private-photo", payload)
        self.assertNotIn("secret-provider-key", payload)
        self.assertIn("configured-real-model", payload)

    def test_provider_http_errors_and_truncation_are_not_treated_as_ready(self):
        self.items()
        import httpx
        original = httpx.AsyncClient
        for status, body in ((401, {"error": "secret-provider-key"}), (429, {}),
                             (200, {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}),
                             (200, {"choices": [{"finish_reason": "stop", "message": {"content": '{"assignments":[]}'}}]})):
            async def handler(_, status=status, body=body):
                return httpx.Response(status, json=body)

            with self.subTest(status=status, body=body), patch("app.organize.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)):
                batch = self.wait(self.create())
            self.assertEqual(batch["state"], "failed", batch)
            self.assertNotIn("secret-provider-key", json.dumps(batch))
            self.assertEqual(self.raw()[0]["category_id"], 1)


if __name__ == "__main__":
    unittest.main()
