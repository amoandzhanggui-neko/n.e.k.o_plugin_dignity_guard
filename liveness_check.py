#!/usr/bin/env python
"""插件此刻活着吗？（T11）

## 它解决的问题

在这之前，所有判据都只能回答「**启动过没有**」：

- 完工定义第 4 条：日志有当天启动记录 + 至少一次业务动作
- `state.sh` 第 5 节：同一个口径

而 2026-10-25 实测撞到过：插件 11:28 启动、12:12 停止，之后 4 小时没在跑，
**但所有闸全绿** —— 因为「今天开过一次」永远成立。那是**真假绿**，
比红更危险：红会让人停手，假绿会让人放心。

## 判据：看端口，不看进程名

判「活着」最直觉的做法是查进程，但**进程名靠不住**：宿主叫 `N.E.K.O` / `electron` / 别的，
换个宿主版本就变；而且本机实测 `wmic process get name` 会返回空（PID 50120 明明是 python.exe）。

所以改看**端口**，而且端口不是我猜的，是**插件自己吐在日志里的**：

| 端口 | 从日志哪一行读 | 它证明什么 |
|---|---|---|
| 插件 IPC | ``ZeroMQ IPC enabled: tcp://127.0.0.1:<port>`` | **插件进程活着**（这条最关键） |
| 主服务 | ``watching http://127.0.0.1:<port> every 20s`` | 宿主的主服务在（插件要连的对象） |

    IPC 在听   主服务在听   结论
    --------  ----------  ----------------------------------
       ✓          ✓       在跑
       ✗          ✓       宿主在，插件没跑（最该报警的一种）
       ✗          ✗       宿主也没开
       ✓          ✗       反常：插件活着但主服务不见了

## 为什么不用「日志多久没更新」当判据

因为**安静运行时不写日志是正常的**。插件每 20 秒轮询一次，没有变化就不落一行。
拿 mtime 当死活判据，会把「正常安静」报成「死了」——那是另一种假信号。
所以这里只把「多久没更新」当**信息**展示。

## 几种结论（注意最后两条的区别）

| state | 含义 | 退出码 |
|---|---|---|
| ``running`` | 插件进程活着，主服务也在 | 0 |
| ``host_only`` | 宿主在，但**插件进程不在了**（最该报警的一种） | 1 |
| ``down`` | 宿主也没开，插件也不在 | 1 |
| ``odd`` | 插件 IPC 在听但主服务没了（刚启动或端口已换） | 1 |
| ``no_host`` | **本机没有宿主日志目录** ⟹ 这条判据不适用 | 0 |
| ``no_log`` | 有宿主目录但从来没有 dignity 日志 ⟹ 装了没跑过 | 1 |

``no_host`` 与 ``no_log`` 必须分开：前者是「这机器上不该问这个问题」（别人 clone 下来跑测试时），
后者是「问了、答案是没跑」。把前者判红，会让这条判据在 CI 上永远红，然后被人关掉。

## 结构

``evaluate()`` 是纯函数（只读文件系统和 netstat，不打印），``main()`` 只负责打印。
这样测试和 ``gate_extra`` 能复用同一份判断，不会出现「看板说在跑、闸说没跑」那种两套口径。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_DEFAULT_LOG_DIRS = [
    Path("C:/Users/Administrator/AppData/Local/N.E.K.O/logs/plugin"),
    Path.home() / "AppData/Local/N.E.K.O/logs/plugin",
]

#: 存档路径。它只用于展示「多久没更新」，**不参与死活判断**（见模块 docstring）。
_DEFAULT_DATA_DIR = Path("C:/Users/Administrator/AppData/Local/N.E.K.O/plugins/dignity_guard/data")

IPC_RE = re.compile(r"ZeroMQ IPC enabled:\s*tcp://127\.0\.0\.1:(\d+)")
MAIN_RE = re.compile(r"watching\s+http://127\.0\.0\.1:(\d+)")


def log_dirs() -> list[Path]:
    """日志目录候选。每**次**调用都读环境变量。

    为什么不在模块级求值：测试要临时指到假目录（``DIGNITY_LOG_DIR``）。
    import 时求值的话，设了环境变量也不会生效，只能 reload 模块 —— 那种测试很容易
    写成「看起来在测、其实测的还是老路径」。
    """
    override = os.environ.get("DIGNITY_LOG_DIR")
    return [Path(override)] if override else _DEFAULT_LOG_DIRS


def data_dir() -> Path:
    override = os.environ.get("DIGNITY_DATA_DIR")
    return Path(override) if override else _DEFAULT_DATA_DIR


def latest_log() -> Path | None:
    cands: list[Path] = []
    for d in log_dirs():
        if d.is_dir():
            cands.extend(d.glob("N.E.K.O_Plugin_dignity_guard_*.log"))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def listening_ports() -> set[int]:
    """当前处于 LISTENING 的本地端口集合。

    用 ``netstat -ano`` 而不是 socket 连接测试：**只读、无副作用**。
    连一下虽然也能判断端口开没开，但会给 ZMQ 留半开连接。

    编码不可靠（中文系统表头是 GBK），但数据行里 ``:端口`` 与 ``LISTENING``
    都是纯 ASCII，所以 ``errors="replace"`` 不影响判断。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001
        return set()
    ports: set[int] = set()
    for line in out.split("\n"):
        if "LISTENING" not in line:
            continue
        m = re.search(r":(\d+)\s", line)
        if m:
            ports.add(int(m.group(1)))
    return ports


def minutes_ago(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 60


def fmt_age(m: float | None) -> str:
    if m is None:
        return "（无）"
    if m < 1:
        return "刚更新"
    if m < 90:
        return f"{m:.0f} 分钟前"
    if m < 60 * 48:
        return f"{m / 60:.1f} 小时前"
    return f"{m / 1440:.1f} 天前"


@dataclass
class Verdict:
    """一次判活的结论。``line()`` 是给状态板 grep 的那一行。"""

    state: str                     # running / host_only / down / odd / no_host / no_log
    ipc: int | None = None
    main_port: int | None = None
    log_name: str | None = None
    log_age: float | None = None
    db_age: float | None = None

    @property
    def alive(self) -> bool:
        return self.state == "running"

    def line(self) -> str:
        """**格式稳定**的一行结论（``state.sh`` 靠 ``^\\[√\\]|^\\[x\\]`` 抓它）。

        改这个格式会让状态板显示「liveness_check 无输出」而不是报错 —— 静默失效。
        所以 ``tests/test_liveness.py`` 里有一条专门钉格式。
        """
        if self.state == "running":
            return f"[√] 在跑：插件进程活着（IPC {self.ipc} 在听），主服务也在（{self.main_port}）"
        if self.state == "host_only":
            return f"[x] 没在跑：宿主在（主服务 {self.main_port} 在听），但插件进程不在了（IPC 端口没在听）"
        if self.state == "odd":
            return f"[x] 反常：插件 IPC {self.ipc} 在听，但主服务端口没在听 —— 可能刚启动或端口已换"
        if self.state == "no_host":
            return "[—] 本机没有宿主日志目录 ⟹ 这条判据不适用（这不是「没在跑」）"
        if self.state == "no_log":
            return "[x] 没在跑：有宿主日志目录，但从来没有 dignity 的启动记录"
        return "[x] 没在跑：宿主没开（主服务端口不在听），插件也不在"


def evaluate() -> Verdict:
    """算一次结论。纯读，不打印。"""
    dirs = log_dirs()
    if not any(d.is_dir() for d in dirs):
        return Verdict(state="no_host")

    log = latest_log()
    if log is None:
        return Verdict(state="no_log")

    text = log.read_text(encoding="utf-8", errors="replace")
    ipc_hits = IPC_RE.findall(text)
    main_hits = MAIN_RE.findall(text)
    ipc = int(ipc_hits[-1]) if ipc_hits else None      # 取**最后一次**启动的端口
    main_p = int(main_hits[-1]) if main_hits else None

    ports = listening_ports()
    ipc_ok = ipc is not None and ipc in ports
    main_ok = main_p is not None and main_p in ports

    v = Verdict(
        state="down",
        ipc=ipc,
        main_port=main_p,
        log_name=log.name,
        log_age=minutes_ago(log),
        db_age=minutes_ago(data_dir() / "store.db"),
    )
    if ipc_ok and main_ok:
        v.state = "running"
    elif main_ok and not ipc_ok:
        v.state = "host_only"
    elif ipc_ok and not main_ok:
        v.state = "odd"
    return v


def main() -> int:
    print("=== 插件此刻活着吗（T11）===")
    v = evaluate()

    if v.state in ("no_host", "no_log"):
        print(v.line())
        return 0 if v.state == "no_host" else 1

    print(f"日志文件: {v.log_name}（最后更新 {fmt_age(v.log_age)}）")
    print(f"插件 IPC 端口: {v.ipc if v.ipc else '（日志里没写）'} → "
          f"{'LISTENING ✓' if v.state in ('running', 'odd') else '不在听 ✗'}")
    print(f"主服务端口:   {v.main_port if v.main_port else '（日志里没写）'} → "
          f"{'LISTENING ✓' if v.state in ('running', 'host_only') else '不在听 ✗'}")
    print(f"存档最后写入: {fmt_age(v.db_age)}")
    print(v.line())
    return 0 if v.alive else 1


if __name__ == "__main__":
    sys.exit(main())
