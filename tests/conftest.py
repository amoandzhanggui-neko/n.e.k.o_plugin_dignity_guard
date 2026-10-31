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
from urllib.parse import unquote

import pytest

#: 服务端 ``main_routers/proactive_router._USER_OWNED_FIELDS`` 的镜像。
#: 这些是**用户专有**字段（隐私模式之类），插件一律不得写 —— 由服务端拒绝，
#: 而不是指望调用方自觉。假服务器必须照样拒，否则"插件没写这些字段"就没被测到。
_USER_OWNED_FIELDS = frozenset({"proactiveVisionEnabled", "visitEnabled", "visitMemoryEnabled"})

#: ``POST /api/proactive/settings`` 的白名单（服务端天生是**部分更新**语义）。
_PROACTIVE_WRITABLE = frozenset(
    {
        "proactiveChatEnabled",
        "proactiveVisionChatEnabled",
        "proactiveNewsChatEnabled",
        "proactiveCommunityChatEnabled",
        "proactiveVideoChatEnabled",
        "proactivePersonalChatEnabled",
        "proactiveMusicEnabled",
        "proactiveMemeEnabled",
        "proactiveMiniGameInviteEnabled",
        "proactiveChatInterval",
        "proactiveVisionInterval",
    }
)

_VALID_MODES = ("off", "normal", "focus", "frequent")


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
        #: 主动搭话预设模式（``POST /api/proactive/mode``）。
        self.proactive_mode = "custom"
        #: 每一次**写**请求都记下来 —— 让测试能断言"它确实写了"，
        #: 而不是只能断言"它没报错"（后者正是这个插件专门去抓的那种假绿）。
        self.writes: list[tuple[str, str, dict[str, Any]]] = []

    def bump_revision(self) -> None:
        self.revision += 1

    def payload_for(self, path: str) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        # ★ 2026-10-11 真机批次抓到：插件读角色资料时带查询串
        #   （`/api/characters?language=zh-CN` —— 不带它服务端会把「昵称/性别」
        #   翻译后再吐出来，插件会把译文写回她自己的话）。而这里原来是**精确相等**
        #   比较 ⟹ 带查询串的请求一律 404 ⟹ 缺席恢复读不到全量、直接 read_failed。
        #   所以匹配前必须先剥掉查询串。
        path = path.split("?", 1)[0]
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
            # 允许把它置成**畸形形状**（如 list）：宿主换版本、中间层包装变了，
            # 插件拿到的就不是 dict。用 isinstance 放行，是为了让这条错误路径
            # 能用「真客户端 + 真 HTTP + 真 JSON 解析」复现，
            # 而不是只能靠把方法换掉来打桩。
            body = self.characters if isinstance(self.characters, list) else dict(self.characters)
            return 200, body, {}
        if path == "/api/config/page_config":
            return 200, dict(self.page_config), {}
        if path == "/api/config/core_api":
            return 200, dict(self.core_api), {}
        if path == "/api/config/preferences":
            # Preferences is a list in production; copy it so a test that mutates
            # an entry does not leak into the next test.
            return 200, list(self.preferences), {}
        return None

    def write_for(
        self, method: str, path: str, body: Any
    ) -> tuple[int, dict[str, Any]] | None:
        """Handle the two write surfaces the plugin actually uses.

        它刻意**照搬真服务器那两个"会咬人"的细节**，否则插件为绕开它们而做的
        read-modify-write 看起来就毫无必要，那条逻辑一旦回归也没人会发现：

        * ``PUT /api/characters/catgirl/<name>`` —— **整份替换**，没传的字段
          等于被删掉（``crud.py:1432-1438``）。这就是插件必须"先读全量、只改一个、
          整份发回"的原因。
        * ``POST /api/proactive/settings`` —— **部分更新**，只认白名单；
          用户专有字段由**服务端**拒绝并回报 ``rejected_user_owned``。
        """
        payload = body if isinstance(body, dict) else {}
        # 与 `payload_for` 同样先剥查询串（防御性：写请求通常不带，但别让它们走进 404）。
        path = path.split("?", 1)[0]
        self.writes.append((method, path, payload))

        if method == "PUT" and path.startswith("/api/characters/catgirl/"):
            name = unquote(path.rsplit("/", 1)[-1])
            catgirl = self.characters.get("猫娘")
            if not isinstance(catgirl, dict) or name not in catgirl:
                return 404, {"success": False, "error": f"unknown character {name!r}"}
            # 整份替换 —— 没传的字段就是被删了（真服务器就是这个脾气）
            catgirl[name] = dict(payload)
            return 200, {"success": True}

        if method == "POST" and path == "/api/proactive/settings":
            rejected_user_owned = sorted(set(payload) & _USER_OWNED_FIELDS)
            applied = {k: v for k, v in payload.items() if k in _PROACTIVE_WRITABLE}
            self.settings.update(applied)
            out: dict[str, Any] = {"success": True, "applied": applied}
            if rejected_user_owned:
                out["rejected_user_owned"] = rejected_user_owned
            return 200, out

        if method == "POST" and path == "/api/proactive/mode":
            mode = str(payload.get("mode", ""))
            if mode not in _VALID_MODES:
                return 400, {"success": False, "error": f"unknown mode {mode!r}"}
            self.proactive_mode = mode
            return 200, {"success": True, "mode": mode}

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

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ★ 2026-10-11 补：**写**通道。
    #   在此之前假服务器只有 ``do_GET``，于是插件所有"写回"路径
    #   （缺席恢复的 ``PUT /api/characters``、她的自主权的 ``POST /api/proactive/*``）
    #   **端到端从未被覆盖过** —— "她真把东西放回去了"一直只是纸面。
    #   只补写、**不补读**：补读会让插件突然看到新的设置项，可能改变现有用例的
    #   diff 结果（例如 ``tracked_paths`` / ``pending_count`` 的断言）。
    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_write("PUT")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_write("POST")

    def _handle_write(self, method: str) -> None:
        server: ThreadingHTTPServer = self.server  # type: ignore[assignment]
        fake: FakeMainServer = server.fake_state  # type: ignore[attr-defined]
        resolved = fake.write_for(method, self.path, self._read_json_body())
        if resolved is None:
            self.send_error(404, "not found")
            return
        status, body = resolved
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
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
