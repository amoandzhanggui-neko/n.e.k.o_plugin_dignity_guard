"""Shared fixtures for the Dignity Guard tests.

The plugin's whole job is to notice a settings change, so several tests need a
stand-in for the local main server. This fixture serves the five read-only
endpoints the plugin reads and nothing else; it never touches the real N.E.K.O
main server or the user's configuration.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


class FakeMainServer:
    """Mutable stand-in for the endpoints the plugin reads."""

    def __init__(self) -> None:
        self.revision = 1
        # A transport/telemetry field that lives at the *top* level of the
        # conversation-settings payload (see ``payload_for``). Exposed as an
        # attribute rather than inlined so a test can flip it: a field nobody
        # ever changes proves nothing about whether it is ignored.
        self.telemetry_branch = "main"
        self.settings: dict[str, Any] = {
            "proactiveChatEnabled": True,
            "userLanguage": "zh",
            "subtitleEnabled": True,
        }
        self.characters: dict[str, Any] = {
            "当前猫娘": "雪",
            "主人": {"昵称": "掌柜"},
            "猫娘": {
                "雪": {
                    "昵称": "雪",
                    "性别": "女",
                    "voice_id": "voice-1",
                    "system_prompt": "你是雪。",
                    "avatar": {"model_type": "live2d", "model_path": "雪.model3.json"},
                }
            },
        }
        self.page_config: dict[str, Any] = {"model_path": "雪.model3.json", "model_type": "live2d"}
        self.core_api: dict[str, Any] = {"api_key": "sk-do-not-store", "ttsModelProvider": "qwen"}
        # Real ``/api/config/preferences`` is a list of structures whose third
        # entry is a mirror of the global conversation settings (marked with
        # ``model_path == "__global_conversation__"``) — see
        # ``main_server_client.prune_mirrored_preferences`` and the
        # ``_flatten_into`` list-descent branch in ``settings_guard``. The old
        # dict fixture never exercised that index-based descent at all.
        self.preferences: Any = [
            {"position": [10, 20], "scale": 1.0},
            {"position": [30, 40], "scale": 2.0},
            {
                "model_path": "__global_conversation__",
                "proactiveChatEnabled": True,
                "proactiveVisionInterval": 30,
            },
        ]
        self.user_language = "zh"

    def bump_revision(self) -> None:
        self.revision += 1

    def payload_for(self, path: str) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        if path == "/api/config/conversation-settings":
            body = {
                "success": True,
                "settings": dict(self.settings),
                "revision": self.revision,
                "decisions": {},
                # Deliberately present at the top level: ``conversation_slice``
                # keeps only ``settings`` and ``decisions``, and this is the field
                # that proves the dropping is real.
                "telemetryBranch": self.telemetry_branch,
            }
            return 200, body, {"ETag": f'"conversation-settings-{self.revision}"'}
        if path == "/api/config/user_language":
            return 200, {"success": True, "language": self.user_language}, {}
        if path == "/api/characters":
            return 200, dict(self.characters), {}
        if path == "/api/config/page_config":
            return 200, dict(self.page_config), {}
        if path == "/api/config/core_api":
            return 200, dict(self.core_api), {}
        if path == "/api/config/preferences":
            # Preferences is a list in production; copy it so a test that mutates
            # an entry does not leak into the next test.
            return 200, list(self.preferences), {}
        return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        server: ThreadingHTTPServer = self.server  # type: ignore[assignment]
        fake: FakeMainServer = server.fake_state  # type: ignore[attr-defined]
        resolved = fake.payload_for(self.path)
        if resolved is None:
            self.send_error(404, "not found")
            return
        status, body, headers = resolved
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: Any) -> None:  # keep pytest output clean
        return


@pytest.fixture
def main_server():
    """Yield ``(fake_state, base_url)`` for a throwaway loopback server."""
    fake = FakeMainServer()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.fake_state = fake  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
