#!/usr/bin/env python
"""插件此刻活着吗？（T11）

## 它解决的问题

在这之前，所有判据都只能回答「**启动过没有**」：

- 完工定义第 4 条：日志有当天启动记录 + 至少一次业务动作
- `state.sh` 第 5 节：同一个口径

而 2026-10-25 实测撞到过：插件 11:28 启动、12:12 停止，之后 4 小时没在跑，
**但所有闸全绿** —— 因为"今天开过一次"永远成立。那是**真假绿**，
比红更危险：红会让人停手，假绿会让人放心。

## 判据：看端口，不看进程名

判"活着"最直觉的做法是查进程，但**进程名靠不住**：宿主叫 `N.E.K.O` / `electron` / 别的，
换个宿主版本就变；而且本机实测 `wmic process get name` 会返回空（PID 50120 明明是 python.exe）。

所以改看**端口**，而且端口不是我猜的，是**插件自己吐在日志里的**：

| 端口 | 从日志哪一行读 | 它证明什么 |
|---|---|---|
| 插件 IPC | ``ZeroMQ IPC enabled: tcp://127.0.0.1:<port>`` | **插件进程活着**（这条最关键） |
| 主服务 | ``watching http://127.0.0.1:<port> every 20s`` | 宿主的主服务在（插件要连的对象） |

判据表：

    IPC 在听   主服务在听   结论
    --------  ----------  ----------------------------------
       ✓          ✓       在跑
       ✗          ✓       宿主在，**插件没跑**（最该报警的一种）
       ✗          ✗       宿主也没开
       ✓          ✗       反常：插件活着但主服务不见了

## 为什么不用"日志多久没更新"当判据

因为**安静运行时不写日志是正常的**。插件每 20 秒轮询一次，没有变化就不落一行。
拿 mtime 当死活判据，会把"正常安静"报成"死了"——那是另一种假信号。

所以这里只把"多久没更新"当**信息**展示（T12），不当判据。

退出码：0 = 在跑；1 = 没在跑或异常。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

#: 宿主日志目录（与 state.sh 第 5 节用同一个）。
#:
#: ``DIGNITY_LOG_DIR`` 可以覆盖它 —— 这不是给生产用的开关，是为了**负向验证**：
#: 要证明"插件不在跑"时脚本真的会报红，就得造一份带假端口的日志，
#: 而绝不能去改真日志（那是伪造现场）。
_DEFAULT_LOG_DIRS = [
    Path("C:/Users/Administrator/AppData/Local/N.E.K.O/logs/plugin"),
    Path.home() / "AppData/Local/N.E.K.O/logs/plugin",
]
_override = os.environ.get("DIGNITY_LOG_DIR")
LOG_DIRS = [Path(_override)] if _override else _DEFAULT_LOG_DIRS
DATA_DIR = Path("C:/Users/Administrator/AppData/Local/N.E.K.O/plugins/dignity_guard/data")

IPC_RE = re.compile(r"ZeroMQ IPC enabled:\s*tcp://127\.0\.0\.1:(\d+)")
MAIN_RE = re.compile(r"watching\s+http://127\.0\.0\.1:(\d+)")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def latest_log() -> Path | None:
    cands: list[Path] = []
    for d in LOG_DIRS:
        if d.is_dir():
            cands.extend(d.glob("N.E.K.O_Plugin_dignity_guard_*.log"))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def listening_ports() -> set[int]:
    """当前处于 LISTENING 的本地端口集合。

    用 ``netstat -ano`` 而不是查进程名：进程名随宿主版本变，端口是硬事实。
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
        m = re.search(r":(\d+)\s+\S+\s+LISTENING", line) or re.search(r":(\d+)\s", line)
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


def main() -> int:
    print("=== 插件此刻活着吗（T11）===")
    log = latest_log()
    if log is None:
        print("[x] 没在跑：找不到任何 dignity 插件日志 ⟹ 从来没启动过")
        return 1

    text = log.read_text(encoding="utf-8", errors="replace")
    ipc_hits = IPC_RE.findall(text)
    main_hits = MAIN_RE.findall(text)
    ipc = int(ipc_hits[-1]) if ipc_hits else None      # 取**最后一次**启动的端口
    main_p = int(main_hits[-1]) if main_hits else None

    ports = listening_ports()
    ipc_ok = ipc is not None and ipc in ports
    main_ok = main_p is not None and main_p in ports

    print(f"日志文件: {log.name}（最后更新 {fmt_age(minutes_ago(log))}）")
    print(f"插件 IPC 端口: {ipc if ipc else '（日志里没写）'} → "
          f"{'LISTENING ✓' if ipc_ok else '不在听 ✗'}")
    print(f"主服务端口:   {main_p if main_p else '（日志里没写）'} → "
          f"{'LISTENING ✓' if main_ok else '不在听 ✗'}")
    print(f"存档最后写入: {fmt_age(minutes_ago(DATA_DIR / 'store.db'))}")

    if ipc_ok and main_ok:
        print(f"[√] 在跑：插件进程活着（IPC {ipc} 在听），主服务也在（{main_p}）")
        return 0
    if main_ok and not ipc_ok:
        print(f"[x] 没在跑：宿主在（主服务 {main_p} 在听），但**插件进程不在了**（IPC 端口没在听）")
        return 1
    if ipc_ok and not main_ok:
        print(f"[x] 反常：插件 IPC {ipc} 在听，但主服务端口没在听 —— 可能刚启动或端口已换")
        return 1
    print("[x] 没在跑：宿主没开（主服务端口不在听），插件也不在")
    return 1


if __name__ == "__main__":
    sys.exit(main())
