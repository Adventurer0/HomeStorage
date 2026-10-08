"""Controlled-update safety tests; GitHub and Docker are deterministic fakes."""

from copy import deepcopy
import gzip
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import tarfile
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from updater.state import StateStore, ensure_token


VERSION = "2.1.1"
REVISION = "c" * 40


def docker_archive(version=VERSION):
    layer_output = BytesIO()
    with tarfile.open(fileobj=layer_output, mode="w"):
        pass
    layer = layer_output.getvalue()
    layer_name = hashlib.sha256(layer).hexdigest() + "/layer.tar"
    configuration = {
        "architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]},
        "config": {"Labels": {"org.opencontainers.image.version": version, "org.opencontainers.image.revision": REVISION}},
    }
    config_bytes = json.dumps(configuration).encode()
    config_name = hashlib.sha256(config_bytes).hexdigest() + ".json"
    manifest = [{"Config": config_name, "RepoTags": [f"homestorage:{version}-amd64"], "Layers": [layer_name]}]
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in ((config_name, config_bytes), ("manifest.json", json.dumps(manifest).encode()), (layer_name, layer)):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, BytesIO(content))
    return gzip.compress(output.getvalue(), mtime=0)


def stable_release(version=VERSION):
    return {
        "version": version, "revision": REVISION,
        "image": f"homestorage:{version}-amd64",
        "asset_url": f"https://github.com/Adventurer0/HomeStorage/releases/download/v{version}/homestorage-{version}-linux-amd64.tar.gz",
        "sha256": "d" * 64, "size": 100,
        "notes": "测试更新", "url": f"https://github.com/Adventurer0/HomeStorage/releases/tag/v{version}",
    }


class FakeReleases:
    def __init__(self):
        self.release = stable_release()
        self.entered = None
        self.resume = None

    def latest(self, arch):
        if arch != "amd64":
            raise ValueError("不支持的架构")
        return deepcopy(self.release)

    def download(self, release, destination, progress_callback):
        if self.entered:
            self.entered.set()
            if not self.resume.wait(5):
                raise RuntimeError("测试下载等待超时")
        Path(destination).write_bytes(docker_archive(release["version"]))
        progress_callback(release["size"], release["size"])


class FakeDocker:
    """A small container model; all mutations are logged for isolation assertions."""
    def __init__(self, data_dir, state_dir):
        self.data_dir = Path(data_dir)
        self.state_dir = Path(state_dir)
        self.events = []
        self.architecture = "x86_64"
        self.fail_new_health = False
        self.fail_old_start = False
        self.migrate_on_start = False
        self.new_counter = 0
        self.before_create = None
        self.container_map = {
            "helper": self._container("helper", "updater", "/app-data"),
            "old": self._container("old", "homestorage", "/data"),
        }
        self.image_metadata = {
            "Id": "sha256:" + "e" * 64,
            "Architecture": "amd64", "Os": "linux",
            "Config": {"Labels": {
                "org.opencontainers.image.version": VERSION,
                "org.opencontainers.image.revision": REVISION,
            }},
        }

    def _container(self, identity, service, destination):
        role = "app" if service == "homestorage" else "updater"
        labels = {
            "com.docker.compose.project": "hs-tests",
            "com.docker.compose.service": service,
            "io.homestorage.role": role,
            "org.opencontainers.image.version": "2.1.0",
            "org.opencontainers.image.revision": "b" * 40,
        }
        env = [
            "HOME_STORAGE_PASSWORD=fixture-private-password",
            "AI_API_KEY=fixture-private-key", "AI_MODEL=custom-model",
            "APP_TIMEZONE=Asia/Shanghai", "DATA_DIR=/data",
            "HOMESTORAGE_VERSION=2.1.0", "HOMESTORAGE_REVISION=" + "b" * 40,
        ]
        mount = {"Type": "bind", "Source": str(self.data_dir), "Destination": destination, "RW": True, "Propagation": "rprivate", "Mode": "rw"}
        return {
            "Id": identity, "Name": "/" + service,
            "Config": {
                "Image": "homestorage:local", "Env": env, "Labels": labels,
                "User": "1000:1000" if role == "app" else "0:0",
                "Entrypoint": None, "Cmd": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
                "WorkingDir": "/app", "ExposedPorts": {"8000/tcp": {}},
                "Healthcheck": {"Test": ["CMD", "python", "-c", "assert True"]},
            },
            "HostConfig": {
                "Binds": [str(self.data_dir) + ":" + destination + ":rw"],
                "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "18080"}]},
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"], "Tmpfs": {"/tmp": "rw,nosuid,size=64m"},
                "NetworkMode": "hs-tests_default",
            },
            "Mounts": [mount],
            "NetworkSettings": {"Networks": {"hs-tests_default": {"Aliases": [service, identity], "IPAMConfig": None}}},
            "State": {"Running": True, "Status": "running", "Health": {"Status": "healthy"}},
        }

    def info(self):
        return {"Architecture": self.architecture, "OSType": "linux"}

    def inspect(self, identity):
        if identity in self.container_map:
            return deepcopy(self.container_map[identity])
        for row in self.container_map.values():
            if row["Name"].lstrip("/") == str(identity).lstrip("/"):
                return deepcopy(row)
        raise RuntimeError("No such container")

    def list_containers(self, *, all=True, filters=None):
        rows = []
        for row in self.container_map.values():
            labels = row["Config"]["Labels"]
            wanted = (filters or {}).get("label", [])
            if not isinstance(wanted, list):
                wanted = [wanted]
            if any(labels.get(entry.split("=", 1)[0]) != entry.split("=", 1)[1] for entry in wanted if "=" in entry):
                continue
            if all or row["State"]["Running"]:
                rows.append({"Id": row["Id"], "Names": [row["Name"]], "Labels": deepcopy(labels), "State": row["State"]["Status"]})
        return rows

    def load_image(self, path):
        assert Path(path).is_file()
        self.events.append(("load", str(path)))

    def inspect_image(self, image):
        return deepcopy(self.image_metadata)

    def create(self, config, *, name):
        if self.before_create:
            self.before_create()
        self.new_counter += 1
        identity = "new-" + str(self.new_counter)
        row = self._container(identity, "homestorage", "/data")
        row["Name"] = "/" + name
        row["Config"] = deepcopy({key: value for key, value in config.items() if key not in {"HostConfig", "NetworkingConfig"}})
        row["HostConfig"] = deepcopy(config.get("HostConfig", {}))
        row["State"] = {"Running": False, "Status": "created", "Health": {"Status": "starting"}}
        self.container_map[identity] = row
        self.events.append(("create", identity, deepcopy(config)))
        return identity

    def start(self, identity):
        self.events.append(("start", identity))
        if identity == "old" and self.fail_old_start:
            raise RuntimeError("fixture-private-key must not be returned")
        row = self.container_map[identity]
        row["State"] = {"Running": True, "Status": "running", "Health": {"Status": "unhealthy" if identity.startswith("new-") and self.fail_new_health else "healthy"}}
        if identity.startswith("new-") and self.migrate_on_start:
            (self.data_dir / "homestorage.sqlite3").write_bytes(b"new-version-data")
            (self.data_dir / "created-by-new-version").write_text("new")

    def stop(self, identity, timeout=30):
        self.events.append(("stop", identity))
        self.container_map[identity]["State"]["Running"] = False
        self.container_map[identity]["State"]["Status"] = "exited"

    def rename(self, identity, name):
        self.events.append(("rename", identity, name))
        self.container_map[identity]["Name"] = "/" + name

    def remove(self, identity, force=False):
        self.events.append(("remove", identity))
        del self.container_map[identity]


class UpdateStateTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="homestorage-update-state-")
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)

    def test_automatic_updates_are_opt_in_and_survive_restart(self):
        store = StateStore(self.state_dir)
        self.assertIs(store.get()["auto_update"], False)
        store.update(auto_update=True)
        self.assertIs(StateStore(self.state_dir).get()["auto_update"], True)
        store.update(auto_update=False)
        self.assertIs(StateStore(self.state_dir).get()["auto_update"], False)
        self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(store.backups_dir.stat().st_mode), 0o700)

    def test_failed_state_write_keeps_previous_durable_preferences(self):
        store = StateStore(self.state_dir)
        store.update(auto_update=True)
        with patch("updater.state.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                store.update(auto_update=False)
        self.assertIs(store.get()["auto_update"], True)
        self.assertIs(StateStore(self.state_dir).get()["auto_update"], True)
        self.assertEqual(list(self.state_dir.glob(".state-*")), [])

    def test_state_corruption_is_preserved_instead_of_enabling_updates(self):
        path = self.state_dir / "state.json"
        for value in ("not JSON", '{"auto_update":1}', '{"schema_version":999}'):
            with self.subTest(value=value):
                path.write_text(value)
                with self.assertRaises(RuntimeError):
                    StateStore(self.state_dir)
                self.assertEqual(path.read_text(), value)

    def test_token_is_stable_private_and_rejects_symlinks(self):
        token = ensure_token(self.state_dir, os.getuid(), os.getgid())
        self.assertEqual(len(token), 64)
        self.assertEqual(ensure_token(self.state_dir, os.getuid(), os.getgid()), token)
        self.assertEqual(stat.S_IMODE((self.state_dir / "token").stat().st_mode), 0o640)
        (self.state_dir / "token").unlink()
        outside = self.state_dir / "outside-secret"
        outside.write_text(token)
        (self.state_dir / "token").symlink_to(outside)
        with self.assertRaises((OSError, RuntimeError)):
            ensure_token(self.state_dir, os.getuid(), os.getgid())
        self.assertEqual(outside.read_text(), token)


class UpdateWebAPITest(unittest.TestCase):
    def setUp(self):
        from app.main import create_app

        self.directory = TemporaryDirectory(prefix="homestorage-update-web-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state_dir = self.root / "state"
        self.token = ensure_token(self.state_dir, os.getuid(), os.getgid())
        self.enterContext(patch.dict(os.environ, {
            "UPDATE_SERVICE_URL": "http://updater:9080", "UPDATE_STATE_DIR": str(self.state_dir),
            "AI_API_KEY": "", "AI_TRANSCRIPTION_MODEL": "",
            "HOMESTORAGE_VERSION": "2.1.0", "HOMESTORAGE_REVISION": "b" * 40,
        }))
        self.proxy = self.enterContext(patch("app.updates.call_updater", new_callable=AsyncMock))
        self.proxy.return_value = {
            "available": True, "current_version": "999.0.0", "current_revision": "untrusted",
            "auto_update": False, "update_available": True, "latest_version": VERSION,
            "release_notes": "新功能", "release_url": stable_release()["url"],
            "phase": "idle", "message": "可更新", "backup_count": 0,
            "progress": {"downloaded_bytes": 0, "total_bytes": 100},
            "journal": {"Env": ["AI_API_KEY=fixture-private-key"]}, "token": self.token,
        }
        self.app = create_app(data_dir=self.root / "data", password="fixture-password", timezone="Asia/Shanghai")
        self.client = self.enterContext(TestClient(self.app))

    def login(self):
        response = self.client.post("/api/login", json={"password": "fixture-password"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_all_update_controls_require_login_and_same_origin(self):
        calls = (
            ("get", "/api/settings/updates", None),
            ("post", "/api/settings/updates/check", {}),
            ("patch", "/api/settings/updates", {"auto_update": True}),
            ("post", "/api/settings/updates/install", {"confirmed": True, "version": VERSION}),
        )
        for method, route, payload in calls:
            with self.subTest(route=route, method=method):
                kwargs = {"json": payload} if payload is not None else {}
                self.assertEqual(getattr(self.client, method)(route, **kwargs).status_code, 401)
        self.assertEqual(self.proxy.await_count, 0)
        self.login()

        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.client.post("/api/settings/updates/install", json={"confirmed": True, "version": VERSION}, headers=headers).status_code, 403)
        self.assertEqual(self.proxy.await_count, 0)
        self.assertEqual(self.client.post("/api/settings/updates/check", json={}, headers={"Origin": "http://testserver"}).status_code, 200)

    def test_manual_update_requires_literal_confirmation_and_no_client_targets(self):
        self.login()
        for body in (
            {"confirmed": False, "version": VERSION}, {"confirmed": 1, "version": VERSION},
            {"confirmed": "true", "version": VERSION}, {"confirmed": True, "version": "latest"},
            {"confirmed": True, "version": "2.1.1; touch /tmp/pwned"},
            {"confirmed": True, "version": VERSION, "url": "http://evil.example/image.tar.gz"},
            {"confirmed": True, "version": VERSION, "image": "evil/app:latest"},
            {"confirmed": True, "version": VERSION, "container_id": "other-app"},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/settings/updates/install", json=body).status_code, 422)
        for body in ({"auto_update": 1}, {"auto_update": "true"}, {"auto_update": True, "command": "docker rm other"}):
            self.assertEqual(self.client.patch("/api/settings/updates", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/settings/updates/check", json={"url": "http://evil.example"}).status_code, 422)
        self.assertEqual(self.proxy.await_count, 0)
        response = self.client.post("/api/settings/updates/install", json={"confirmed": True, "version": VERSION})
        self.assertEqual(response.status_code, 200, response.text)
        self.proxy.assert_awaited_once_with("http://updater:9080", self.token, "POST", "/v1/install", {"confirmed": True, "version": VERSION})

    def test_status_projects_safe_fields_and_reports_actual_application_version(self):
        self.login()
        self.proxy.return_value["message"] = "token=" + self.token
        status = self.client.get("/api/settings/updates")
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertEqual(body["current_version"], "2.1.0")
        self.assertEqual(body["current_revision"], "b" * 40)
        self.assertNotIn("journal", body)
        self.assertNotIn("token", body)
        self.assertNotIn(self.token, status.text)
        self.assertNotIn("fixture-private-key", status.text)

    def test_unavailable_service_never_leaks_raw_errors_or_claims_up_to_date(self):
        self.login()
        self.proxy.side_effect = httpx.ConnectError("fixture-private-key internal-IP")
        status = self.client.get("/api/settings/updates")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertIs(status.json()["available"], False)
        self.assertIsNone(status.json()["latest_version"])
        self.assertNotIn("fixture-private-key", status.text)
        self.assertEqual(self.client.post("/api/settings/updates/check", json={}).status_code, 503)

    def test_update_controls_require_configured_password_even_on_open_inventory(self):
        from app.main import create_app

        with TestClient(create_app(data_dir=self.root / "open-data", password="", timezone="Asia/Shanghai")) as opened:
            status = opened.get("/api/settings/updates")
            self.assertIs(status.json()["available"], False)
            self.assertIn("HOME_STORAGE_PASSWORD", status.json()["message"])
            self.assertEqual(opened.post("/api/settings/updates/check", json={}).status_code, 403)
            self.assertEqual(opened.patch("/api/settings/updates", json={"auto_update": True}).status_code, 403)
            self.assertEqual(opened.post("/api/settings/updates/install", json={"confirmed": True, "version": VERSION}).status_code, 403)
        self.assertEqual(self.proxy.await_count, 0)

    def test_maintenance_blocks_inventory_and_ai_writes_but_keeps_reads_and_controls(self):
        self.login()
        home = next(row for row in self.client.get("/api/locations").json() if row["name"] == "家")
        item = self.client.post("/api/items", json={"name": "更新前物品", "location_id": home["id"]}).json()
        (self.state_dir / "maintenance.flag").write_text("updating")
        calls = (
            ("post", "/api/items", {"name": "不应写入", "location_id": home["id"]}),
            ("patch", f"/api/items/{item['id']}", {"name": "不应改名"}),
            ("post", f"/api/items/{item['id']}/archive", {"confirmed": True}),
            ("post", "/api/categories", {"name": "不应创建"}),
            ("post", "/api/ai/chat", {"message": "录入新物品"}),
        )
        for method, route, payload in calls:
            with self.subTest(route=route):
                self.assertEqual(getattr(self.client, method)(route, json=payload).status_code, 503)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/items").json()[0]["name"], "更新前物品")
        self.assertEqual(self.client.get("/api/settings/updates").status_code, 200)
        self.assertEqual(self.client.patch("/api/settings/updates", json={"auto_update": False}).status_code, 200)
        self.assertEqual(self.client.post("/api/logout").status_code, 200)
        self.login()


class UpdateReleaseTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="homestorage-update-release-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.archive = gzip.compress(b"test Docker archive payload")
        self.manifest = {
            "schema_version": 1, "version": VERSION, "revision": REVISION,
            "images": {
                arch: {"image": f"homestorage:{VERSION}-{arch}", "asset": f"homestorage-{VERSION}-linux-{arch}.tar.gz", "sha256": hashlib.sha256(self.archive).hexdigest(), "size": len(self.archive)}
                for arch in ("amd64", "arm64")
            },
        }
        assets = []
        for identity, name, size in (
            (101, "homestorage-update.json", len(json.dumps(self.manifest).encode())),
            (102, self.manifest["images"]["amd64"]["asset"], len(self.archive)),
            (103, self.manifest["images"]["arm64"]["asset"], len(self.archive)),
        ):
            assets.append({"id": identity, "url": f"https://api.github.com/repos/Adventurer0/HomeStorage/releases/assets/{identity}", "name": name, "size": size, "browser_download_url": f"https://github.com/Adventurer0/HomeStorage/releases/download/v{VERSION}/{name}"})
        self.metadata = {
            "tag_name": "v" + VERSION, "draft": False, "prerelease": False,
            "html_url": stable_release()["url"], "body": "发布说明", "assets": assets,
        }
        self.requests = []

    def provider(self, request):
        from updater.releases import API_URL

        self.requests.append(request)
        url = str(request.url)
        if url == API_URL:
            content = json.dumps(self.metadata).encode()
        elif url.endswith("homestorage-update.json") or url.endswith("/assets/101"):
            content = json.dumps(self.manifest).encode()
        else:
            content = self.archive
        return httpx.Response(200, stream=httpx.ByteStream(content))

    def releases(self, handler=None, *, token="", **client_options):
        from updater.releases import ReleaseClient

        client = self.enterContext(httpx.Client(transport=httpx.MockTransport(handler or self.provider), **client_options))
        return ReleaseClient(client=client, github_token=token)

    def test_checksummed_stable_archive_download_has_exact_size_and_progress(self):
        releases = self.releases()
        release = releases.latest("amd64")
        destination = self.root / "image.tar.gz"
        progress = []
        releases.download(release, destination, lambda current, total: progress.append((current, total)))
        self.assertEqual(destination.read_bytes(), self.archive)
        self.assertEqual(progress[-1], (len(self.archive), len(self.archive)))
        self.assertEqual(release["revision"], REVISION)
        self.assertTrue(all(request.url.scheme == "https" for request in self.requests))
        self.assertTrue(all("authorization" not in request.headers for request in self.requests))

    def test_metadata_rejects_wrong_source_version_digest_size_and_prereleases(self):
        from updater.releases import ReleaseError

        metadata = deepcopy(self.metadata)
        manifest = deepcopy(self.manifest)
        mutations = (
            lambda: self.metadata.update(prerelease=True),
            lambda: self.metadata.update(draft=True),
            lambda: self.metadata.update(tag_name="v2.1.1-beta"),
            lambda: self.metadata.update(html_url="https://github.com/other/repo/releases/tag/v" + VERSION),
            lambda: self.manifest.update(version="2.1.0"),
            lambda: self.manifest.update(revision="not-a-commit"),
            lambda: self.manifest.update(schema_version=True),
            lambda: self.manifest.update(extra="unexpected"),
            lambda: self.manifest["images"]["amd64"].update(image="evil/image:latest"),
            lambda: self.manifest["images"]["amd64"].update(asset="../../evil.tar.gz"),
            lambda: self.manifest["images"]["amd64"].update(sha256="not-a-sha"),
            lambda: self.manifest["images"]["amd64"].update(size=True),
            lambda: self.manifest["images"]["amd64"].update(size=1024 ** 3 + 1),
            lambda: self.metadata["assets"][1].update(size=len(self.archive) + 1),
            lambda: self.metadata["assets"][1].update(browser_download_url="https://github.com/evil/repo/releases/download/v" + VERSION + "/file.tar.gz"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                self.metadata = deepcopy(metadata)
                self.manifest = deepcopy(manifest)
                mutate()
                with self.assertRaises(ReleaseError):
                    self.releases().latest("amd64")
        self.metadata, self.manifest = metadata, manifest
        with self.assertRaises(ReleaseError):
            self.releases().latest("mips64")

    def test_incomplete_tampered_or_non_gzip_download_is_removed(self):
        from updater.releases import ReleaseError

        releases = self.releases()
        release = releases.latest("amd64")
        for content in (self.archive[:-1], self.archive + b"extra", b"wrong" * 20):
            with self.subTest(content_size=len(content)):
                self.archive = content
                destination = self.root / "rejected.tar.gz"
                with self.assertRaises(ReleaseError):
                    releases.download(release, destination, lambda *_: None)
                self.assertFalse(destination.exists())
        plain = b"plain valid SHA but invalid archive"
        self.archive = plain
        release["sha256"], release["size"] = hashlib.sha256(plain).hexdigest(), len(plain)
        with self.assertRaises(ReleaseError):
            releases.download(release, self.root / "rejected.tar.gz", lambda *_: None)
        self.assertFalse((self.root / "rejected.tar.gz").exists())

    def test_api_404_is_an_access_or_missing_release_error(self):
        from updater.releases import ReleaseError

        releases = self.releases(lambda request: httpx.Response(404, stream=httpx.ByteStream(b"private details secret-token")))
        with self.assertRaises(ReleaseError) as caught:
            releases.latest("amd64")
        self.assertIn("私有", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_private_asset_redirects_never_forward_github_or_client_authorization(self):
        from updater.releases import API_URL

        def private_provider(request):
            self.requests.append(request)
            url = str(request.url)
            if url == API_URL:
                return httpx.Response(200, stream=httpx.ByteStream(json.dumps(self.metadata).encode()))
            if "/assets/101" in url:
                return httpx.Response(302, headers={"Location": "https://release-assets.githubusercontent.com/manifest"})
            if "/assets/102" in url:
                return httpx.Response(302, headers={"Location": "https://release-assets.githubusercontent.com/archive?signature=signed"})
            body = json.dumps(self.manifest).encode() if url.endswith("/manifest") else self.archive
            return httpx.Response(200, stream=httpx.ByteStream(body))

        releases = self.releases(private_provider, token="fixture-github-read-token", headers={"Authorization": "Bearer client-default-secret"}, auth=("user", "password"))
        release = releases.latest("amd64")
        releases.download(release, self.root / "private.tar.gz", lambda *_: None)
        self.assertIn("asset_api_url", release)
        for request in self.requests:
            if request.url.host == "api.github.com":
                self.assertEqual(request.headers.get("Authorization"), "Bearer fixture-github-read-token")
            else:
                self.assertNotIn("Authorization", request.headers)

    def test_release_redirects_cannot_choose_untrusted_or_unencrypted_hosts(self):
        from updater.releases import API_URL, ReleaseError

        for redirect in ("http://github.com/unsafe", "https://evil.example/archive", "https://user:password@github.com/archive"):
            with self.subTest(redirect=redirect):
                def redirect_provider(request):
                    if str(request.url) == API_URL:
                        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(self.metadata).encode()))
                    return httpx.Response(302, headers={"Location": redirect})
                with self.assertRaises(ReleaseError):
                    self.releases(redirect_provider).latest("amd64")

class UpdateEngineTest(unittest.TestCase):
    def setUp(self):
        from updater.engine import UpdateEngine

        self.directory = TemporaryDirectory(prefix="homestorage-update-engine-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data_dir, self.state_dir = self.root / "data", self.root / "state"
        self.data_dir.mkdir()
        (self.data_dir / "uploads").mkdir()
        (self.data_dir / "homestorage.sqlite3").write_bytes(b"original-stock-and-audit")
        (self.data_dir / "uploads" / "photo.jpg").write_bytes(b"original-photo")
        (self.data_dir / ".session-key").write_bytes(b"original-session-identity")
        self.store = StateStore(self.state_dir)
        self.docker = FakeDocker(self.data_dir, self.state_dir)
        self.releases = FakeReleases()
        self.engine = UpdateEngine(self.docker, self.store, self.releases, self_id="helper", data_dir=self.data_dir, state_dir=self.state_dir)
        self.engine.HEALTH_TIMEOUT = 0.02
        self.engine.HEALTH_POLL = 0.01

    def files(self):
        return {str(path.relative_to(self.data_dir)): path.read_bytes() for path in self.data_dir.rglob("*") if path.is_file()}

    def test_success_backs_up_all_data_before_creation_and_preserves_deployment_config(self):
        original = deepcopy(self.docker.container_map["old"])
        original_files = self.files()

        def verify_backup_before_create():
            self.assertFalse(self.docker.container_map["old"]["State"]["Running"])
            self.assertTrue((self.state_dir / "maintenance.flag").is_file())
            backups = list((self.state_dir / "backups").glob("backup-*.tar.gz"))
            self.assertEqual(len(backups), 1)
            with tarfile.open(backups[0], "r:gz") as archive:
                for name, content in original_files.items():
                    self.assertEqual(archive.extractfile("data/" + name).read(), content)

        self.docker.before_create = verify_backup_before_create
        self.engine.check()
        status = self.engine.install(VERSION)
        self.assertEqual(status["phase"], "completed")
        self.assertEqual(status["current_version"], VERSION)
        self.assertEqual(status["backup_count"], 1)
        self.assertFalse((self.state_dir / "maintenance.flag").exists())
        self.assertIsNone(self.store.get()["journal"])
        self.assertNotIn("old", self.docker.container_map)
        self.assertIn("helper", self.docker.container_map)
        new = self.docker.container_map["new-1"]
        before = dict(entry.split("=", 1) for entry in original["Config"]["Env"])
        after = dict(entry.split("=", 1) for entry in new["Config"]["Env"])
        for key in ("HOME_STORAGE_PASSWORD", "AI_API_KEY", "AI_MODEL", "APP_TIMEZONE", "DATA_DIR"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["HOMESTORAGE_VERSION"], VERSION)
        self.assertEqual(after["HOMESTORAGE_REVISION"], REVISION)
        self.assertEqual(new["HostConfig"], original["HostConfig"])
        self.assertEqual(new["Config"]["User"], "1000:1000")
        events = [row[0] for row in self.docker.events]
        self.assertLess(events.index("load"), events.index("stop"))
        self.assertLess(events.index("stop"), events.index("create"))
        self.assertEqual(self.files(), original_files)
        self.assertNotIn("fixture-private-key", json.dumps(status))

    def test_project_role_mount_and_duplicate_targets_never_mutate_docker(self):
        from updater.engine import UpdateError

        originals = deepcopy(self.docker.container_map)
        mutations = (
            lambda: self.docker.container_map["old"]["Config"]["Labels"].update({"com.docker.compose.project": "other-project"}),
            lambda: self.docker.container_map["old"]["Config"]["Labels"].update({"io.homestorage.role": "other-app"}),
            lambda: self.docker.container_map["old"]["Mounts"][0].update(Source="/other-volume"),
            lambda: self.docker.container_map["old"]["Mounts"][0].update(RW=False),
            lambda: self.docker.container_map["helper"]["Config"]["Labels"].update({"io.homestorage.role": "app"}),
            lambda: self.docker.container_map.update({"other": {**deepcopy(self.docker.container_map["old"]), "Id": "other", "Name": "/other-active"}}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                self.docker.container_map = deepcopy(originals)
                self.store.update(release_cache={**stable_release(), "arch": "amd64"})
                mutate()
                with self.assertRaises(UpdateError):
                    self.engine.install(VERSION)
                self.assertEqual(self.docker.events, [])
                self.assertEqual((self.data_dir / "homestorage.sqlite3").read_bytes(), b"original-stock-and-audit")

    def test_unsupported_architecture_or_unchecked_version_is_rejected_before_mutation(self):
        from updater.engine import UpdateError

        self.engine.check()
        for version in ("2.1.2", "2.1.0", "latest", "2.1.1; shell"):
            with self.subTest(version=version):
                with self.assertRaises(UpdateError):
                    self.engine.install(version)
                self.assertEqual(self.docker.events, [])
        self.docker.architecture = "mips64"
        with self.assertRaises(UpdateError):
            self.engine.install(VERSION)
        self.assertEqual(self.docker.events, [])

    def test_image_label_or_arch_mismatch_never_stops_existing_app(self):
        from updater.engine import UpdateError

        self.engine.check()
        original = deepcopy(self.docker.image_metadata)
        for field, value in (("Architecture", "arm64"), ("Os", "windows"), ("version", "2.1.2"), ("revision", "f" * 40)):
            with self.subTest(field=field):
                self.docker.image_metadata = deepcopy(original)
                if field in ("Architecture", "Os"):
                    self.docker.image_metadata[field] = value
                else:
                    self.docker.image_metadata["Config"]["Labels"]["org.opencontainers.image." + field] = value
                with self.assertRaises(UpdateError):
                    self.engine.install(VERSION)
                self.assertFalse(any(row[0] in {"stop", "rename", "create", "remove"} for row in self.docker.events))
                self.assertTrue(self.docker.container_map["old"]["State"]["Running"])

    def test_failed_health_restores_database_photos_and_session_identity(self):
        from updater.engine import UpdateError

        original_files = self.files()
        self.docker.fail_new_health = self.docker.migrate_on_start = True
        self.engine.check()
        with self.assertRaises(UpdateError):
            self.engine.install(VERSION)
        self.assertEqual(self.store.get()["phase"], "rolled_back")
        self.assertEqual(self.files(), original_files)
        self.assertEqual(set(self.docker.container_map), {"helper", "old"})
        self.assertTrue(self.docker.container_map["old"]["State"]["Running"])
        self.assertEqual(self.docker.container_map["old"]["Name"], "/homestorage")
        self.assertFalse((self.state_dir / "maintenance.flag").exists())

    def test_restore_syncs_each_file_and_nested_directory_before_restart_without_following_links(self):
        from updater.engine import UpdateError

        photo = self.data_dir / "uploads" / "photo.jpg"
        photo.chmod(0o640)
        os.utime(photo, (1700000001, 1700000001))
        uploads = self.data_dir / "uploads"
        uploads.chmod(0o750)
        os.utime(uploads, (1700000000, 1700000000))
        (self.data_dir / "photo-link").symlink_to("uploads/photo.jpg")
        (self.data_dir / "uploads-link").symlink_to("uploads", target_is_directory=True)
        expected = {"homestorage.sqlite3", ".session-key", "uploads/photo.jpg", "uploads", "."}
        synced = []
        original_fsync = os.fsync
        original_start = self.docker.start

        def fsync(descriptor):
            path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            journal = self.store.get().get("journal") or {}
            if path.is_relative_to(self.data_dir) and journal.get("stage") == "restoring":
                synced.append(str(path.relative_to(self.data_dir)))
            return original_fsync(descriptor)

        def start(identity):
            if identity == "old":
                self.assertEqual(set(synced), expected)
                self.assertEqual(len(synced), len(expected))
                self.assertLess(synced.index("uploads/photo.jpg"), synced.index("uploads"))
                self.assertEqual(synced[-1], ".")
            return original_start(identity)

        self.docker.fail_new_health = self.docker.migrate_on_start = True
        self.engine.check()
        with patch("updater.engine.os.fsync", side_effect=fsync), patch.object(self.docker, "start", side_effect=start):
            with self.assertRaises(UpdateError):
                self.engine.install(VERSION)
        self.assertEqual(self.store.get()["phase"], "rolled_back")
        self.assertIsNone(self.store.get()["journal"])
        self.assertEqual(stat.S_IMODE(photo.stat().st_mode), 0o640)
        self.assertEqual(photo.stat().st_mtime, 1700000001)
        self.assertEqual(stat.S_IMODE(uploads.stat().st_mode), 0o750)
        self.assertEqual(uploads.stat().st_mtime, 1700000000)
        self.assertTrue((self.data_dir / "photo-link").is_symlink())
        self.assertTrue((self.data_dir / "uploads-link").is_symlink())

    def test_restore_fsync_failure_retains_recovery_journal_and_backup_until_restart(self):
        from updater.engine import UpdateEngine, UpdateError

        original_files = self.files()
        original_fsync = os.fsync

        def fsync(descriptor):
            path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            journal = self.store.get().get("journal") or {}
            if path == self.data_dir / "homestorage.sqlite3" and journal.get("stage") == "restoring":
                raise OSError("simulated restored-data flush failure")
            return original_fsync(descriptor)

        self.docker.fail_new_health = self.docker.migrate_on_start = True
        self.engine.check()
        with patch("updater.engine.os.fsync", side_effect=fsync):
            with self.assertRaises(UpdateError):
                self.engine.install(VERSION)
        journal = StateStore(self.state_dir).get()["journal"]
        self.assertEqual(journal["stage"], "restoring")
        backup = self.state_dir / "backups" / journal["backup_file"]
        self.assertTrue(backup.is_file())
        self.assertTrue((self.state_dir / "maintenance.flag").is_file())
        self.assertFalse(self.docker.container_map["old"]["State"]["Running"])
        self.assertNotIn(("start", "old"), self.docker.events)
        recovered = UpdateEngine(self.docker, StateStore(self.state_dir), self.releases,
                                 self_id="helper", data_dir=self.data_dir, state_dir=self.state_dir)
        self.assertEqual(recovered.recover()["phase"], "rolled_back")
        self.assertEqual(self.files(), original_files)
        self.assertFalse(backup.exists())
        self.assertFalse((self.state_dir / "maintenance.flag").exists())

    def test_failed_recovery_keeps_maintenance_and_does_not_claim_rollback(self):
        from updater.engine import UpdateError

        self.docker.fail_new_health = self.docker.migrate_on_start = self.docker.fail_old_start = True
        self.engine.check()
        with self.assertRaises(UpdateError) as caught:
            self.engine.install(VERSION)
        self.assertEqual(self.store.get()["phase"], "failed")
        self.assertTrue((self.state_dir / "maintenance.flag").exists())
        self.assertIsNotNone(self.store.get()["journal"])
        self.assertNotIn("fixture-private-key", str(caught.exception))
        self.assertNotIn("fixture-private-key", json.dumps(self.engine.status()))

    def test_crash_after_new_start_is_recovered_from_durable_journal(self):
        from updater.engine import UpdateEngine

        original_files = self.files()
        self.docker.migrate_on_start = True
        self.engine.check()
        with patch.object(self.engine, "_health", side_effect=KeyboardInterrupt("simulated process crash")):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.install(VERSION)
        self.assertIsNotNone(StateStore(self.state_dir).get()["journal"])
        self.assertNotEqual(self.files(), original_files)
        recovered = UpdateEngine(self.docker, StateStore(self.state_dir), self.releases, self_id="helper", data_dir=self.data_dir, state_dir=self.state_dir)
        result = recovered.recover()
        self.assertEqual(result["phase"], "rolled_back")
        self.assertEqual(self.files(), original_files)
        self.assertTrue(self.docker.container_map["old"]["State"]["Running"])
        self.assertFalse((self.state_dir / "maintenance.flag").exists())

    def test_crash_during_create_finds_replacement_without_recorded_container_id(self):
        original_create = self.docker.create

        def interrupted_create(config, *, name):
            original_create(config, name=name)
            raise KeyboardInterrupt("daemon created container but response was lost")

        self.engine.check()
        with patch.object(self.docker, "create", side_effect=interrupted_create):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.install(VERSION)
        self.assertIsNone(self.store.get()["journal"]["new_id"])
        self.assertIn("new-1", self.docker.container_map)
        result = self.engine.recover()
        self.assertEqual(result["phase"], "rolled_back")
        self.assertEqual(set(self.docker.container_map), {"helper", "old"})

    def test_corrupt_backup_gzip_footer_never_deletes_live_data_during_recovery(self):
        from updater.engine import UpdateError

        self.docker.migrate_on_start = True
        self.engine.check()
        with patch.object(self.engine, "_health", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.install(VERSION)
        journal = self.store.get()["journal"]
        backup = self.state_dir / "backups" / journal["backup_file"]
        content = bytearray(backup.read_bytes())
        content[-1] ^= 0x01
        backup.write_bytes(content)
        live_data = self.files()
        with self.assertRaises(UpdateError):
            self.engine.recover()
        self.assertEqual(self.files(), live_data)
        self.assertEqual(self.store.get()["phase"], "failed")
        self.assertTrue((self.state_dir / "maintenance.flag").exists())

    def test_concurrent_install_is_denied_and_disabling_auto_during_download_prevents_stop(self):
        from updater.engine import UpdateError

        self.engine.check()
        self.store.update(auto_update=True)
        self.releases.entered, self.releases.resume = threading.Event(), threading.Event()
        errors = []

        def install():
            try:
                self.engine.install(VERSION, automatic=True)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=install)
        worker.start()
        self.addCleanup(lambda: self.releases.resume.set())
        self.assertTrue(self.releases.entered.wait(2))
        with self.assertRaises(UpdateError):
            self.engine.install(VERSION)
        self.store.update(auto_update=False)
        self.releases.resume.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(self.docker.container_map["old"]["State"]["Running"])
        self.assertFalse(any(row[0] == "stop" for row in self.docker.events))

    def test_only_three_successful_full_backups_are_retained(self):
        for patch_version in range(1, 5):
            version = "2.1." + str(patch_version)
            self.releases.release = stable_release(version)
            self.docker.image_metadata["Config"]["Labels"]["org.opencontainers.image.version"] = version
            self.engine.check()
            self.engine.install(version)
        self.assertEqual(self.engine.status()["backup_count"], 3)
        self.assertEqual(len(list((self.state_dir / "backups").glob("backup-*.tar.gz"))), 3)

    def test_real_release_client_and_engine_share_checked_cache_without_private_fields(self):
        from updater.releases import API_URL, ReleaseClient

        archive = docker_archive()
        manifest = {"schema_version": 1, "version": VERSION, "revision": REVISION, "images": {
            arch: {"image": f"homestorage:{VERSION}-{arch}", "asset": f"homestorage-{VERSION}-linux-{arch}.tar.gz", "sha256": hashlib.sha256(archive).hexdigest(), "size": len(archive)}
            for arch in ("amd64", "arm64")
        }}
        names = ["homestorage-update.json"] + [manifest["images"][arch]["asset"] for arch in ("amd64", "arm64")]
        metadata = {"tag_name": "v" + VERSION, "draft": False, "prerelease": False, "html_url": stable_release()["url"], "body": "integration release", "assets": [
            {"name": name, "size": len(json.dumps(manifest).encode()) if index == 0 else len(archive), "browser_download_url": f"https://github.com/Adventurer0/HomeStorage/releases/download/v{VERSION}/{name}"}
            for index, name in enumerate(names)
        ]}

        def provider(request):
            url = str(request.url)
            content = json.dumps(metadata).encode() if url == API_URL else json.dumps(manifest).encode() if url.endswith("homestorage-update.json") else archive
            return httpx.Response(200, stream=httpx.ByteStream(content))

        client = self.enterContext(httpx.Client(transport=httpx.MockTransport(provider)))
        self.engine.releases = ReleaseClient(client=client, github_token="")
        self.engine.check()
        self.assertEqual(self.store.get()["release_cache"]["arch"], "amd64")
        status = self.engine.install(VERSION)
        self.assertEqual(status["phase"], "completed")
        self.assertEqual(status["current_version"], VERSION)
        self.assertNotIn("release_cache", status)

    def test_invalid_archive_structure_or_gzip_is_rejected_before_any_docker_mutation(self):
        from updater.engine import UpdateError

        oversized = tarfile.TarInfo("huge-layer.tar")
        oversized.size = 5 * 1024 ** 3
        traversal = tarfile.TarInfo("../outside")
        corrupted = bytearray(docker_archive())
        corrupted[-1] ^= 1
        invalid_archives = (
            b"not gzip", gzip.compress(b"not tar"), bytes(corrupted), docker_archive("2.1.2"),
            gzip.compress(oversized.tobuf() + bytes(1024)),
            gzip.compress(traversal.tobuf() + bytes(1024)),
        )
        self.engine.check()
        for index, content in enumerate(invalid_archives):
            with self.subTest(case=index):
                with patch.object(self.releases, "download", side_effect=lambda release, destination, progress, body=content: Path(destination).write_bytes(body)):
                    with self.assertRaises(UpdateError):
                        self.engine.install(VERSION)
                self.assertEqual(self.docker.events, [])
                self.assertTrue(self.docker.container_map["old"]["State"]["Running"])


class FakeServiceEngine:
    def __init__(self, store):
        self.store = store
        self.checks = 0
        self.installs = []
        self.recoveries = 0
        self.entered = None
        self.resume = None
        self.rollback_install = False
        self.latest_version = VERSION

    def status(self):
        return {
            "available": True, "current_version": "2.1.0", "current_revision": "b" * 40,
            "latest_version": self.latest_version, "update_available": True,
            "release_url": stable_release(self.latest_version)["url"], "release_notes": "模拟发布",
            "phase": self.store.get()["phase"], "message": self.store.get()["message"],
            "progress": {"downloaded_bytes": 0, "total_bytes": 100}, "backup_count": 0,
            "token": "fixture-github-token", "journal": {"Env": ["AI_API_KEY=fixture-private-key"]},
        }

    def recover(self):
        self.recoveries += 1

    def check(self):
        self.checks += 1
        if self.entered:
            self.entered.set()
            if not self.resume.wait(5):
                raise RuntimeError("fixture check wait timeout")
        self.store.update(phase="idle", message="已检查")

    def install(self, version, automatic=False):
        from updater.engine import UpdateError

        self.installs.append((version, automatic))
        if self.rollback_install:
            self.store.update(phase="rolled_back", message="原版本及数据已恢复。")
            raise UpdateError("已安全恢复原版本。")
        self.store.update(phase="completed", message="已安装")


class UpdateInternalServiceTest(unittest.TestCase):
    def setUp(self):
        from updater.main import create_app

        self.directory = TemporaryDirectory(prefix="homestorage-update-service-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = StateStore(self.root / "state")
        self.engine = FakeServiceEngine(self.store)
        self.token = "a" * 64
        self.app = create_app(engine=self.engine, store=self.store, token=self.token, state_dir=self.root / "state", data_dir=self.root / "data", schedule_enabled=False)
        self.client = self.enterContext(TestClient(self.app))
        self.headers = {"Authorization": "Bearer " + self.token}

    def wait_job(self):
        future = self.app.state.controller._future
        self.assertIsNotNone(future)
        future.result(timeout=3)

    def test_internal_endpoints_require_token_and_reject_extra_install_targets(self):
        calls = (("get", "/v1/status", None), ("post", "/v1/check", {}), ("post", "/v1/settings", {"auto_update": True}), ("post", "/v1/install", {"confirmed": True, "version": VERSION}))
        for method, path, body in calls:
            with self.subTest(path=path):
                kwargs = {"json": body} if body is not None else {}
                for auth in ({}, {"Authorization": "Bearer wrong-token"}):
                    self.assertEqual(getattr(self.client, method)(path, headers=auth, **kwargs).status_code, 401)
        self.assertEqual(self.engine.installs, [])
        for body in ({"confirmed": False, "version": VERSION}, {"confirmed": 1, "version": VERSION}, {"confirmed": True, "version": VERSION, "image": "arbitrary/image"}, {"confirmed": True, "version": "2.1.1-alpha"}):
            self.assertEqual(self.client.post("/v1/install", json=body, headers=self.headers).status_code, 422)
        for body in ({"auto_update": 1}, {"auto_update": True, "url": "http://evil.example"}):
            self.assertEqual(self.client.post("/v1/settings", json=body, headers=self.headers).status_code, 422)
        self.assertEqual(self.engine.recoveries, 1)
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)

    def test_status_hides_tokens_journal_and_environment_and_setting_persists(self):
        response = self.client.get("/v1/status", headers=self.headers)
        self.assertIs(response.json()["auto_update"], False)
        self.assertNotIn("token", response.json())
        self.assertNotIn("journal", response.json())
        self.assertNotIn("fixture-github-token", response.text)
        self.assertNotIn("fixture-private-key", response.text)
        self.assertEqual(self.client.post("/v1/settings", json={"auto_update": True}, headers=self.headers).status_code, 200)
        self.assertIs(StateStore(self.root / "state").get()["auto_update"], True)
        self.assertEqual(self.client.post("/v1/settings", json={"auto_update": False}, headers=self.headers).status_code, 200)
        self.assertIs(StateStore(self.root / "state").get()["auto_update"], False)

    def test_queued_job_blocks_second_job_but_allows_switching_off(self):
        self.engine.entered, self.engine.resume = threading.Event(), threading.Event()
        self.addCleanup(self.engine.resume.set)
        response = self.client.post("/v1/check", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(self.engine.entered.wait(2))
        self.assertEqual(self.client.post("/v1/install", json={"confirmed": True, "version": VERSION}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post("/v1/check", json={}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post("/v1/settings", json={"auto_update": False}, headers=self.headers).status_code, 200)
        self.engine.resume.set()
        self.wait_job()
        self.assertEqual(self.engine.checks, 1)
        self.assertEqual(self.engine.installs, [])

    def test_daily_job_checks_with_auto_off_and_installs_only_with_user_opt_in(self):
        controller = self.app.state.controller
        controller.start("check", automatic=True)
        self.wait_job()
        self.assertEqual(self.engine.checks, 1)
        self.assertEqual(self.engine.installs, [])
        self.store.update(auto_update=True)
        controller.start("check", automatic=True)
        self.wait_job()
        self.assertEqual(self.engine.installs, [(VERSION, True)])
        self.engine.entered, self.engine.resume = threading.Event(), threading.Event()
        self.addCleanup(self.engine.resume.set)
        controller.start("check", automatic=True)
        self.assertTrue(self.engine.entered.wait(2))
        self.client.post("/v1/settings", json={"auto_update": False}, headers=self.headers)
        self.engine.resume.set()
        self.wait_job()
        self.assertEqual(self.engine.installs, [(VERSION, True)])

    def test_failed_update_preserves_successful_rollback_phase(self):
        self.engine.rollback_install = True
        response = self.client.post("/v1/install", json={"confirmed": True, "version": VERSION}, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        self.wait_job()
        response = self.client.get("/v1/status", headers=self.headers)
        self.assertEqual(response.json()["phase"], "rolled_back")
        self.assertIn("恢复", response.json()["message"])

    def test_unchecked_repeat_or_downgrade_install_does_not_start_job(self):
        self.assertEqual(self.client.post("/v1/install", json={"confirmed": True, "version": "2.1.2"}, headers=self.headers).status_code, 409)
        self.engine.latest_version = "2.0.0"
        self.assertEqual(self.client.post("/v1/install", json={"confirmed": True, "version": "2.0.0"}, headers=self.headers).status_code, 409)
        self.engine.latest_version = "2.1.0"
        self.assertEqual(self.client.post("/v1/install", json={"confirmed": True, "version": "2.1.0"}, headers=self.headers).status_code, 409)
        self.assertEqual(self.engine.installs, [])


if __name__ == "__main__":
    unittest.main()
