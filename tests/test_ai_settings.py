"""Web provider settings persistence, live requests and permission boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from app.ai_settings import AISettingsInput, AISettingsStore
from app.db import connect
from app.main import create_app


ENVIRONMENT = {
    "AI_API_BASE_URL": "https://original.example/v1",
    "AI_API_KEY": "environment-secret-never-return",
    "AI_MODEL": "original-chat",
    "AI_TRANSCRIPTION_MODEL": "original-voice",
    "AI_TRANSCRIPTION_BASE_URL": "",
    "AI_TRANSCRIPTION_API_KEY": "",
}


class AIWebSettingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="homestorage-ai-settings-")
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        self.update_dir = self.data_dir / "update-state"
        self.update_dir.mkdir()
        self.enterContext(patch.dict(os.environ, {**ENVIRONMENT, "UPDATE_STATE_DIR": str(self.update_dir)}))
        self.app = create_app(data_dir=self.data_dir, password="fixture-login-password")
        self.client = self.enterContext(TestClient(self.app))
        self.ok(self.client.post("/api/login", json={"password": "fixture-login-password"}))

    def ok(self, response):
        self.assertGreaterEqual(response.status_code, 200, response.text)
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def save(self, **body):
        return self.ok(self.client.patch("/api/settings/ai", json=body))

    def settings(self):
        return self.ok(self.client.get("/api/settings/ai"))

    @staticmethod
    def completion(*tools, reply="测试回复"):
        answer = {"role": "assistant", "content": reply}
        if tools:
            answer["tool_calls"] = [
                {"id": "call-" + str(index), "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments),
                }} for index, (name, arguments) in enumerate(tools)
            ]
        return httpx.Response(200, json={"choices": [{"message": answer}]})

    @contextmanager
    def provider(self, *responses, side_effect=None):
        provider = AsyncMock()
        provider.__aenter__.return_value = provider
        provider.post.side_effect = side_effect if side_effect is not None else list(responses)
        with patch("app.ai.httpx.AsyncClient", return_value=provider):
            yield provider

    def chat(self):
        return self.client.post("/api/ai/chat", json={"message": "东西在哪"})

    def transcribe(self):
        return self.client.post("/api/ai/transcribe", files={
            "file": ("recording.wav", b"RIFF\x04\x00\x00\x00WAVE", "audio/wav"),
        })

    def test_environment_defaults_are_public_without_credential_values(self):
        response = self.client.get("/api/settings/ai")
        value = self.ok(response)
        self.assertEqual(value, {
            "api_base_url": ENVIRONMENT["AI_API_BASE_URL"], "model": "original-chat",
            "transcription_model": "original-voice", "transcription_base_url": "",
            "api_key_set": True, "transcription_api_key_set": False,
            "enabled": True, "voice_enabled": True, "source": "environment",
        })
        self.assertNotIn(ENVIRONMENT["AI_API_KEY"], response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        with connect(self.data_dir / "homestorage.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_configuration").fetchone()[0], 0)

    def test_partial_first_save_captures_defaults_and_survives_restart_environment_changes(self):
        self.save(model="web-chat")
        replacement = {**ENVIRONMENT, "AI_API_KEY": "new-environment-key", "AI_MODEL": "new-environment-chat",
                       "AI_API_BASE_URL": "https://new-environment.example/v1"}
        with patch.dict(os.environ, replacement), TestClient(create_app(
            data_dir=self.data_dir, password="fixture-login-password",
        )) as restarted:
            self.ok(restarted.post("/api/login", json={"password": "fixture-login-password"}))
            value = self.ok(restarted.get("/api/settings/ai"))
            self.assertEqual((value["source"], value["model"], value["api_base_url"]),
                             ("web", "web-chat", ENVIRONMENT["AI_API_BASE_URL"]))
            with self.provider(self.completion()) as provider:
                self.ok(restarted.post("/api/ai/chat", json={"message": "查询"}))
                self.assertEqual(provider.post.await_args.kwargs["headers"]["Authorization"],
                                 "Bearer " + ENVIRONMENT["AI_API_KEY"])

    def test_provider_url_model_and_key_take_effect_without_restart(self):
        self.save(api_base_url="https://web-provider.example/v1/", model="web-model", api_key="web-private-key")
        with self.provider(self.completion()) as provider:
            self.ok(self.chat())
            call = provider.post.await_args
            self.assertEqual(call.args[0], "https://web-provider.example/v1/chat/completions")
            self.assertEqual(call.kwargs["json"]["model"], "web-model")
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer web-private-key")
        status = self.ok(self.client.get("/api/ai/status"))
        self.assertEqual((status["base_url"], status["model"]), ("https://web-provider.example/v1", "web-model"))
        self.assertNotIn("web-private-key", json.dumps(status))

    def test_each_chat_uses_one_snapshot_across_tool_calls_during_concurrent_save(self):
        calls = []

        async def respond(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                response = await asyncio.to_thread(self.client.patch, "/api/settings/ai", json={
                    "api_base_url": "https://changed.example/v1", "api_key": "changed-secret", "model": "changed-chat",
                })
                self.ok(response)
                return self.completion(("list_categories", {}))
            return self.completion()

        with self.provider(side_effect=respond):
            self.ok(self.chat())
        self.assertEqual(len(calls), 2)
        for args, kwargs in calls:
            self.assertEqual(args[0], ENVIRONMENT["AI_API_BASE_URL"] + "/chat/completions")
            self.assertEqual(kwargs["json"]["model"], ENVIRONMENT["AI_MODEL"])
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + ENVIRONMENT["AI_API_KEY"])
        with self.provider(self.completion()) as provider:
            self.ok(self.chat())
            self.assertEqual(provider.post.await_args.args[0], "https://changed.example/v1/chat/completions")

    def test_blank_and_omitted_keys_preserve_null_removes_without_environment_resurrection(self):
        self.save(api_key="saved-secret", transcription_api_key="separate-voice-secret")
        self.save(api_key="", transcription_api_key="   ", model="another-model")
        with self.provider(self.completion()) as provider:
            self.ok(self.chat())
            self.assertEqual(provider.post.await_args.kwargs["headers"]["Authorization"], "Bearer saved-secret")
        cleared = self.save(api_key=None)
        self.assertFalse(cleared["api_key_set"])
        self.assertFalse(cleared["enabled"])
        self.assertEqual(self.chat().status_code, 503)
        with TestClient(create_app(data_dir=self.data_dir, password="fixture-login-password")) as restarted:
            self.ok(restarted.post("/api/login", json={"password": "fixture-login-password"}))
            self.assertFalse(self.ok(restarted.get("/api/settings/ai"))["api_key_set"])
            self.assertEqual(restarted.post("/api/ai/chat", json={"message": "查询"}).status_code, 503)

    def test_voice_inherits_chat_and_can_use_and_clear_separate_provider(self):
        self.save(api_base_url="https://chat-web.example/v1", api_key="chat-web-secret", transcription_model="web-voice")
        with self.provider(httpx.Response(200, json={"text": "识别内容"})) as provider:
            self.assertEqual(self.ok(self.transcribe()), {"text": "识别内容"})
            call = provider.post.await_args
            self.assertEqual(call.args[0], "https://chat-web.example/v1/audio/transcriptions")
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer chat-web-secret")
            self.assertEqual(call.kwargs["data"], {"model": "web-voice"})
        self.save(transcription_base_url="https://voice-only.example/v1", transcription_api_key="voice-only-secret")
        with self.provider(httpx.Response(200, json={"text": "独立服务"})) as provider:
            self.ok(self.transcribe())
            call = provider.post.await_args
            self.assertEqual(call.args[0], "https://voice-only.example/v1/audio/transcriptions")
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer voice-only-secret")
        value = self.save(transcription_base_url="", transcription_api_key=None)
        self.assertFalse(value["transcription_api_key_set"])
        self.assertTrue(value["voice_enabled"])
        with self.provider(httpx.Response(200, json={"text": "重新继承"})) as provider:
            self.ok(self.transcribe())
            self.assertEqual(provider.post.await_args.args[0], "https://chat-web.example/v1/audio/transcriptions")
            self.assertEqual(provider.post.await_args.kwargs["headers"]["Authorization"], "Bearer chat-web-secret")

    def test_removing_environment_voice_key_does_not_revive_it_after_restart(self):
        with patch.dict(os.environ, {"AI_TRANSCRIPTION_API_KEY": "environment-voice-secret"}), TestClient(create_app(
            data_dir=self.data_dir, password="fixture-login-password",
        )) as initial:
            self.ok(initial.post("/api/login", json={"password": "fixture-login-password"}))
            self.assertTrue(self.ok(initial.get("/api/settings/ai"))["transcription_api_key_set"])
            self.ok(initial.patch("/api/settings/ai", json={"transcription_api_key": None}))
        with patch.dict(os.environ, {"AI_TRANSCRIPTION_API_KEY": "resurrected-voice-secret"}), TestClient(create_app(
            data_dir=self.data_dir, password="fixture-login-password",
        )) as restarted:
            self.ok(restarted.post("/api/login", json={"password": "fixture-login-password"}))
            self.assertFalse(self.ok(restarted.get("/api/settings/ai"))["transcription_api_key_set"])
            with self.provider(httpx.Response(200, json={"text": "内容"})) as provider:
                self.ok(restarted.post("/api/ai/transcribe", files={
                    "file": ("recording.wav", b"RIFF\x04\x00\x00\x00WAVE", "audio/wav"),
                }))
                self.assertEqual(provider.post.await_args.kwargs["headers"]["Authorization"],
                                 "Bearer " + ENVIRONMENT["AI_API_KEY"])

    def test_invalid_explicit_legacy_voice_url_does_not_send_recordings_to_chat_provider(self):
        with patch.dict(os.environ, {"AI_TRANSCRIPTION_BASE_URL": "http://invalid-remote.example/v1"}), TestClient(create_app(
            data_dir=self.data_dir, password="fixture-login-password",
        )) as legacy:
            self.ok(legacy.post("/api/login", json={"password": "fixture-login-password"}))
            self.assertFalse(self.ok(legacy.get("/api/ai/status"))["voice_enabled"])
            # A partial migration must retain this disabled state too.
            self.assertFalse(self.ok(legacy.patch("/api/settings/ai", json={"model": "changed-chat"}))["voice_enabled"])
            with self.provider() as provider:
                response = legacy.post("/api/ai/transcribe", files={
                    "file": ("recording.wav", b"RIFF\x04\x00\x00\x00WAVE", "audio/wav"),
                })
                self.assertEqual(response.status_code, 503)
                provider.post.assert_not_awaited()
            self.assertTrue(self.ok(legacy.patch("/api/settings/ai", json={"transcription_base_url": ""}))["voice_enabled"])

    def test_configuration_requires_login_and_same_origin(self):
        self.ok(self.client.post("/api/logout"))
        self.assertEqual(self.client.get("/api/settings/ai").status_code, 401)
        self.assertEqual(self.client.patch("/api/settings/ai", json={"model": "attacker"}).status_code, 401)
        self.ok(self.client.post("/api/login", json={"password": "fixture-login-password"}))
        for headers in ({"origin": "https://attacker.example"}, {"sec-fetch-site": "cross-site"}):
            self.assertEqual(self.client.patch("/api/settings/ai", json={"api_key": "attacker"}, headers=headers).status_code, 403)
        self.assertEqual(self.settings()["source"], "environment")
        self.ok(self.client.patch("/api/settings/ai", json={"model": "same-origin"}, headers={"origin": "http://testserver"}))

    def test_passwordless_inventory_and_environment_ai_work_but_settings_are_forbidden(self):
        with TestClient(create_app(data_dir=self.data_dir, password="")) as public:
            self.assertEqual(public.get("/api/settings/ai").status_code, 403)
            self.assertEqual(public.patch("/api/settings/ai", json={"model": "no-password"}).status_code, 403)
            self.assertTrue(self.ok(public.get("/api/ai/status"))["enabled"])
            with self.provider(self.completion()):
                self.ok(public.post("/api/ai/chat", json={"message": "查询"}))
            self.ok(public.get("/api/items"))

    def test_settings_writes_are_blocked_during_backup_but_reads_remain_available(self):
        (self.update_dir / "maintenance.flag").write_text("backup")
        self.assertEqual(self.client.patch("/api/settings/ai", json={"api_key": "must-not-save"}).status_code, 503)
        self.assertEqual(self.settings()["source"], "environment")
        (self.update_dir / "maintenance.flag").unlink()
        self.assertEqual(self.save(model="after-backup")["model"], "after-backup")

    def test_validation_rejects_invalid_types_fields_urls_and_control_characters_without_echo(self):
        bodies = [
            {"api_base_url": value} for value in ("", "ftp://provider.example/v1", "http://remote.example/v1",
                "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "https://example.com/#secret",
                "https://example.com:0/v1", "https://example.com:65536/v1", "https://example.com\\evil/v1",
                "https://exa mple.com/v1")
        ] + [
            {"api_base_url": None}, {"model": None}, {"transcription_model": None}, {"transcription_base_url": None},
            {"api_key": 12}, {"model": True}, {"enabled": True}, {"command": "bad-secret-command"},
            {"api_key": "bad-secret\nheader"}, {"model": "bad-secret\x85model"},
            {"api_key": "bad-secret" * 500}, {"model": "bad-secret" * 30},
            {"transcription_base_url": "http://remote-voice.example/v1"},
        ]
        for body in bodies:
            with self.subTest(body=tuple(body)):
                response = self.client.patch("/api/settings/ai", json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("secret", response.text)
                self.assertNotIn(ENVIRONMENT["AI_API_KEY"], response.text)
        self.assertEqual(self.settings()["source"], "environment")

    def test_local_http_provider_and_blank_models_are_supported(self):
        self.save(api_base_url="http://127.0.0.1:11434/v1", transcription_base_url="http://[::1]:8080/v1")
        self.assertEqual(self.settings()["api_base_url"], "http://127.0.0.1:11434/v1")
        self.assertFalse(self.save(transcription_model="")["voice_enabled"])
        self.assertFalse(self.save(model="")["enabled"])

    def test_secret_values_never_enter_settings_status_export_logs_or_provider_tools(self):
        chat_secret, voice_secret = "web-secret-only-on-nas", "web-voice-secret-only-on-nas"
        response = self.client.patch("/api/settings/ai", json={"api_key": chat_secret, "transcription_api_key": voice_secret})
        self.ok(response)
        responses = [response, self.client.get("/api/settings/ai"), self.client.get("/api/ai/status"),
                     self.client.get("/api/export"), self.client.get("/api/logs")]
        for value in responses:
            self.ok(value)
            self.assertNotIn(chat_secret, value.text)
            self.assertNotIn(voice_secret, value.text)
            self.assertNotIn("transcription_api_key\"", value.text)
        with self.provider(self.completion()) as provider:
            self.ok(self.chat())
            payload = provider.post.await_args.kwargs["json"]
            self.assertNotIn(chat_secret, json.dumps(payload))
            self.assertNotIn(voice_secret, json.dumps(payload))
            tools = {tool["function"]["name"] for tool in payload["tools"]}
            self.assertEqual(tools, {"search_items", "list_locations", "list_categories", "propose_create",
                                    "propose_restock", "propose_move", "propose_archive"})
        with self.provider(httpx.Response(401, json={"error": chat_secret + voice_secret})):
            failed = self.chat()
            self.assertEqual(failed.status_code, 502)
            self.assertNotIn(chat_secret, failed.text)
            self.assertNotIn(voice_secret, failed.text)
        exported = self.ok(self.client.get("/api/export"))
        self.assertNotIn("ai_configuration", json.dumps(exported))

    def test_provider_cannot_change_settings_through_an_unlisted_tool(self):
        with self.provider(self.completion(("set_ai_settings", {"api_key": "attacker-secret"}))) as provider:
            self.assertEqual(self.chat().status_code, 502)
            self.assertEqual(provider.post.await_count, 1)
        self.assertEqual(self.settings()["source"], "environment")

    def test_concurrent_partial_saves_merge_inside_serialized_transaction(self):
        barrier = Barrier(2)
        store = AISettingsStore(lambda: connect(self.data_dir / "homestorage.sqlite3"))

        def write(body):
            barrier.wait(3)
            return store.save(AISettingsInput.model_validate(body))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, {"model": "parallel-chat"}),
                       pool.submit(write, {"transcription_model": "parallel-voice"})]
            for future in futures:
                future.result(timeout=5)
        current = self.settings()
        self.assertEqual((current["model"], current["transcription_model"]), ("parallel-chat", "parallel-voice"))
        self.assertTrue(current["api_key_set"])


if __name__ == "__main__":
    unittest.main()
