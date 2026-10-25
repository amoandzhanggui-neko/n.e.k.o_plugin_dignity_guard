"""T14：`liveness_check.py` 自己的正确性必须有闸。

## 为什么需要这个文件

`liveness_check.py` 是「插件此刻活着吗」这条判据的**实现**，但它自己**不在**
`seam/blocks.json` 的块受控清单里 —— 而那是**对的**：那份清单收的是业务块
（`settings_guard.py` / `__init__.py` 这些），工具脚本塞进去会让「改工具」要求「改契约」，
那是把判据用错了地方。

于是有个缺口：**它改坏了没有任何闸会发现。** 最坏的一种改法是让它永远 `return 0`
（或永远算成 `running`）—— 状态板会一直显示「在跑」，而那正是它本来要防的假绿。

所以用测试钉住。测试不是"补充材料"，它是这条判据唯一的保护。

## 三条断言

1. **六种状态各自正确**（含 `no_host` 与 `no_log` 的区分）
2. **输出格式稳定**：结论行必须以 `[√]` / `[x]` / `[—]` 开头
   —— `state.sh` 靠 `grep -E '^\[√\]|^\[x\]'` 抓它，格式一变就变成「无输出」，那是静默失效
3. **`evaluate()` 与 `main()` 口径一致** —— `gate_extra` 用前者、状态板用后者，
   两套口径会造出「看板说在跑、闸说没跑」
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LOG_NAME = "N.E.K.O_Plugin_dignity_guard_20261025.log"


def _load():
    spec = importlib.util.spec_from_file_location("_lc_for_test", PLUGIN_ROOT / "liveness_check.py")
    module = importlib.util.module_from_spec(spec)
    # ★ 必须先注册进 sys.modules，再 exec_module。
    #
    #   为什么：`liveness_check.py` 有 `from __future__ import annotations` + `@dataclass`。
    #   dataclasses 在处理字符串注解时会通过 `sys.modules[cls.__module__]` 反查模块，
    #   模块不在表里就拿到 None，报的错是：
    #       AttributeError: 'NoneType' object has no attribute '__dict__'
    #   —— 这个报错**完全看不出跟 dataclass 有关**，第一次踩会以为是 fixture 写错了。
    sys.modules["_lc_for_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def lc(monkeypatch, tmp_path):
    """加载模块并把端口探测替换成固定集合。

    为什么替换 `listening_ports`：测试不该依赖"本机此刻有没有端口在听"。
    真依赖它，就会出现「在我机器上过、在你机器上红」——而这条判据正是为跨机器设计的。
    """
    module = _load()

    def fake_ports(ports):
        monkeypatch.setattr(module, "listening_ports", lambda: set(ports))

    monkeypatch.setattr(module, "listening_ports", lambda: set())
    module._fake_ports = fake_ports  # 交给各测试自己设
    return module


def _write_log(dirpath: Path, ipc: int, mainp: int) -> Path:
    p = dirpath / LOG_NAME
    p.write_text(
        f"2026-10-25 16:52:11 - x - INFO - ZeroMQ IPC enabled: tcp://127.0.0.1:{ipc}\n"
        f"2026-10-25 16:52:11 - x - INFO - watching http://127.0.0.1:{mainp} every 20s\n",
        encoding="utf-8",
    )
    return p


# ───────────────────── 六种状态 ─────────────────────


def test_running(lc, monkeypatch, tmp_path) -> None:
    _write_log(tmp_path, 1111, 2222)
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))
    lc._fake_ports({1111, 2222})

    v = lc.evaluate()
    assert v.state == "running"
    assert v.alive is True
    assert v.ipc == 1111 and v.main_port == 2222


def test_host_only(lc, monkeypatch, tmp_path) -> None:
    """宿主在、插件不在 —— 这是最该报警的一种。"""
    _write_log(tmp_path, 1111, 2222)
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))
    lc._fake_ports({2222})            # 只有主服务在听

    assert lc.evaluate().state == "host_only"


def test_down(lc, monkeypatch, tmp_path) -> None:
    _write_log(tmp_path, 1111, 2222)
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))
    lc._fake_ports(set())

    assert lc.evaluate().state == "down"


def test_odd(lc, monkeypatch, tmp_path) -> None:
    """插件在听但主服务没了 —— 刚启动或端口已换。"""
    _write_log(tmp_path, 1111, 2222)
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))
    lc._fake_ports({1111})

    assert lc.evaluate().state == "odd"


def test_no_host_is_not_a_failure(lc, monkeypatch, tmp_path) -> None:
    """没有宿主日志目录 ⟹ 这条判据**不适用**，不能判红。

    分不清的后果很具体：别人 clone 下来跑测试，这条永远红，然后被人关掉 ——
    连真红一起关掉。
    """
    missing = tmp_path / "nope"
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(missing))

    v = lc.evaluate()
    assert v.state == "no_host"
    assert v.line().startswith("[—]")


def test_no_log_is_a_failure(lc, monkeypatch, tmp_path) -> None:
    """有宿主目录但从来没有 dignity 日志 ⟹ 装了没跑过，算没在跑。"""
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))   # 目录存在但是空的

    v = lc.evaluate()
    assert v.state == "no_log"
    assert v.line().startswith("[x]")


# ───────────────────── 输出格式（state.sh 靠它） ─────────────────────


def test_conclusion_line_format_is_stable(lc) -> None:
    """结论行必须以 `[√]` / `[x]` / `[—]` 开头。

    `state.sh` 的 grep 就是 `^\\[√\\]|^\\[x\\]`。这里如果改成别的符号，
    状态板不会报错，只会显示「liveness_check 无输出」——静默失效。
    """
    states = ["running", "host_only", "down", "odd", "no_host", "no_log"]
    for state in states:
        line = lc.Verdict(state=state, ipc=1111, main_port=2222).line()
        assert re.match(r"^\[(√|x|—)\] ", line), f"{state} 的结论行格式不对：{line!r}"


def test_main_output_matches_evaluate(lc, monkeypatch, tmp_path, capsys) -> None:
    """`evaluate()` 与 `main()` 必须同口径。

    `gate_extra` 用前者、状态板用后者。两套口径会造出「看板说在跑、闸说没跑」，
    而那正是这个插件最在意的那类错误。
    """
    _write_log(tmp_path, 1111, 2222)
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))
    lc._fake_ports({1111, 2222})

    v = lc.evaluate()
    rc = lc.main()
    out = capsys.readouterr().out

    assert v.state == "running"
    assert rc == 0, "在跑时退出码应为 0"
    assert v.line() in out, "main() 的输出里必须含 evaluate() 的结论行"


def test_main_returns_nonzero_when_not_running(lc, monkeypatch, tmp_path, capsys) -> None:
    """没在跑必须返回非零 —— 否则调用方（state.sh / 闸）拿不到信号。"""
    _write_log(tmp_path, 1111, 2222)
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path))
    lc._fake_ports(set())             # 两个端口都不在听

    assert lc.main() != 0
    assert "没在跑" in capsys.readouterr().out


def test_main_skips_gracefully_without_host(lc, monkeypatch, tmp_path, capsys) -> None:
    """没有宿主目录时 `main()` 也要返回 0（跳过 ≠ 失败）。"""
    monkeypatch.setenv("DIGNITY_LOG_DIR", str(tmp_path / "nope"))

    assert lc.main() == 0
    assert "不适用" in capsys.readouterr().out
