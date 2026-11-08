"""版本检查与半自动更新（2026-11-08 掌柜要求）。

能力边界先说清楚：

* **能做的**：查 GitHub 上最新的 release/tag，跟本地版本比；有新版本时把
  安装包（``*.neko-plugin``）下载到插件的 data 目录里，供用户去插件中心重装。
* **不能做的**：插件不能替换自己再重启自己 —— 它没有那个权限，硬做等于
  自改自删。所以「装」这一步永远由人点一下完成。
"""

from __future__ import annotations

import re
from typing import Any

GITHUB_REPO = "amoandzhanggui-neko/n.e.k.o_plugin_dignity_guard"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
TAGS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/tags"
#: 仓库一个 release / tag 都没有时的兜底：走 **contents API** 读 main 上的 plugin.toml。
#: 2026-11-08 实测：`/releases/latest` 恒 404、`/tags` 返回空数组；
#: 而 raw.githubusercontent.com 在这台机器上 ConnectError（连不上），
#: api.github.com 却是通的 ⟹ 兜底必须走 API，不能走 raw 域名。
CONTENTS_TOML_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/plugin.toml?ref=main"
_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE)

#: GitHub 要求带 UA，否则匿名请求会被 403。
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "dignity_guard-update-check",
}


def version_tuple(raw: Any) -> tuple[int, int, int]:
    """把 ``0.1.0`` / ``v0.2.1`` / ``0.1.0.dev0`` 统一成三元组。

    非数字段按 0 处理 —— 宁可把「看不懂的版本号」当成最旧，
    也不要因为解析失败就谎报「有新版本」。
    """
    text = str(raw or "").strip().lstrip("vV")
    core = text.split("+")[0].split("-")[0]
    parts: list[int] = []
    for segment in core.split(".")[:3]:
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def is_newer(latest: Any, current: Any) -> bool:
    return version_tuple(latest) > version_tuple(current)


def _pick_asset(assets: Any) -> str:
    if not isinstance(assets, list):
        return ""
    # 优先 .neko-plugin，其次 zip —— 两个都没有就留空，前端只给网页链接。
    for suffix in (".neko-plugin", ".zip"):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if url and name.lower().endswith(suffix):
                return url
    return ""


async def fetch_latest(client: Any, errors: list[str] | None = None) -> dict[str, Any] | None:
    """查最新版本。先看 release，没有 release 就退到 tag。

    返回 ``None`` 表示「没查到」—— 调用方必须把这种情况和「已是最新」区分开，
    否则一次网络故障会被说成「你已是最新版」，那是假的。

    ``errors`` 用来回收失败原因：静默吞掉异常会让「为什么连不上」永远查不出来
    （2026-11-08 实测踩过：面板只说"连不上"，日志里一个字都没有）。
    """
    def note(text: str) -> None:
        if errors is not None and len(errors) < 4:
            errors.append(text)

    try:
        resp = await client.get(LATEST_RELEASE_URL, headers=_HEADERS, timeout=12.0)
        if getattr(resp, "status_code", 0) == 200:
            data = resp.json()
            tag = data.get("tag_name") or data.get("name") or ""
            if tag:
                return {
                    "version": str(tag).lstrip("vV"),
                    "url": str(data.get("html_url") or ""),
                    "asset_url": _pick_asset(data.get("assets")),
                    "published_at": str(data.get("published_at") or ""),
                    "source": "release",
                }
            note("release: 返回里没有 tag")
        else:
            note(f"release: HTTP {getattr(resp, 'status_code', '?')}")
    except Exception as exc:
        note(f"release: {type(exc).__name__}: {exc}")

    try:
        resp = await client.get(TAGS_URL, headers=_HEADERS, timeout=12.0)
        if getattr(resp, "status_code", 0) == 200:
            tags = resp.json()
            if isinstance(tags, list):
                for item in tags:
                    name = str((item or {}).get("name") or "") if isinstance(item, dict) else ""
                    if name:
                        return {
                            "version": name.lstrip("vV"),
                            "url": f"https://github.com/{GITHUB_REPO}/releases",
                            "asset_url": "",
                            "published_at": "",
                            "source": "tag",
                        }
            note("tag: 列表为空")
        else:
            note(f"tag: HTTP {getattr(resp, 'status_code', '?')}")
    except Exception as exc:
        note(f"tag: {type(exc).__name__}: {exc}")

    # 兜底：没有 release、也没有 tag 的仓库（当前就是），走 contents API 读 plugin.toml。
    try:
        resp = await client.get(CONTENTS_TOML_URL, headers=_HEADERS, timeout=12.0)
        if getattr(resp, "status_code", 0) == 200:
            payload = resp.json()
            encoded = payload.get("content") if isinstance(payload, dict) else None
            if isinstance(encoded, str) and encoded:
                import base64

                text = base64.b64decode(encoded).decode("utf-8", "replace")
                match = _VERSION_RE.search(text)
                if match:
                    return {
                        "version": match.group(1),
                        "url": f"https://github.com/{GITHUB_REPO}",
                        "asset_url": "",
                        "published_at": "",
                        "source": "repo",
                    }
            note("repo: contents 里没有可解码的 plugin.toml")
        else:
            note(f"repo: HTTP {getattr(resp, 'status_code', '?')}")
    except Exception as exc:
        note(f"repo: {type(exc).__name__}: {exc}")

    return None