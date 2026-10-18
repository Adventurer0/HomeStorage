"""Safety workflows for local scanning and reviewed CSV import."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import csv
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.transfer import CSV_COLUMNS, _qr_identifier


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="homestorage-transfer-")
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        self.client = self.enterContext(TestClient(create_app(data_dir=self.data_dir, password="")))
        self.home = self.ok(self.client.get("/api/locations"))[0]

    def ok(self, response):
        self.assertLess(response.status_code, 300, response.text)
        self.assertGreaterEqual(response.status_code, 200, response.text)
        return response.json()

    def item(self, name="电池", **fields):
        return self.ok(self.client.post("/api/items", json={
            "name": name, "location_id": self.home["id"], "barcode": "000123", "quantity": 5, **fields,
        }))

    def current(self, item):
        return self.ok(self.client.get(f"/api/items/{item['id']}"))

    def scan(self, item, key="request-0123456789", **fields):
        return self.client.post("/api/scan/commit", json={
            "code": item["barcode"], "item_id": item["id"], "operation": "outbound",
            "quantity": 1, "request_id": key, "confirmed": True, **fields,
        })

    def csv(self, rows, fields=CSV_COLUMNS):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({"name": "新电池", "location_path": "家", "quantity": "2", **row})
        return b"\xef\xbb\xbf" + output.getvalue().encode()

    def preview(self, rows=None, *, content=None, fields=CSV_COLUMNS):
        return self.client.post("/api/transfer/import/preview", files={
            "file": ("inventory.csv", content if content is not None else self.csv(rows, fields), "text/csv")
        })

    def confirm(self, preview, confirmed=True):
        return self.client.post(f"/api/transfer/import/{preview['id']}/confirm", json={"confirmed": confirmed})

    def database(self):
        db = sqlite3.connect(self.data_dir / "homestorage.sqlite3")
        self.addCleanup(db.close)
        return db

    def test_lookup_returns_all_ambiguous_barcodes_without_mutation(self):
        first = self.item()
        second = self.item("另一批电池")
        result = self.ok(self.client.get("/api/scan/lookup", params={"code": "000123"}))
        self.assertEqual(result["kind"], "items")
        self.assertEqual({row["id"] for row in result["items"]}, {first["id"], second["id"]})
        self.assertEqual(self.current(first)["quantity"], 5)
        self.assertEqual(self.ok(self.client.get("/api/scan/lookup", params={"code": "missing"}))["items"], [])

    def test_scan_requires_confirmation_selection_and_safe_quantities(self):
        item = self.item()
        for fields in ({"confirmed": False}, {"quantity": 0}, {"quantity": True}, {"quantity": 6},
                       {"item_id": item["id"] + 100}, {"code": "different"}):
            with self.subTest(fields=fields):
                self.assertGreaterEqual(self.scan(item, **fields).status_code, 400)
        self.assertEqual(self.current(item)["quantity"], 5)

    def test_scan_retry_and_concurrent_retry_decrement_once(self):
        item = self.item()
        barrier = Barrier(2)

        def scan():
            barrier.wait()
            return self.scan(item)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: scan(), range(2)))
        decoded = [self.ok(result) for result in results]
        self.assertEqual(sorted(result["replayed"] for result in decoded), [False, True])
        self.assertEqual(self.current(item)["quantity"], 4)
        self.assertEqual(self.scan(item, quantity=2).status_code, 409)
        self.assertEqual(self.current(item)["quantity"], 4)

    def test_scan_restock_take_and_return_reuse_inventory_state_rules(self):
        item = self.item()
        self.assertEqual(self.ok(self.scan(item, operation="inbound", quantity=2))["item"]["quantity"], 7)
        self.assertEqual(self.ok(self.scan(item, key="takeout-0123456789", operation="take_out"))["item"]["status"], "in_use")
        self.assertEqual(self.scan(item, key="invalid-0123456789").status_code, 409)
        self.assertEqual(self.ok(self.scan(item, key="returned-0123456789", operation="return"))["item"]["status"], "in_stock")

    def test_scan_inventory_and_idempotency_record_commit_together(self):
        item = self.item()
        db = self.database()
        db.execute("CREATE TRIGGER fail_scan_receipt BEFORE INSERT ON scan_commits BEGIN SELECT RAISE(ABORT,'test'); END")
        db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.scan(item)
        self.assertEqual(self.current(item)["quantity"], 5)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_commits").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM logs WHERE action='consume'").fetchone()[0], 0)
        db.execute("DROP TRIGGER fail_scan_receipt")
        db.commit()
        self.assertFalse(self.ok(self.scan(item))["replayed"])
        self.assertEqual(self.current(item)["quantity"], 4)

    def test_scan_cannot_change_archived_items_or_partially_take_out_a_record(self):
        item = self.item()
        self.assertEqual(self.scan(item, operation="take_out", quantity=2).status_code, 422)
        self.ok(self.client.post(f"/api/items/{item['id']}/archive", json={"confirmed": True}))
        self.assertEqual(self.scan(item).status_code, 409)
        self.assertEqual(self.current(item)["quantity"], 5)

    def test_item_and_location_qr_parse_without_fetching(self):
        item = self.item()
        code = f"https://private-nas.example/?item={item['id']}&scan=1"
        with patch("httpx.Client.send", side_effect=AssertionError("QR must not access any URL")):
            # TestClient itself uses httpx.Client.send, so call pure parser here.
            self.assertEqual(_qr_identifier(code), ("item", item["id"]))
        result = self.ok(self.client.get("/api/scan/lookup", params={"code": code}))
        self.assertEqual(result["items"][0]["id"], item["id"])
        location = self.ok(self.client.get("/api/scan/lookup", params={"code": f"https://nas.example/?location={self.home['id']}"}))
        self.assertEqual(location["kind"], "location")
        self.assertEqual(location["location"]["id"], self.home["id"])
        response = self.client.get(f"/api/items/{item['id']}/qr", params={"base_url": "https://nas.example:8443"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers["content-type"])
        self.assertGreater(len(response.content), 500)
        for origin in ("javascript:alert(1)", "https://user:pw@nas.example", "https://nas.example/path", "https://nas.example/?secret=x"):
            self.assertEqual(self.client.get(f"/api/items/{item['id']}/qr", params={"base_url": origin}).status_code, 422)

    def test_csv_preview_is_readonly_confirm_atomic_and_not_replayable(self):
        preview = self.ok(self.preview([{"barcode": "001"}, {"name": "透明胶", "barcode": "002"}]))
        self.assertTrue(preview["can_confirm"])
        self.assertEqual(preview["valid_count"], 2)
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        self.assertEqual(self.confirm(preview, False).status_code, 422)
        result = self.ok(self.confirm(preview))
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(len(self.ok(self.client.get("/api/items"))), 2)

    def test_csv_duplicate_barcode_location_skipped_never_overwrites(self):
        item = self.item()
        preview = self.ok(self.preview([{"barcode": item["barcode"], "name": "不得覆盖"},
                                        {"barcode": "222"}, {"barcode": "222", "name": "重复"}]))
        self.assertEqual((preview["valid_count"], preview["skipped_count"]), (1, 2))
        self.assertEqual(self.ok(self.confirm(preview))["created_count"], 1)
        self.assertEqual(self.current(item)["name"], item["name"])
        self.assertEqual(self.current(item)["quantity"], 5)

    def test_csv_errors_list_exact_rows_and_block_entire_confirmation(self):
        preview = self.ok(self.preview([{"barcode": "111"}, {"name": "错误行", "quantity": "-1"},
                                        {"category": "未知分类"}, {"location_path": "不存在的位置"},
                                        {"stored_date": "2026-02-30"}, {"purchase_price": "nan"}]))
        self.assertFalse(preview["can_confirm"])
        self.assertEqual([error["row"] for error in preview["errors"]], [3, 4, 5, 6, 7])
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(self.ok(self.client.get("/api/items")), [])

    def test_csv_rejects_bad_headers_encoding_shape_and_limits(self):
        for content in (b"name,name\na,b\n", b"name,location_path\na\n", b"name,location_path,sql\na,b,c\n",
                        b"name,location_path\n\xff,home\n", b"name,location_path\n\x00,home\n"):
            self.assertEqual(self.preview(content=content).status_code, 422)
        self.assertEqual(self.preview(content=b"x" * (10 * 1024 * 1024 + 1)).status_code, 413)
        self.assertEqual(self.preview(content=b"name,location_path\n" + "x,家\n".encode() * 5001).status_code, 413)

    def test_csv_stale_location_category_and_new_duplicates_rejected(self):
        for change in ("location", "category", "duplicate"):
            with self.subTest(change=change):
                preview = self.ok(self.preview([{"barcode": "stale-" + change}]))
                if change == "location":
                    self.ok(self.client.patch(f"/api/locations/{self.home['id']}", json={"name": "新家"}))
                elif change == "category":
                    category = next(row for row in self.ok(self.client.get("/api/categories")) if row["name"] == "其他")
                    self.ok(self.client.patch(f"/api/categories/{category['id']}", json={"active": False}))
                else:
                    self.item(barcode="stale-duplicate")
                self.assertEqual(self.confirm(preview).status_code, 409)
                if change == "location":
                    self.ok(self.client.patch(f"/api/locations/{self.home['id']}", json={"name": "家"}))
                if change == "category":
                    self.ok(self.client.patch(f"/api/categories/{category['id']}", json={"active": True}))

    def test_csv_preview_expiration_and_actor_binding(self):
        preview = self.ok(self.preview([{}]))
        db = self.database()
        db.execute("UPDATE csv_previews SET actor='someone-else' WHERE id=?", (preview["id"],))
        db.commit()
        self.assertEqual(self.confirm(preview).status_code, 404)
        preview = self.ok(self.preview([{}]))
        db.execute("UPDATE csv_previews SET created_at=0 WHERE id=?", (preview["id"],))
        db.commit()
        self.assertEqual(self.confirm(preview).status_code, 409)

    def test_csv_full_transaction_rolls_back_on_second_create_failure(self):
        preview = self.ok(self.preview([{"name": "第一行"}, {"name": "第二行"}]))
        db = self.database()
        db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON items WHEN NEW.name='第二行' BEGIN SELECT RAISE(ABORT,'test'); END")
        db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.confirm(preview)
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        self.assertIsNone(db.execute("SELECT used_at FROM csv_previews WHERE id=?", (preview["id"],)).fetchone()[0])

    def test_csv_concurrent_confirmation_creates_exactly_one_batch(self):
        preview = self.ok(self.preview([{"name": "并发第一行"}, {"name": "并发第二行"}]))
        barrier = Barrier(2)

        def confirm():
            barrier.wait()
            return self.confirm(preview)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: confirm(), range(2)))
        self.assertEqual(sorted(result.status_code for result in results), [200, 409])
        self.assertEqual(len(self.ok(self.client.get("/api/items"))), 2)
        self.assertEqual(self.database().execute("SELECT COUNT(*) FROM logs WHERE action='csv_import'").fetchone()[0], 1)

    def test_csv_unicode_control_barcodes_are_reported_during_preview(self):
        for control in ("\u0085", "\u200b", "\u202e"):
            with self.subTest(control=repr(control)):
                preview = self.ok(self.preview([{"barcode": "00" + control + "123"}]))
                self.assertFalse(preview["can_confirm"])
                self.assertEqual(preview["errors"][0]["field"], "barcode")
                self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(self.ok(self.client.get("/api/items")), [])

    def test_csv_export_formula_protection_and_business_state_roundtrip(self):
        item = self.item("=HYPERLINK(\"example\")", notes="@SUM(A1)", barcode="000123", quantity=2)
        self.ok(self.client.post(f"/api/items/{item['id']}/actions", json={"action": "take_out"}))
        self.ok(self.client.post(f"/api/items/{item['id']}/archive", json={"confirmed": True}))
        response = self.client.get("/api/transfer/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        row = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))[0]
        self.assertEqual(row["name"], "'" + item["name"])
        self.assertEqual(row["notes"], "'@SUM(A1)")
        self.assertEqual(row["barcode"], "'000123")
        self.assertEqual(row["status"], "in_use")
        self.assertTrue(row["archived_at"])
        # Change barcode in the file so it is a deliberate new record in this DB.
        row["barcode"] = "'000124"
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=row.keys()); writer.writeheader(); writer.writerow(row)
        preview = self.ok(self.preview(content=output.getvalue().encode()))
        created = self.ok(self.confirm(preview))
        restored = self.ok(self.client.get(f"/api/items/{created['item_ids'][0]}"))
        self.assertEqual(restored["name"], item["name"])
        self.assertEqual(restored["notes"], "@SUM(A1)")
        self.assertEqual((restored["status"], restored["quantity"], restored["barcode"]), ("in_use", 2, "000124"))
        self.assertTrue(restored["archived_at"])

    def test_csv_rejects_inconsistent_status_and_ignores_old_ids_photo_paths(self):
        for status, quantity in (("depleted", "2"), ("discarded", "2"), ("in_stock", "0"), ("in_use", "0"), ("lent", "0")):
            preview = self.ok(self.preview([{"status": status, "quantity": quantity}]))
            self.assertFalse(preview["can_confirm"])
        preview = self.ok(self.preview([{"id": "999999", "image_url": "/uploads/not-authorized.jpg", "status": "lost", "quantity": "0"}], fields=("id", *CSV_COLUMNS, "image_url")))
        created = self.ok(self.confirm(preview))
        item = self.ok(self.client.get(f"/api/items/{created['item_ids'][0]}"))
        self.assertNotEqual(item["id"], 999999)
        self.assertIsNone(item["image_url"])
        self.assertEqual((item["status"], item["quantity"]), ("lost", 0))


class TransferHouseholdTest(unittest.TestCase):
    """Real cookie sessions must protect reviewed imports and scan writes."""

    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.data_dir = Path(self.stack.enter_context(TemporaryDirectory(prefix="hs-transfer-household-")))
        self.app = create_app(data_dir=self.data_dir, password="fixture-admin-password")
        self.admin = self.stack.enter_context(TestClient(self.app))
        self.ok(self.admin.post("/api/login", json={"password": "fixture-admin-password"}))
        self.home = self.ok(self.admin.get("/api/locations"))[0]
        self.item = self.ok(self.admin.post("/api/items", json={
            "name": "家人共享电池", "location_id": self.home["id"], "barcode": "00123", "quantity": 5,
        }))
        self.member = self.client_for("member", "member")
        self.viewer = self.client_for("viewer", "viewer")

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def client_for(self, username, role):
        self.ok(self.admin.post("/api/users", json={
            "username": username, "password": "fixture-user-password", "role": role,
        }))
        client = self.stack.enter_context(TestClient(self.app))
        self.ok(client.post("/api/login", json={"username": username, "password": "fixture-user-password"}))
        return client

    def scan(self, client):
        return client.post("/api/scan/commit", json={
            "code": "00123", "item_id": self.item["id"], "operation": "outbound", "quantity": 1,
            "request_id": "shared-key-0123456789", "confirmed": True,
        })

    def preview(self, client):
        return client.post("/api/transfer/import/preview", files={
            "file": ("inventory.csv", "name,location_path\n家人导入,家\n".encode(), "text/csv"),
        })

    def test_viewer_can_read_scan_results_and_export_but_cannot_import_or_write(self):
        self.assertEqual(self.viewer.get("/api/scan/lookup", params={"code": "00123"}).status_code, 200)
        self.assertEqual(self.viewer.get(f"/api/items/{self.item['id']}/qr", params={"base_url": "https://nas.example"}).status_code, 200)
        self.assertEqual(self.viewer.get("/api/transfer/export.csv").status_code, 200)
        self.assertEqual(self.scan(self.viewer).status_code, 403)
        self.assertEqual(self.preview(self.viewer).status_code, 403)
        self.assertEqual(self.viewer.get("/api/transfer/template.csv").status_code, 403)
        self.assertEqual(self.viewer.post("/api/transfer/import/unknown/confirm", json={"confirmed": True}).status_code, 403)
        self.assertEqual(self.ok(self.admin.get(f"/api/items/{self.item['id']}"))["quantity"], 5)

    def test_member_preview_is_bound_to_member_and_can_be_confirmed_once(self):
        preview = self.ok(self.preview(self.member))
        path = f"/api/transfer/import/{preview['id']}/confirm"
        self.assertEqual(self.admin.post(path, json={"confirmed": True}).status_code, 404)
        self.assertEqual(self.ok(self.member.post(path, json={"confirmed": True}))["created_count"], 1)
        self.assertEqual(self.member.post(path, json={"confirmed": True}).status_code, 409)
        log = next(row for row in self.ok(self.admin.get("/api/logs")) if row["action"] == "csv_import")
        self.assertEqual(log["actor_name"], "member")

    def test_same_scan_request_key_is_independent_for_each_family_member(self):
        self.assertFalse(self.ok(self.scan(self.member))["replayed"])
        self.assertFalse(self.ok(self.scan(self.admin))["replayed"])
        self.assertTrue(self.ok(self.scan(self.member))["replayed"])
        self.assertEqual(self.ok(self.admin.get(f"/api/items/{self.item['id']}"))["quantity"], 3)

    def test_signed_out_clients_cannot_access_transfer_or_scan_records(self):
        anonymous = self.stack.enter_context(TestClient(self.app))
        for path in ("/api/scan/lookup?code=00123", "/api/transfer/export.csv", "/api/transfer/template.csv",
                     f"/api/items/{self.item['id']}/qr?base_url=https://nas.example"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        self.assertEqual(self.scan(anonymous).status_code, 401)
        self.assertEqual(self.preview(anonymous).status_code, 401)


if __name__ == "__main__":
    unittest.main()
