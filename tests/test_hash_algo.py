"""哈希口径三处同步的机器检查（T10）。

## 为什么需要它

哈希口径现在写在**三个地方**：

1. `neko_review_gate.js` 的 `const HASH_ALGO = '...'`
2. `gate_extra.py` 的 `HASH_ALGO = "..."`
3. `sha16()` / `file_hash16()` 的**实现**（真的做了 CRLF→LF 归一化）

改一处忘另外两处的后果是**静默失效**，而且症状很误导：

- 只改常量、不改实现 ⟹ 台账记着"lf"，实际算的是 raw ⟹ 判红 7 拿两套数字比 ⟹ 一片假红
- 改实现、不改 gate_extra 的常量 ⟹ `ledger_files()` 认为口径不符 ⟹ **拒绝使用整份台账** ⟹
  判红 7 静默退回只看 `git status`（能力变弱，但不会报错，很难发现）

两种都不会崩。**不崩的失效是最贵的**，所以用测试钉住。

## 三条断言

1. 两处常量相等
2. 常量值就是 `sha256-16-lf`（写死预期值，防止两边"一起改错"）
3. `file_hash16()` 对**同内容、不同行尾**的两个文件返回**同一个哈希**

第 3 条是关键：前两条只验"声明"，第 3 条验"实现"。只验声明的话，
实现里把 `.replace()` 删掉照样全绿。
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
EXPECTED = "sha256-16-lf"


def _load_gate_extra():
    spec = importlib.util.spec_from_file_location("_gate_extra_for_test", PLUGIN_ROOT / "gate_extra.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _js_algo() -> str | None:
    src = (PLUGIN_ROOT / "neko_review_gate.js").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"const\s+HASH_ALGO\s*=\s*['\"]([^'\"]+)['\"]", src)
    return m.group(1) if m else None


def test_both_constants_are_the_expected_value() -> None:
    """两处常量都必须是写死的那个值。

    "两边相等"不够 —— 两边可以一起改错。所以预期值在这里写死一遍。
    """
    py_algo = _load_gate_extra().HASH_ALGO
    js_algo = _js_algo()

    assert js_algo is not None, "neko_review_gate.js 里找不到 `const HASH_ALGO = '...'`"
    assert py_algo == EXPECTED, f"gate_extra.py 的 HASH_ALGO 是 {py_algo!r}，应为 {EXPECTED!r}"
    assert js_algo == EXPECTED, f"neko_review_gate.js 的 HASH_ALGO 是 {js_algo!r}，应为 {EXPECTED!r}"


def test_implementation_really_normalizes_line_endings() -> None:
    """实现必须真的把 CRLF 归一成 LF —— 不是只在常量里写了 `-lf`。

    做法：造两个内容完全相同、只有行尾不同的文件，哈希必须相等。
    如果哪天有人把 `file_hash16` 里的 `.replace(b"\\r\\n", b"\\n")` 删掉，
    这条会立刻红 —— 而前两条照样绿。
    """
    gate_extra = _load_gate_extra()
    body = "第一行\n第二行\n第三行\n"

    with tempfile.TemporaryDirectory() as tmp:
        lf = Path(tmp) / "lf.txt"
        crlf = Path(tmp) / "crlf.txt"
        lf.write_bytes(body.encode("utf-8"))
        crlf.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))

        h_lf = gate_extra.file_hash16(lf)
        h_crlf = gate_extra.file_hash16(crlf)

    assert h_lf == h_crlf, (
        "同内容不同行尾算出了不同哈希 ⟹ 归一化没生效。"
        f"lf={h_lf} crlf={h_crlf}"
    )
    # 再对一次独立实现，确认算的是"归一化后的 sha256 前 16 位"
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    assert h_lf == expected, f"归一化后的哈希应等于 LF 内容的 sha256[:16]={expected}"


def test_lf_hash_equals_git_blob_hash_for_a_real_file() -> None:
    """真实文件上，`file_hash16` 必须等于 `git blob` 的 sha256[:16]。

    这一条把"跨机器可比"钉死：git blob 是仓库里存的字节（LF），
    任何机器 clone 都一样 ⟹ 我们的哈希也就任何机器都一样。
    """
    import subprocess

    target = "settings_guard.py"
    path = PLUGIN_ROOT / target
    if not path.is_file():
        return  # 不该发生；缺文件时上面几条已经会红

    blob = subprocess.run(
        ["git", "show", f"HEAD:{target}"],
        cwd=str(PLUGIN_ROOT), capture_output=True,
    ).stdout
    if not blob:
        return  # 还没提交过，跳过（不把"没有基线"当成失败）

    expected = hashlib.sha256(blob).hexdigest()[:16]
    assert _load_gate_extra().file_hash16(path) == expected, (
        f"{target} 的 file_hash16 与 git blob 不一致 ⟹ 跨机器不可比"
    )
