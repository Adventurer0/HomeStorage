"""Consistent private ZIPs, permission boundaries and real local WebDAV I/O."""

import asyncio
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError

from app.backups import BACKUP_FORMAT, BackupError, BackupSettingsInput, build_backup_router, initialize_backups, verify_backup
from app.db import connect, initialize


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="homestorage-backup-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.database = self.data / "homestorage.sqlite3"
        initialize(self.database)
        self.connection = lambda: connect(self.database)
        initialize_backups(self.connection)
        (self.data / "uploads").mkdir()
        (self.data / "uploads/photo.jpg").write_bytes(b"fixture-photo-payload")
        (self.data / "uploads/orphan.jpg").write_bytes(b"not-referenced")
        (self.data / "session.key").write_bytes(b"x" * 32)
        (self.data / "private-unrelated.txt").write_text("unrelated-file")
        with closing(self.connection()) as db, db:
            location = db.execute("INSERT INTO locations(name,type) VALUES('家','home')").lastrowid
            category = db.execute("SELECT id FROM categories ORDER BY id LIMIT 1").fetchone()[0]
            db.execute("""INSERT INTO items(name,category_id,location_id,quantity,stored_date,
                       image_url,created_at,updated_at) VALUES('测试物品',?,?,3,'2026-10-17',
                       '/uploads/photo.jpg','2026-10-17T00:00:00Z','2026-10-17T00:00:00Z')""", (category, location))
        self.maintenance = False

        def require_admin(request):
            role = request.headers.get("x-test-role", "admin")
            if role == "none":
                raise HTTPException(401, "请先登录")
            if role != "admin":
                raise HTTPException(403, "需要家庭管理员登录")
            return {"id": 1, "role": "admin"}

        self.services = {"connect": self.connection, "data_dir": self.data, "timezone": "Asia/Shanghai",
                         "require_admin": require_admin, "maintenance_active": lambda: self.maintenance}
        self.router = build_backup_router(self.services)
        self.manager = self.router.backup_manager

        @asynccontextmanager
        async def lifespan(_app):
            await self.manager.start()
            yield
            await self.manager.stop()

        self.app = FastAPI(lifespan=lifespan)
        self.app.include_router(self.router)
        self.client = self.enterContext(TestClient(self.app))

    def ok(self, response):
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def save(self, **body):
        return self.ok(self.client.patch("/api/backups/settings", json=body))

    def drain(self):
        async def wait_for_threads():
            if self.manager._tasks:
                await asyncio.gather(*tuple(self.manager._tasks))
        self.client.portal.call(wait_for_threads)

    def make_backup(self, upload=False):
        job = self.ok(self.client.post("/api/backups", json={"confirmed": True, "upload": upload}))
        self.drain()
        return self.ok(self.client.get("/api/backups/jobs/" + job["id"]))

    def successful_backup(self, upload=False):
        job = self.make_backup(upload)
        self.assertEqual(job["status"], "succeeded", job)
        return job

    def test_full_snapshot_manifest_and_exclusions(self):
        self.save(password="private-webdav-password", username="private-user", webdav_url="https://dav.example/dav/backup/")
        job = self.successful_backup()
        response = self.client.get(f"/api/backups/{job['backup_id']}/download")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"homestorage.sqlite3", "uploads/photo.jpg", "session.key", "manifest.json"})
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], BACKUP_FORMAT)
            self.assertTrue(manifest["private"])
            for entry in manifest["files"]:
                content = archive.read(entry["path"])
                self.assertEqual(len(content), entry["size"])
                self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"])
            restored = self.data / "restore-validation.sqlite3"
            restored.write_bytes(archive.read("homestorage.sqlite3"))
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT name,quantity FROM items").fetchone(), ("测试物品", 3))
                self.assertIn("private-webdav-password", db.execute("SELECT value FROM backup_configuration").fetchone()[0])
            self.assertEqual(archive.read("session.key"), b"x" * 32)
        listing = self.ok(self.client.get("/api/backups"))
        self.assertEqual(len(listing["backups"]), 1)
        self.assertIsNone(listing["active_job"])
        self.assertEqual(listing["last_job"]["status"], "succeeded")
        self.assertNotIn("private-webdav-password", json.dumps(listing))

    def test_admin_and_external_token_boundaries_cover_all_routes(self):
        job = self.successful_backup()
        for role, expected in (("none", 401), ("member", 403), ("viewer", 403)):
            headers = {"x-test-role": role}
            for path in ("/api/backups", "/api/backups/settings", "/api/backups/jobs/" + job["id"],
                         "/api/backups/" + job["backup_id"] + "/download"):
                self.assertEqual(self.client.get(path, headers=headers).status_code, expected)
            self.assertEqual(self.client.post("/api/backups", json={"confirmed": True}, headers=headers).status_code, expected)
        self.assertEqual(self.client.get("/api/backups", headers={"Authorization": "Bearer external-key"}).status_code, 403)
        self.assertEqual(self.client.patch("/api/backups/settings", json={"keep_count": 4},
                                          headers={"Authorization": "Bearer external-key"}).status_code, 403)

    def test_password_semantics_and_target_change_clear_credentials(self):
        saved = self.save(webdav_url="https://dav.example/dav/备份", username="first", password="first-private")
        self.assertTrue(saved["password_set"])
        self.assertNotIn("first-private", json.dumps(saved))
        self.assertIn("%E5%A4%87%E4%BB%BD", saved["webdav_url"])
        self.save(password="", keep_count=9)
        self.assertEqual(self.manager.settings()["password"], "first-private")
        self.save(username="second")
        self.assertEqual(self.manager.settings()["password"], "")
        self.save(password="second-private")
        self.save(webdav_url="https://other.example/dav/backups/", password="replacement-private")
        self.assertEqual(self.manager.settings()["password"], "replacement-private")
        self.save(password=None)
        self.assertFalse(self.ok(self.client.get("/api/backups/settings"))["password_set"])

    def test_settings_validation_and_lan_http_warning(self):
        for url in ("ftp://nas/dav/a/", "https://user:secret@nas/dav/a/", "https://nas/", "https://nas/dav/a?key=secret",
                    "https://nas/dav/a#secret", "https://nas/dav/../a/", "https://nas/dav/%2e%2e/a/",
                    "https://nas/dav/%00a/", "https://nas:99999/dav/a/", "https://nas/dav/a\\b/"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    BackupSettingsInput(webdav_url=url)
        for settings in ({"time": "24:00"}, {"keep_count": 0}, {"keep_count": 31}, {"auto_backup": None},
                         {"auto_upload": "true"}, {"password": "newline\nsecret"}, {"unknown": True}):
            with self.assertRaises(ValidationError):
                BackupSettingsInput(**settings)
        self.assertEqual(self.client.patch("/api/backups/settings", json={"auto_upload": True}).status_code, 422)
        self.assertTrue(self.save(webdav_url="http://192.168.1.2:5244/dav/backups/")["http_warning"])

    def test_explicit_confirmation_and_fixed_catalogue_paths(self):
        self.assertEqual(self.client.post("/api/backups", json={"confirmed": False}).status_code, 400)
        self.assertEqual(self.client.get("/api/backups/unknown/download").status_code, 404)
        self.assertEqual(self.client.get("/api/backups/jobs/not-an-id").status_code, 404)
        job = self.successful_backup()
        response = self.client.request("DELETE", f"/api/backups/{job['backup_id']}", json={"confirmed": False})
        self.assertEqual(response.status_code, 400)
        self.ok(self.client.request("DELETE", f"/api/backups/{job['backup_id']}", json={"confirmed": True}))
        self.assertEqual(self.ok(self.client.get("/api/backups"))["backups"], [])

    def test_missing_or_symlink_photo_never_produces_successful_package(self):
        photo = self.data / "uploads/photo.jpg"
        photo.unlink()
        job = self.make_backup()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(list(self.manager.directory.iterdir()), [])
        photo.symlink_to(self.data / "session.key")
        job = self.make_backup()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.ok(self.client.get("/api/backups"))["backups"], [])

    def test_disk_space_and_invalid_key_fail_safely(self):
        with patch("app.backups.shutil.disk_usage") as usage:
            usage.return_value.free = 0
            job = self.make_backup()
        self.assertEqual(job["status"], "failed")
        self.assertIn("空间不足", job["error"])
        (self.data / "session.key").write_bytes(b"short")
        self.assertEqual(self.make_backup()["status"], "failed")
        self.assertEqual(list(self.manager.directory.iterdir()), [])

    def test_retention_keeps_latest_completed_backup_only(self):
        self.save(keep_count=1)
        first = self.successful_backup()["backup_id"]
        second = self.successful_backup()["backup_id"]
        listing = self.ok(self.client.get("/api/backups"))["backups"]
        self.assertEqual([backup["id"] for backup in listing], [second])
        self.assertEqual(self.client.get(f"/api/backups/{first}/download").status_code, 404)
        self.assertEqual(len(list(self.manager.directory.glob("*.zip"))), 1)

    def test_open_download_survives_concurrent_retention_or_deletion(self):
        job = self.successful_backup()
        _, path = self.manager.backup(job["backup_id"])
        expected = path.read_bytes()
        response = self.manager.download(job["backup_id"])
        self.manager.delete(job["backup_id"])
        self.assertFalse(path.exists())

        async def consume():
            try:
                return b"".join([chunk async for chunk in response.body_iterator])
            finally:
                await response.background()

        self.assertEqual(self.client.portal.call(consume), expected)
        self.assertEqual(int(response.headers["content-length"]), len(expected))
        self.assertEqual(self.client.get(f"/api/backups/{job['backup_id']}/download").status_code, 404)

    def test_changed_local_archive_is_not_downloaded_as_a_complete_backup(self):
        job = self.successful_backup()
        _, path = self.manager.backup(job["backup_id"])
        with path.open("ab") as target:
            target.write(b"changed")
        response = self.client.get(f"/api/backups/{job['backup_id']}/download")
        self.assertEqual(response.status_code, 409)
        self.assertIn("发生变化", response.json()["detail"])

    def test_no_parallel_jobs_or_settings_mutation_during_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        original = self.manager._create_backup

        def blocked(job_id):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release worker")
            return original(job_id)

        with patch.object(self.manager, "_create_backup", side_effect=blocked):
            self.ok(self.client.post("/api/backups", json={"confirmed": True}))
            self.assertTrue(entered.wait(timeout=5))
            try:
                self.assertEqual(self.client.post("/api/backups", json={"confirmed": True}).status_code, 409)
                self.assertEqual(self.client.patch("/api/backups/settings", json={"keep_count": 2}).status_code, 409)
            finally:
                release.set()
                self.drain()

    def test_schedule_uses_timezone_once_per_day_and_does_not_replay_missed_time(self):
        self.save(auto_backup=True, time="03:30")
        before = datetime(2026, 10, 16, 19, 29, tzinfo=timezone.utc)
        due = datetime(2026, 10, 16, 19, 30, tzinfo=timezone.utc)
        after = datetime(2026, 10, 16, 19, 31, tzinfo=timezone.utc)
        self.assertIsNone(self.client.portal.call(self.manager.tick, before))
        self.assertIsNotNone(self.client.portal.call(self.manager.tick, due))
        self.drain()
        self.assertIsNone(self.client.portal.call(self.manager.tick, due))
        self.assertIsNone(self.client.portal.call(self.manager.tick, after))
        self.assertEqual(len(self.ok(self.client.get("/api/backups"))["backups"]), 1)
        self.save(auto_backup=False)
        self.assertIsNone(self.client.portal.call(self.manager.tick, due.replace(day=17)))

    def test_maintenance_blocks_manual_and_scheduled_backups(self):
        self.save(auto_backup=True)
        self.maintenance = True
        self.assertEqual(self.client.post("/api/backups", json={"confirmed": True}).status_code, 503)
        self.assertEqual(self.client.patch("/api/backups/settings", json={"keep_count": 3}).status_code, 503)
        self.assertIsNone(self.client.portal.call(self.manager.tick, datetime(2026, 10, 16, 19, 30, tzinfo=timezone.utc)))

    def test_restart_marks_interrupted_jobs_failed_without_uploading(self):
        with closing(self.connection()) as db, db:
            db.execute("INSERT INTO backup_jobs VALUES(?,?,?,?,?,?,?,?,?)", ("f" * 32, "backup", "running", "upload", "{}", None, None, "2026", "2026"))
        self.client.portal.call(self.manager.stop)
        with patch.object(self.manager, "_upload") as upload:
            self.client.portal.call(self.manager.start)
            job = self.ok(self.client.get("/api/backups/jobs/" + "f" * 32))
            self.assertEqual(job["status"], "failed")
            self.assertIn("重启", job["error"])
            upload.assert_not_called()

    def webdav(self, status=201):
        observations = []

        class Handler(BaseHTTPRequestHandler):
            def do_MKCOL(self):
                observations.append(("MKCOL", self.path, self.headers.get("Authorization"), b""))
                self.send_response(405)
                self.end_headers()

            def do_PUT(self):
                content = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                observations.append(("PUT", self.path, self.headers.get("Authorization"), content))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"remote-secret-body-must-never-be-reflected")

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.save(webdav_url=f"http://127.0.0.1:{server.server_port}/dav/backups/", username="fixture-user", password="fixture-private-password")
        return observations

    def test_real_local_webdav_upload_is_complete_and_private(self):
        requests = self.webdav()
        job = self.successful_backup(upload=True)
        self.assertEqual([request[0] for request in requests], ["MKCOL", "MKCOL", "PUT"])
        self.assertTrue(all(request[2].startswith("Basic ") for request in requests))
        uploaded = requests[-1][3]
        local = self.client.get(f"/api/backups/{job['backup_id']}/download").content
        self.assertEqual(uploaded, local)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(uploaded)))
        listing = self.ok(self.client.get("/api/backups"))
        self.assertIsNotNone(listing["backups"][0]["uploaded_at"])
        self.assertNotIn("fixture-private-password", json.dumps(listing))

    def test_explicit_automatic_upload_authorization_reaches_selected_webdav(self):
        requests = self.webdav()
        self.save(auto_backup=True, auto_upload=True, time="03:30")
        due = datetime(2026, 10, 16, 19, 30, tzinfo=timezone.utc)
        self.assertIsNotNone(self.client.portal.call(self.manager.tick, due))
        self.drain()
        self.assertEqual([request[0] for request in requests], ["MKCOL", "MKCOL", "PUT"])
        self.assertEqual(self.ok(self.client.get("/api/backups"))["last_job"]["status"], "succeeded")

    def test_remote_denial_and_connection_failure_are_safe_and_keep_local_backup(self):
        self.webdav(status=403)
        job = self.make_backup(upload=True)
        self.assertEqual(job["status"], "failed")
        self.assertIn("认证失败", job["error"])
        self.assertNotIn("remote-secret", json.dumps(job))
        self.assertNotIn("fixture-private-password", json.dumps(job))
        self.assertEqual(self.client.get(f"/api/backups/{job['backup_id']}/download").status_code, 200)
        with patch("app.backups.httpx.Client") as client:
            client.return_value.__enter__.return_value.stream.side_effect = httpx.ConnectError("https://user:private@remote.example/body-secret")
            request = self.ok(self.client.post(f"/api/backups/{job['backup_id']}/upload", json={"confirmed": True}))
            self.drain()
        result = self.ok(self.client.get("/api/backups/jobs/" + request["id"]))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private@", json.dumps(result))

    def test_changed_local_backup_is_not_uploaded(self):
        requests = self.webdav()
        job = self.successful_backup()
        _, path = self.manager.backup(job["backup_id"])
        with path.open("ab") as target:
            target.write(b"modified")
        upload = self.ok(self.client.post(f"/api/backups/{job['backup_id']}/upload", json={"confirmed": True}))
        self.drain()
        failed = self.ok(self.client.get("/api/backups/jobs/" + upload["id"]))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("校验失败", failed["error"])
        self.assertEqual(requests, [])

    def test_webdav_redirect_is_rejected_and_credentials_are_not_forwarded(self):
        requests = self.webdav(status=302)
        job = self.make_backup(upload=True)
        self.assertEqual(job["status"], "failed")
        self.assertIn("重定向", job["error"])
        self.assertEqual([request[0] for request in requests], ["MKCOL", "MKCOL", "PUT"])
        self.assertIsNotNone(job["backup_id"])

    def test_startup_removes_only_unpublished_owned_artifacts(self):
        self.client.portal.call(self.manager.stop)
        orphan = self.manager.directory / ("homestorage-20261017T010101Z-" + "a" * 32 + ".zip")
        orphan.write_bytes(b"unpublished-private-data")
        other = self.manager.directory / "user-file.zip"
        other.write_bytes(b"leave-user-file")
        self.client.portal.call(self.manager.start)
        self.assertFalse(orphan.exists())
        self.assertTrue(other.exists())

    def test_offline_manifest_verification_rejects_modified_payload_and_unsafe_entries(self):
        job = self.successful_backup()
        _, path = self.manager.backup(job["backup_id"])
        self.assertEqual(verify_backup(path)["format"], BACKUP_FORMAT)
        damaged = self.data / "damaged.zip"
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(damaged, "w") as target:
            for entry in source.infolist():
                content = source.read(entry.filename)
                if entry.filename == "uploads/photo.jpg":
                    content = b"x" * len(content)
                target.writestr(entry.filename, content)
        with self.assertRaisesRegex(BackupError, "校验失败"):
            verify_backup(damaged)
        with zipfile.ZipFile(damaged, "w") as target:
            target.writestr("../outside.txt", "unsafe")
            target.writestr("manifest.json", "{}")
        with self.assertRaisesRegex(BackupError, "不安全"):
            verify_backup(damaged)


if __name__ == "__main__":
    unittest.main()
