"""HomeStorage API workflows: python -m unittest discover -v."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app


class HomeStorageAPITest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="homestorage-test-")
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        self.client = self.enterContext(
            TestClient(create_app(data_dir=self.data_dir, password="", timezone="Asia/Shanghai"))
        )
        locations = self.ok(self.client.get("/api/locations"))
        self.home = next(location for location in locations if location["name"] == "家")

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def rejected(self, response):
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertLess(response.status_code, 500, response.text)
        self.assertIn("detail", response.json())

    def location(self, name="书房", parent=None, kind="room"):
        return self.ok(self.client.post("/api/locations", json={
            "name": name,
            "type": kind,
            "parent_id": self.home["id"] if parent is None else parent,
        }))

    def item(self, name="收纳物品", **fields):
        payload = {"name": name, "location_id": self.home["id"], **fields}
        return self.ok(self.client.post("/api/items", json=payload))

    def action(self, item, action, **fields):
        return self.ok(self.client.post(
            f"/api/items/{item['id']}/actions", json={"action": action, **fields}
        ))

    def current(self, item):
        return self.ok(self.client.get(f"/api/items/{item['id']}"))

    def logs(self, item):
        return self.ok(self.client.get("/api/logs", params={"item_id": item["id"]}))

    @staticmethod
    def png():
        output = BytesIO()
        Image.new("RGB", (3, 2), color=(22, 138, 90)).save(output, format="PNG")
        return output.getvalue()

    def test_fresh_environment_has_home_without_sample_inventory(self):
        self.assertEqual(self.ok(self.client.get("/health")), {"status": "ok"})
        self.assertEqual(self.home["type"], "home")
        self.assertIsNone(self.home["parent_id"])
        self.assertEqual(self.ok(self.client.get("/api/items")), [])
        dashboard = self.ok(self.client.get("/api/dashboard"))
        for field in ("total_items", "total_quantity", "in_use_count", "soon_count", "expired_count"):
            self.assertEqual(dashboard[field], 0)
        self.assertEqual(dashboard["expiring_items"], [])
        self.assertEqual(self.ok(self.client.get("/api/meta"))["timezone"], "Asia/Shanghai")

    def test_nested_locations_filter_descendants_and_count_active_inventory(self):
        room = self.location()
        shelf = self.location("器材架", room["id"], "shelf")
        box = self.location("摄影箱", shelf["id"], "box")
        camera = self.item("微单相机", location_id=box["id"], category="摄影器材", quantity=2)
        lens = self.item("备用镜头", location_id=shelf["id"], category="摄影器材")
        self.item("厨房抹布", category="日常用品")
        self.action(camera, "take_out")
        result = self.ok(self.client.get("/api/items", params={"location_id": room["id"]}))
        self.assertEqual({item["id"] for item in result}, {camera["id"], lens["id"]})
        locations = {loc["id"]: loc for loc in self.ok(self.client.get("/api/locations"))}
        self.assertEqual(locations[room["id"]]["item_count"], 2)
        self.assertEqual(locations[box["id"]]["item_count"], 1)
        self.assertEqual(self.current(camera)["location_path"], "家 / 书房 / 器材架 / 摄影箱")
        self.action(lens, "discard")
        locations = {loc["id"]: loc for loc in self.ok(self.client.get("/api/locations"))}
        self.assertEqual(locations[room["id"]]["item_count"], 1)

    def test_quantities_are_exact_nonnegative_integers(self):
        for quantity in (-1, 1.5, True, "1.5"):
            with self.subTest(quantity=quantity):
                self.rejected(self.client.post("/api/items", json={
                    "name": "无效库存", "location_id": self.home["id"], "quantity": quantity,
                }))
        stock = self.item(quantity=2)
        initial_logs = len(self.logs(stock))
        for quantity in (0, -1, 0.5, True, 3):
            with self.subTest(consume=quantity):
                self.rejected(self.client.post(f"/api/items/{stock['id']}/actions", json={
                    "action": "consume", "quantity": quantity,
                }))
        self.assertEqual(self.current(stock)["quantity"], 2)
        self.assertEqual(len(self.logs(stock)), initial_logs)
        self.assertEqual(self.item("空包装", quantity=0)["status"], "depleted")

    def test_moving_preserves_historical_location_and_item_name_snapshots(self):
        first = self.location("工具柜", kind="shelf")
        second = self.location("维修箱", kind="box")
        drill = self.item("电钻", location_id=first["id"], quantity=1)
        moved = self.action(drill, "move", location_id=second["id"], note="整理工具")
        self.assertEqual(moved["location_path"], "家 / 维修箱")
        self.ok(self.client.patch(f"/api/locations/{first['id']}", json={"name": "旧工具柜"}))
        self.ok(self.client.patch(f"/api/locations/{second['id']}", json={"name": "常用工具箱"}))
        self.ok(self.client.patch(f"/api/items/{drill['id']}", json={"name": "充电电钻"}))
        move = next(log for log in self.logs(drill) if log["action"] == "move")
        self.assertEqual(move["item_name"], "电钻")
        self.assertEqual(move["from_location"], "家 / 工具柜")
        self.assertEqual(move["to_location"], "家 / 维修箱")
        self.assertEqual(move["quantity_delta"], 0)
        self.assertEqual(move["note"], "整理工具")
        self.assertEqual(self.current(drill)["location_path"], "家 / 常用工具箱")

    def test_take_lend_and_return_follow_legal_transitions(self):
        destination = self.location("自行车柜", kind="box")
        helmet = self.item("骑行头盔", quantity=2)
        taken = self.action(helmet, "take_out")
        self.assertEqual(taken["status"], "in_use")
        self.assertEqual(self.ok(self.client.get("/api/dashboard"))["in_use_count"], 1)
        self.rejected(self.client.post(f"/api/items/{helmet['id']}/actions", json={"action": "take_out"}))
        self.assertEqual(self.action(helmet, "lend", note="借给家人")["status"], "lent")
        for payload in ({"action": "move", "location_id": destination["id"]}, {"action": "consume", "quantity": 1}):
            self.rejected(self.client.post(f"/api/items/{helmet['id']}/actions", json=payload))
        returned = self.action(helmet, "return", location_id=destination["id"])
        self.assertEqual((returned["status"], returned["quantity"], returned["location_id"]),
                         ("in_stock", 2, destination["id"]))
        self.rejected(self.client.post(f"/api/items/{helmet['id']}/actions", json={"action": "return"}))
        self.assertEqual(self.action(helmet, "lend")["status"], "lent")
        self.assertEqual(self.action(helmet, "return")["status"], "in_stock")
        self.assertEqual(self.current(helmet)["quantity"], 2)

    def test_consumption_restock_and_terminal_states_keep_audit(self):
        tape = self.item("胶带", quantity=5)
        self.assertEqual(self.action(tape, "consume", quantity=2)["quantity"], 3)
        consumed = self.action(tape, "consume", quantity=3)
        self.assertEqual((consumed["quantity"], consumed["status"]), (0, "depleted"))
        restocked = self.action(tape, "restock", quantity=4)
        self.assertEqual((restocked["quantity"], restocked["status"]), (4, "in_stock"))
        discarded = self.action(tape, "discard")
        self.assertEqual((discarded["quantity"], discarded["status"]), (0, "discarded"))
        self.rejected(self.client.post(f"/api/items/{tape['id']}/actions", json={"action": "restock", "quantity": 1}))
        lost = self.item("遗失充电器", quantity=2)
        self.action(lost, "take_out")
        self.assertEqual(self.action(lost, "lost")["status"], "lost")
        self.rejected(self.client.post(f"/api/items/{lost['id']}/actions", json={"action": "return"}))
        self.assertEqual(self.ok(self.client.get("/api/dashboard"))["total_quantity"], 0)
        self.assertEqual({log["action"] for log in self.logs(tape)},
                         {"create", "consume", "restock", "discard"})
        self.assertEqual(sum(log["quantity_delta"] for log in self.logs(tape)), 0)

    def test_expiry_boundaries_use_shanghai_date_and_active_states(self):
        # Shanghai is already January 3 while UTC is still January 2. A fixed
        # instant catches code that accidentally uses the host's calendar day.
        instant = datetime(2026, 1, 2, 16, 30, tzinfo=ZoneInfo("UTC"))

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        self.enterContext(patch("app.main.datetime", FrozenDatetime))
        today = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
        expired = self.item("昨日到期食品", expires_on=(today - timedelta(days=1)).isoformat(), warning_days=0)
        due = self.item("今天到期药品", expires_on=today.isoformat(), warning_days=0)
        boundary = self.item("提醒边界", expires_on=(today + timedelta(days=7)).isoformat(), warning_days=7)
        safe = self.item("提醒之外", expires_on=(today + timedelta(days=8)).isoformat(), warning_days=7)
        no_date = self.item("无保质期")
        self.assertEqual(due["stored_date"], "2026-01-03")
        self.assertEqual((expired["expiry_status"], expired["remaining_days"]), ("expired", -1))
        self.assertEqual((due["expiry_status"], due["remaining_days"]), ("soon", 0))
        self.assertEqual((boundary["expiry_status"], boundary["remaining_days"]), ("soon", 7))
        self.assertEqual((safe["expiry_status"], safe["remaining_days"]), ("safe", 8))
        self.assertEqual((no_date["expiry_status"], no_date["remaining_days"]), ("none", None))
        self.action(due, "take_out")
        self.action(boundary, "lend")
        self.assertEqual(self.ok(self.client.get("/api/dashboard"))["soon_count"], 2)
        self.action(expired, "discard")
        dashboard = self.ok(self.client.get("/api/dashboard"))
        self.assertEqual(dashboard["expired_count"], 0)
        self.assertEqual({item["id"] for item in dashboard["expiring_items"]}, {due["id"], boundary["id"]})
        soon = self.ok(self.client.get("/api/items", params={"expiry": "soon"}))
        self.assertEqual({item["id"] for item in soon}, {due["id"], boundary["id"]})

    def test_hierarchy_rejects_cycles_and_deleting_linked_locations(self):
        room = self.location("储物间")
        box = self.location("配件箱", room["id"], "box")
        for location_id, parent_id in ((room["id"], room["id"]), (room["id"], box["id"]), (box["id"], 999999)):
            self.rejected(self.client.patch(f"/api/locations/{location_id}", json={"parent_id": parent_id}))
        self.rejected(self.client.delete(f"/api/locations/{room['id']}"))
        cable = self.item("旧线材", location_id=box["id"])
        self.action(cable, "discard")
        self.rejected(self.client.delete(f"/api/locations/{box['id']}"))
        locations = {loc["id"]: loc for loc in self.ok(self.client.get("/api/locations"))}
        self.assertEqual(locations[box["id"]]["path"], "家 / 储物间 / 配件箱")

    def test_location_qr_is_real_svg_and_rejects_invalid_origins(self):
        box = self.location("可扫描箱", kind="box")
        response = self.client.get(f"/api/locations/{box['id']}/qr", params={"base_url": "https://nas.example:8443"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("image/svg+xml", response.headers["content-type"])
        root = ElementTree.fromstring(response.content)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        graphics = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"path", "rect"}]
        self.assertTrue(graphics, "QR SVG must contain modules, not only a text label")
        self.assertGreater(len(response.content), 500)
        for base_url in ("javascript:alert(1)", "https://nas.example/path", "https://nas.example/?x=1", "https://user:password@nas.example"):
            with self.subTest(base_url=base_url):
                self.rejected(self.client.get(f"/api/locations/{box['id']}/qr", params={"base_url": base_url}))

    def test_deleted_location_ids_never_redirect_old_printed_labels(self):
        old_box = self.location("旧箱子", kind="box")
        self.ok(self.client.delete(f"/api/locations/{old_box['id']}"))
        new_box = self.location("新箱子", kind="box")
        self.assertNotEqual(new_box["id"], old_box["id"])
        self.rejected(self.client.get("/api/items", params={"location_id": old_box["id"]}))
        self.rejected(self.client.get(f"/api/locations/{old_box['id']}/qr", params={"base_url": "https://nas.example"}))

    def test_legacy_location_schema_upgrade_preserves_data_and_retired_labels(self):
        room = self.location("历史储物间")
        box = self.location("保留箱", room["id"], "box")
        retired = self.location("已撤除箱", kind="box")
        upload = self.ok(self.client.post("/api/uploads", files={"file": ("photo.png", self.png(), "image/png")}))
        stock = self.item("升级前物品", location_id=box["id"], quantity=3, image_url=upload["image_url"])
        self.action(stock, "consume", quantity=1)
        self.ok(self.client.delete(f"/api/locations/{retired['id']}"))
        before = self.current(stock)
        before_logs = self.logs(stock)

        # Reproduce the legacy location schema, which permitted ID
        # reuse. Other tables and uploaded files remain as real API-created data.
        database_path = self.data_dir / "homestorage.sqlite3"
        with sqlite3.connect(database_path) as database:
            database.execute("PRAGMA foreign_keys=OFF")
            database.executescript("""
                BEGIN;
                CREATE TABLE legacy_locations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    code TEXT NOT NULL DEFAULT '',
                    type TEXT NOT NULL,
                    parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT
                );
                INSERT INTO legacy_locations SELECT * FROM locations;
                DROP TABLE locations;
                ALTER TABLE legacy_locations RENAME TO locations;
                COMMIT;
            """)

        with TestClient(create_app(data_dir=self.data_dir, password="", timezone="Asia/Shanghai")) as upgraded:
            self.assertEqual(self.ok(upgraded.get(f"/api/items/{stock['id']}")), before)
            self.assertEqual(self.ok(upgraded.get("/api/logs", params={"item_id": stock["id"]})), before_logs)
            self.assertEqual(upgraded.get(upload["image_url"]).status_code, 200)
            new_box = self.ok(upgraded.post("/api/locations", json={
                "name": "升级后新箱", "type": "box", "parent_id": self.home["id"],
            }))
            self.assertGreater(new_box["id"], retired["id"])
            self.rejected(upgraded.get("/api/items", params={"location_id": retired["id"]}))
        with sqlite3.connect(database_path) as database:
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_photo_upload_checks_content_and_serves_decodable_image(self):
        uploaded = self.ok(self.client.post("/api/uploads", files={
            "file": ("真实照片.png", self.png(), "image/png"),
        }))
        image_url = uploaded["image_url"]
        self.assertTrue(image_url.startswith("/uploads/"))
        response = self.client.get(image_url)
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as image:
            image.load()
            self.assertEqual(image.size, (3, 2))
        item = self.item("带照片的物品", image_url=image_url)
        self.assertEqual(self.current(item)["image_url"], image_url)
        self.rejected(self.client.post("/api/uploads", files={
            "file": ("假照片.png", b"not a picture", "image/png"),
        }))
        # A valid PNG signature and structure still need checksum verification.
        # Pillow raises SyntaxError specifically for a damaged IDAT CRC.
        damaged = bytearray(self.png())
        offset = 8
        while offset < len(damaged):
            chunk_size = int.from_bytes(damaged[offset:offset + 4], "big")
            if damaged[offset + 4:offset + 8] == b"IDAT":
                damaged[offset + 8 + chunk_size] ^= 1
                break
            offset += 12 + chunk_size
        else:
            self.fail("PNG fixture must contain an IDAT chunk")
        corrupted_upload = self.client.post("/api/uploads", files={
            "file": ("损坏校验.png", bytes(damaged), "image/png"),
        })
        self.assertEqual(corrupted_upload.status_code, 415, corrupted_upload.text)
        self.rejected(self.client.post("/api/uploads", files={
            "file": ("超大照片.png", self.png() + bytes(10 * 1024 * 1024), "image/png"),
        }))

    def test_password_auth_cookies_and_cross_origin_mutation_protection(self):
        password = "test-local-password"
        auth_dir = self.data_dir / "authenticated"
        with TestClient(create_app(data_dir=auth_dir, password=password, timezone="Asia/Shanghai")) as client:
            self.assertEqual(self.ok(client.get("/api/auth")), {"authenticated": False, "auth_required": True})
            self.assertEqual(client.get("/health").status_code, 200)
            self.rejected(client.get("/api/items"))
            self.rejected(client.post("/api/login", json={"password": "wrong"}))
            self.rejected(client.post("/api/login", json={"password": password}, headers={"origin": "https://foreign.example"}))
            self.ok(client.post("/api/login", json={"password": password}, headers={"origin": "http://testserver"}))
            self.assertTrue(client.cookies)
            self.assertTrue(self.ok(client.get("/api/auth"))["authenticated"])
            self.assertEqual(self.ok(client.get("/api/items")), [])
            self.rejected(client.post("/api/locations", json={"name": "恶意新增", "type": "room"},
                                      headers={"origin": "https://foreign.example"}))
            upload = self.ok(client.post("/api/uploads", files={"file": ("photo.png", self.png(), "image/png")}))
            self.assertEqual(client.get(upload["image_url"]).status_code, 200)
            self.ok(client.post("/api/logout", headers={"origin": "http://testserver"}))
            self.rejected(client.get(upload["image_url"]))
            self.rejected(client.get("/api/export"))
            self.assertFalse(self.ok(client.get("/api/auth"))["authenticated"])

    def test_backup_and_reopening_preserve_inventory_logs_and_photos(self):
        room = self.location("可持久保存")
        upload = self.ok(self.client.post("/api/uploads", files={"file": ("photo.png", self.png(), "image/png")}))
        original = self.item("持久库存", location_id=room["id"], quantity=7, image_url=upload["image_url"],
                             brand="品牌", tags="家庭,备用", purchase_date="2026-01-01", purchase_price=12.5)
        self.action(original, "consume", quantity=2, note="实际消耗")
        export_response = self.client.get("/api/export")
        backup = self.ok(export_response)
        self.assertIn("attachment", export_response.headers.get("content-disposition", ""))
        self.assertTrue({"version", "exported_at", "locations", "items", "logs"}.issubset(backup))
        saved_item = next(item for item in backup["items"] if item["id"] == original["id"])
        self.assertEqual(saved_item["quantity"], 5)
        self.assertEqual(saved_item["image_url"], upload["image_url"])
        with TestClient(create_app(data_dir=self.data_dir, password="", timezone="Asia/Shanghai")) as reopened:
            item = self.ok(reopened.get(f"/api/items/{original['id']}"))
            self.assertEqual(item["quantity"], 5)
            self.assertEqual(item["location_path"], "家 / 可持久保存")
            self.assertEqual(item["tags"], "家庭,备用")
            self.assertEqual(reopened.get(upload["image_url"]).status_code, 200)
            logs = self.ok(reopened.get("/api/logs", params={"item_id": original["id"]}))
            self.assertEqual({log["action"] for log in logs}, {"create", "consume"})

    def test_search_reads_are_side_effect_free_and_explicit_search_is_logged(self):
        camera = self.item("摄影相机", category="摄影器材")
        self.item("骑行水壶", category="骑行用品")
        before = self.ok(self.client.get("/api/logs"))
        result = self.ok(self.client.get("/api/items", params={"q": "相机", "category": "摄影器材"}))
        self.assertEqual([item["id"] for item in result], [camera["id"]])
        self.assertEqual(self.ok(self.client.get("/api/logs")), before)
        self.assertEqual(self.ok(self.client.post("/api/search", json={"query": "相机"}))["count"], 1)
        searches = [log for log in self.ok(self.client.get("/api/logs")) if log["action"] == "search"]
        self.assertEqual(len(searches), 1)
        self.assertIn("相机", searches[0]["note"])

    def test_metadata_cannot_bypass_stock_and_movement_audit(self):
        room = self.location()
        stock = self.item("原始名称", quantity=3)
        initial_logs = self.logs(stock)
        for payload in ({"quantity": 100}, {"location_id": room["id"]}, {"warning_days": -1},
                        {"warning_days": 3651}, {"expires_on": "2026-02-30"},
                        {"category": "不存在的分类"}, {"image_url": "javascript:alert(1)"}):
            with self.subTest(payload=payload):
                self.rejected(self.client.patch(f"/api/items/{stock['id']}", json=payload))
        unchanged = self.current(stock)
        self.assertEqual((unchanged["quantity"], unchanged["location_id"]), (3, self.home["id"]))
        self.assertEqual(self.logs(stock), initial_logs)
        changed = self.ok(self.client.patch(f"/api/items/{stock['id']}", json={
            "name": "更新后的名称", "notes": "家庭备用", "category": "工具耗材", "warning_days": 0,
        }))
        self.assertEqual((changed["name"], changed["notes"], changed["warning_days"]),
                         ("更新后的名称", "家庭备用", 0))
        self.assertEqual(changed["quantity"], 3)

    def test_competing_consumption_cannot_overdraw_stock(self):
        stock = self.item("共享耗材", quantity=10)

        def consume():
            return self.client.post(f"/api/items/{stock['id']}/actions", json={"action": "consume", "quantity": 8})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: consume(), range(2)))
        successful = [response for response in responses if 200 <= response.status_code < 300]
        rejected = [response for response in responses if 400 <= response.status_code < 500]
        self.assertEqual((len(successful), len(rejected)), (1, 1), [response.text for response in responses])
        self.assertEqual(self.current(stock)["quantity"], 2)
        consumes = [log for log in self.logs(stock) if log["action"] == "consume"]
        self.assertEqual(len(consumes), 1)
        self.assertEqual(consumes[0]["quantity_delta"], -8)

    def test_twenty_concurrent_reads_and_transactions_use_sqlite_safely(self):
        start = Barrier(20)

        def request(index):
            start.wait(timeout=10)
            if index % 3 == 0:
                return self.client.get("/api/locations")
            if index % 3 == 1:
                return self.client.get("/api/items")
            created = self.client.post("/api/items", json={
                "name": f"并发耗材 {index}", "location_id": self.home["id"], "quantity": 10,
            })
            if created.status_code >= 300:
                return created
            return self.client.post(f"/api/items/{created.json()['id']}/actions", json={
                "action": "consume", "quantity": 2,
            })

        with ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(request, range(20)))
        for response in responses:
            self.ok(response)
        items = self.ok(self.client.get("/api/items"))
        self.assertEqual(len(items), 6)
        self.assertEqual(sum(item["quantity"] for item in items), 48)
        for item in items:
            logs = self.logs(item)
            self.assertEqual({log["action"] for log in logs}, {"create", "consume"})
            self.assertEqual(sum(log["quantity_delta"] for log in logs), 8)


if __name__ == "__main__":
    unittest.main()
