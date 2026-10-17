"""Web-managed updater proxies preserve credentials and internal isolation."""

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from updater.state import StateStore, ensure_token


PROXY_URL = "http://192.168.31.1:7890"
PASSWORD = "fixture-private-proxy-password"
ENVIRONMENT = {
    key: "" for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    )
}


class UpdateProxyWebTest(unittest.TestCase):
    def setUp(self):
        from app.main import create_app

        self.root = Path(self.enterContext(TemporaryDirectory(prefix="homestorage-web-proxy-")))
        self.state_dir = self.root / "state"
        self.token = ensure_token(self.state_dir, os.getuid(), os.getgid())
        self.enterContext(patch.dict(os.environ, {
            **ENVIRONMENT, "UPDATE_SERVICE_URL": "http://updater:9080",
            "UPDATE_STATE_DIR": str(self.state_dir), "AI_API_KEY": "",
        }))
        self.call = self.enterContext(patch("app.updates.call_updater", new_callable=AsyncMock))
        self.call.return_value = {
            "available": True, "phase": "idle", "auto_update": False,
            "proxy_url": PROXY_URL, "proxy_username": "family",
            "proxy_password_set": True, "proxy_source": "web",
            "proxy_password": PASSWORD, "proxy_configuration": {"password": PASSWORD},
        }
        self.client = self.enterContext(TestClient(create_app(
            data_dir=self.root / "data", password="fixture-login-password", timezone="Asia/Shanghai",
        )))

    def login(self):
        response = self.client.post("/api/login", json={"password": "fixture-login-password"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_proxy_settings_require_login_and_same_origin(self):
        self.assertEqual(self.client.get("/api/settings/updates").status_code, 401)
        self.assertEqual(self.client.patch("/api/settings/updates", json={"proxy_url": PROXY_URL}).status_code, 401)
        self.login()
        for headers in ({"Origin": "https://untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}):
            response = self.client.patch("/api/settings/updates", json={"proxy_url": PROXY_URL}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.call.assert_not_awaited()

    def test_proxy_only_patch_forwards_only_explicit_fields_and_filters_secrets(self):
        self.login()
        payload = {"proxy_url": PROXY_URL, "proxy_username": "family", "proxy_password": PASSWORD}
        response = self.client.patch("/api/settings/updates", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.call.assert_awaited_once_with("http://updater:9080", self.token, "POST", "/v1/settings", payload)
        self.assertEqual(response.json()["proxy_url"], PROXY_URL)
        self.assertEqual(response.json()["proxy_username"], "family")
        self.assertIs(response.json()["proxy_password_set"], True)
        self.assertEqual(response.json()["proxy_source"], "web")
        for route in ("/api/settings/updates", "/api/export"):
            result = self.client.get(route)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotIn(PASSWORD, result.text)
            self.assertNotIn("proxy_configuration", result.json())
        self.assertNotIn(PASSWORD, response.text)

    def test_invalid_proxy_urls_and_empty_requests_never_reach_updater(self):
        self.login()
        invalid = (
            "ftp://192.168.31.1:7890", "http://family:password@192.168.31.1:7890",
            "http://192.168.31.1:7890/path", "http://192.168.31.1:7890?token=secret",
            "http://192.168.31.1:7890#fragment", "http://192.168.31.1:0",
            "http://192.168.31.1:65536", "http://192.168.31.1:not-a-port",
            "http://192.168.31.1:7890\n", "http://192.168.31.1:7890\\path",
        )
        for value in invalid:
            with self.subTest(url=value):
                response = self.client.patch("/api/settings/updates", json={"proxy_url": value})
                self.assertEqual(response.status_code, 422, response.text)
        for payload in ({}, {"proxy_url": None}, {"proxy_password": 123}, {"auto_update": "false"}):
            response = self.client.patch("/api/settings/updates", json=payload)
            self.assertEqual(response.status_code, 422, response.text)
        self.call.assert_not_awaited()

    def test_password_clear_and_existing_auto_update_requests_preserve_patch_semantics(self):
        self.login()
        for payload in ({"proxy_password": ""}, {"proxy_password": None}, {"auto_update": False}):
            self.call.reset_mock()
            response = self.client.patch("/api/settings/updates", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.call.assert_awaited_once_with("http://updater:9080", self.token, "POST", "/v1/settings", payload)

    def test_untrusted_status_cannot_return_credentials_inside_proxy_url(self):
        self.login()
        self.call.return_value["proxy_url"] = "http://user:" + PASSWORD + "@192.168.31.1:7890"
        response = self.client.get("/api/settings/updates")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(PASSWORD, response.text)
        self.assertNotIn("@", response.json().get("proxy_url", ""))


class UpdateProxyInternalTest(unittest.TestCase):
    def setUp(self):
        from tests.test_updates import FakeServiceEngine
        from updater.main import create_app

        self.root = Path(self.enterContext(TemporaryDirectory(prefix="homestorage-internal-proxy-")))
        self.enterContext(patch.dict(os.environ, ENVIRONMENT))
        self.store = StateStore(self.root / "state")
        self.engine = FakeServiceEngine(self.store)
        self.old_client = Mock(spec=["close"])
        self.engine.releases = self.old_client
        self.constructor = self.enterContext(patch("updater.main.ReleaseClient", side_effect=lambda **kwargs: Mock(spec=["close"])))
        self.app = create_app(
            engine=self.engine, store=self.store, token="b" * 64,
            state_dir=self.root / "state", data_dir=self.root / "data", schedule_enabled=False,
        )
        self.client = self.enterContext(TestClient(self.app, raise_server_exceptions=False))
        self.headers = {"Authorization": "Bearer " + "b" * 64}

    def save(self, payload):
        return self.client.post("/v1/settings", json=payload, headers=self.headers)

    def configuration(self):
        return StateStore(self.root / "state").get()["proxy_configuration"]

    def test_internal_proxy_settings_require_service_token_and_hide_invalid_input(self):
        payload = {"proxy_url": PROXY_URL, "proxy_password": PASSWORD}
        for headers in ({}, {"Authorization": "Bearer incorrect"}):
            self.assertEqual(self.client.post("/v1/settings", json=payload, headers=headers).status_code, 401)
        for invalid in ({}, {"proxy_url": None}, {"proxy_username": None}, {"auto_update": None}, {"proxy_password": 42}):
            response = self.save(invalid)
            self.assertEqual(response.status_code, 422, response.text)
        response = self.save({"proxy_password": PASSWORD + "\n"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn(PASSWORD, response.text)
        self.constructor.assert_not_called()

    def test_proxy_configuration_persists_privately_and_replaces_client_after_save(self):
        response = self.save({"proxy_url": PROXY_URL, "proxy_username": "family", "proxy_password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        configuration = {"url": PROXY_URL, "username": "family", "password": PASSWORD}
        self.assertEqual(self.configuration(), configuration)
        self.assertEqual(self.constructor.call_args.kwargs["proxy_configuration"], configuration)
        self.assertIsNot(self.engine.releases, self.old_client)
        self.old_client.close.assert_called_once()
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        status = self.client.get("/v1/status", headers=self.headers)
        self.assertEqual(status.json()["proxy_url"], PROXY_URL)
        self.assertEqual(status.json()["proxy_username"], "family")
        self.assertIs(status.json()["proxy_password_set"], True)
        self.assertEqual(status.json()["proxy_source"], "web")
        self.assertNotIn(PASSWORD, response.text + status.text)
        self.assertNotIn("proxy_configuration", status.json())

    def test_password_preservation_clearing_and_proxy_identity_changes(self):
        def save_and_read(payload):
            response = self.save(payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(PASSWORD, response.text)
            return self.configuration()

        original = {"url": PROXY_URL, "username": "family", "password": PASSWORD}
        save_and_read({"proxy_url": PROXY_URL, "proxy_username": "family", "proxy_password": PASSWORD})
        self.assertEqual(save_and_read({"proxy_password": ""}), original)
        self.assertEqual(save_and_read({"proxy_url": PROXY_URL + "/"}), original)
        calls = self.constructor.call_count
        self.assertEqual(save_and_read({"auto_update": True}), original)
        self.assertEqual(self.constructor.call_count, calls)
        self.assertEqual(save_and_read({"proxy_password": None})["password"], "")
        save_and_read({"proxy_password": PASSWORD})
        self.assertEqual(save_and_read({"proxy_username": "different-family", "proxy_password": ""})["password"], "")
        save_and_read({"proxy_password": PASSWORD})
        changed = save_and_read({"proxy_url": "socks5h://192.168.31.2:1080"})
        self.assertEqual(changed["password"], "")
        save_and_read({"proxy_password": PASSWORD})
        self.assertEqual(save_and_read({"proxy_url": ""}), {"url": "", "username": "", "password": ""})

    def test_proxy_changes_wait_for_running_job_but_auto_update_can_be_disabled(self):
        self.engine.entered, self.engine.resume = threading.Event(), threading.Event()
        self.addCleanup(self.engine.resume.set)
        queued = self.client.post("/v1/check", json={}, headers=self.headers)
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertTrue(self.engine.entered.wait(2))
        self.assertEqual(self.save({"proxy_url": PROXY_URL}).status_code, 409)
        response = self.save({"auto_update": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(self.store.get()["auto_update"], False)
        self.constructor.assert_not_called()
        self.engine.resume.set()
        self.app.state.controller._future.result(timeout=3)

    def test_failed_persistence_does_not_swap_client_or_memory_configuration(self):
        before = self.store.get()
        with patch.object(self.store, "_write", side_effect=OSError(PASSWORD)):
            response = self.save({"proxy_url": PROXY_URL, "proxy_username": "family", "proxy_password": PASSWORD})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn(PASSWORD, response.text)
        self.assertEqual(self.store.get(), before)
        self.assertEqual(StateStore(self.root / "state").get(), before)
        self.assertIs(self.engine.releases, self.old_client)
        self.old_client.close.assert_not_called()


class UpdateProxyValidationTest(unittest.TestCase):
    def test_supported_proxy_schemes_and_lan_ipv6_addresses_normalize(self):
        from updater.proxy import normalize_proxy_url

        for url in (
            PROXY_URL, "https://proxy.example:8443", "socks5://192.168.31.1:1080",
            "socks5h://nas-proxy.local:1080", "http://[fd00::1]:7890",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_proxy_url(url + "/"), url)

    def test_explicit_disabled_proxy_bypasses_ambient_proxy_environment(self):
        from updater.releases import ReleaseClient

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://private-user:" + PASSWORD + "@192.168.31.5:7890"}):
            with patch("updater.releases.httpx.Client") as factory:
                release = ReleaseClient(proxy_configuration={"url": "", "username": "", "password": ""})
                self.assertIs(factory.call_args.kwargs.get("trust_env"), False)
                self.assertFalse(factory.call_args.kwargs.get("proxy"))
                release.close()

    def test_proxy_credentials_are_separate_from_url_and_default_env_behavior_is_retained(self):
        from updater.releases import ReleaseClient

        with patch("updater.releases.httpx.Client") as factory:
            release = ReleaseClient(proxy_configuration={"url": PROXY_URL, "username": "family", "password": PASSWORD})
            self.assertIs(factory.call_args.kwargs.get("trust_env"), False)
            proxy = factory.call_args.kwargs["proxy"]
            self.assertEqual(str(proxy.url), PROXY_URL)
            self.assertNotIn(PASSWORD, str(proxy.url))
            credentials = tuple(value.decode() if isinstance(value, bytes) else value for value in proxy.auth)
            self.assertEqual(credentials, ("family", PASSWORD))
            release.close()
        with patch("updater.releases.httpx.Client") as factory:
            release = ReleaseClient()
            self.assertIs(factory.call_args.kwargs.get("trust_env", True), True)
            release.close()


if __name__ == "__main__":
    unittest.main()
