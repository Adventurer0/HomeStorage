"""Inventory navigation regressions for large and reorganized storage trees."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.db import connect, transaction
from app.main import create_app


class InventoryNavigationTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix="homestorage-navigation-"))
        self.data_dir = Path(directory)
        self.client = self.enterContext(TestClient(create_app(data_dir=self.data_dir, password="")))
        self.home = self.ok(self.client.get("/api/locations"))[0]

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def location(self, name, parent_id=None):
        return self.ok(self.client.post("/api/locations", json={
            "name": name, "parent_id": self.home["id"] if parent_id is None else parent_id,
        }))

    def item(self, name, **values):
        return self.ok(self.client.post("/api/items", json={
            "name": name, "location_id": self.home["id"], **values,
        }))

    def action(self, item, action, **values):
        return self.ok(self.client.post(f"/api/items/{item['id']}/actions", json={
            "action": action, **values,
        }))

    def test_deep_locations_remain_readable_filterable_and_exportable(self):
        database = connect(self.data_dir / "homestorage.sqlite3")
        self.addCleanup(database.close)
        parent = self.home["id"]
        with transaction(database):
            # More than Python's normal recursion limit; legacy data may already
            # have this shape, so reading it must not depend on recursion depth.
            for index in range(1100):
                parent = database.execute(
                    "INSERT INTO locations(name,type,parent_id) VALUES (?,?,?)",
                    (f"层{index}", "other", parent),
                ).lastrowid
        item = self.item("最深处物品", location_id=parent, quantity=7)
        expected_path = "家 / " + " / ".join(f"层{index}" for index in range(1100))
        self.assertEqual(item["location_path"], expected_path)
        locations = self.ok(self.client.get("/api/locations"))
        self.assertEqual(len(locations), 1101)
        self.assertTrue(all(row["item_count"] == 1 for row in locations))
        self.assertEqual(locations[-1]["path"], expected_path)
        items = self.ok(self.client.get("/api/items", params={"location_id": self.home["id"]}))
        self.assertEqual([row["id"] for row in items], [item["id"]])
        self.assertEqual(self.ok(self.client.get("/api/dashboard"))["total_quantity"], 7)
        exported = self.ok(self.client.get("/api/export"))
        self.assertEqual(exported["items"][0]["location_path"], expected_path)
        self.assertEqual(exported["locations"][-1]["item_count"], 1)

    def test_location_counts_after_reparenting_include_active_records_only(self):
        older = self.location("先创建的收纳箱")
        child = self.location("箱内夹层", older["id"])
        newer = self.location("后创建的房间")
        self.ok(self.client.patch(f"/api/locations/{older['id']}", json={"parent_id": newer["id"]}))
        direct = self.item("房间物品", location_id=newer["id"])
        in_use = self.item("正在使用", location_id=older["id"], quantity=3)
        self.action(in_use, "take_out")
        lent = self.item("已借出", location_id=child["id"], quantity=4)
        self.action(lent, "lend")
        discarded = self.item("已丢弃", location_id=child["id"])
        self.action(discarded, "discard")
        lost = self.item("已遗失", location_id=child["id"])
        self.action(lost, "lost")
        self.item("已耗尽", location_id=child["id"], quantity=0)
        archived = self.item("已归档", location_id=child["id"])
        self.ok(self.client.post(f"/api/items/{archived['id']}/archive", json={"confirmed": True}))
        locations = {row["id"]: row for row in self.ok(self.client.get("/api/locations"))}
        self.assertEqual(locations[self.home["id"]]["item_count"], 3)
        self.assertEqual(locations[newer["id"]]["item_count"], 3)
        self.assertEqual(locations[older["id"]]["item_count"], 2)
        self.assertEqual(locations[child["id"]]["item_count"], 1)
        self.assertEqual(locations[child["id"]]["path"], "家 / 后创建的房间 / 先创建的收纳箱 / 箱内夹层")
        self.ok(self.client.post(f"/api/items/{archived['id']}/restore", json={"confirmed": True}))
        self.assertEqual(next(row for row in self.ok(self.client.get("/api/locations"))
                              if row["id"] == newer["id"])["item_count"], 4)

    def test_combined_filters_preserve_archive_order_and_casefolded_multiword_search(self):
        room = self.location("书房")
        box = self.location("USB 配件箱", room["id"])
        category = self.ok(self.client.post("/api/categories", json={"name": "个性配件"}))
        fields = {
            "category_id": category["id"], "location_id": box["id"],
            "brand": "Straße", "model": "X-2", "tags": "备用线材", "notes": "蓝色编织线",
        }
        first = self.item("数据线一", **fields)
        second = self.item("数据线二", **fields)
        taken = self.item("取出数据线", **fields)
        self.action(taken, "take_out")
        archived = self.item("归档数据线", **fields)
        self.ok(self.client.post(f"/api/items/{archived['id']}/archive", json={"confirmed": True}))
        self.item("其他位置线材", **{**fields, "location_id": self.home["id"]})
        self.item("其他分类线材", **{**fields, "category_id": None, "category": "其他"})
        query = {
            "q": "  STRASSE\t蓝色\n备用 USB  ", "location_id": room["id"],
            "category_id": category["id"], "category": category["name"], "status": "in_stock",
        }
        response = self.ok(self.client.get("/api/items", params=query))
        self.assertEqual([row["id"] for row in response], [second["id"], first["id"]])
        response = self.ok(self.client.get("/api/items", params={**query, "archived": "true"}))
        self.assertEqual([row["id"] for row in response], [archived["id"]])
        response = self.ok(self.client.get("/api/items", params={**query, "archived": "all"}))
        self.assertEqual([row["id"] for row in response], [archived["id"], second["id"], first["id"]])
        self.assertEqual(self.ok(self.client.get("/api/items", params={**query, "category": "其他"})), [])
        self.assertEqual(len(self.ok(self.client.get("/api/items", params={"q": " \t "}))), 5)
        for invalid in ({"location_id": 999999}, {"category_id": 999999}):
            self.assertEqual(self.client.get("/api/items", params=invalid).status_code, 404)


if __name__ == "__main__":
    unittest.main()
