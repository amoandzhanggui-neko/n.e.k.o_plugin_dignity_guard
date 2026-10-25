#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
装配检查（skill 工序 7 的产物）

三阶段：导入 → 注册 → 结构+对账

════════════════════════════════════════════════════════════════════
⚠ 诚实边界（必读，别把它当"启动验证"用）
  `NekoPluginBase.__init__(self, ctx)` 的 ctx 是**宿主注入**的，脱离宿主造不出来。
  所以本脚本**验不到"真启动"**。真正的启动验证只有一条：
    宿主日志 AppData/Local/N.E.K.O/logs/plugin/<插件名>_<日期>.log
    里出现当天时间戳的启动记录 + 至少一次业务动作   ← 完工定义第 4 条
  本脚本管的是"代码结构接得上、声明与宿主实际一致"，运行日志管的是"真跑起来了"。
  两条证据链不能互相顶替。

★ 为什么必须跟宿主日志对账（2026-10-25 实测教训）
  只数 `@plugin_entry` 会漏掉一半入口。实测该插件：
    源码 @plugin_entry 14 个，但宿主 collect_entries() 报 20 个 ——
    多出来的是 @lifecycle(3) + @llm_tool(2) + @timer_interval(1)。
  也就是说：**只看 plugin_entry，删掉一个定时器脚本根本发现不了。**
  所以判据必须是"源码声明的全部入口类型" vs "宿主实际注册的清单"。
════════════════════════════════════════════════════════════════════

用法:
    python assembly_check.py                 # 自动探测宿主 ../N.E.K.O
    python assembly_check.py --host <path>
退出码: 0 = 三阶段全过；1 = 有失败
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
FAIL = []

# 会产生「可调用入口」的全部装饰器（少一个就会漏掉一类入口）
ENTRY_DECOS = (
    "plugin_entry", "lifecycle", "timer_interval", "message",
    "on_event", "custom_event", "llm_tool", "hook",
    "before_entry", "after_entry", "around_entry", "replace_entry",
)


def say(stage: str, ok: bool, msg: str, detail: str = "") -> None:
    print(("[√] " if ok else "[x] ") + f"阶段{stage} {msg}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAIL.append(f"阶段{stage}: {msg}" + (f" — {detail}" if detail else ""))


def warn(stage: str, msg: str, detail: str = "") -> None:
    print(f"[!] 阶段{stage} {msg}" + (f" — {detail}" if detail else ""))


def read_toml() -> tuple[str, str, str]:
    p = ROOT / "plugin.toml"
    if not p.exists():
        raise SystemExit("[x] 找不到 plugin.toml")
    txt = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^\s*entry\s*=\s*["\']([^"\']+)["\']', txt, re.M)
    if not m:
        raise SystemExit("[x] plugin.toml 里没有 entry =")
    mod, _, cls = m.group(1).partition(":")
    pid = re.search(r'^\s*id\s*=\s*["\']([^"\']+)["\']', txt, re.M)
    return mod.strip(), cls.strip(), (pid.group(1) if pid else ROOT.name)


def find_host(given: str | None) -> Path | None:
    if given:
        p = Path(given)
        return p if (p / "plugin" / "sdk").is_dir() else None
    for cand in (ROOT.parent / "N.E.K.O", ROOT.parent.parent / "N.E.K.O"):
        if (cand / "plugin" / "sdk").is_dir():
            return cand
    return None


# ─────────────── 阶段 1：导入 ───────────────
def stage_import(host: Path, modpath: str):
    say("1", True, "开始导入插件模块", modpath)
    sys.path.insert(0, str(ROOT.parent))
    sys.path.insert(0, str(host))
    try:
        import importlib

        mod = importlib.import_module(modpath)
    except SyntaxError as e:
        say("1", False, "语法错误，导入失败", f"{e.filename}:{e.lineno} {e.msg}")
        return None
    except Exception as e:  # noqa: BLE001
        say("1", False, "导入失败", f"{type(e).__name__}: {e}")
        return None
    say("1", True, "导入成功", f"{getattr(mod, '__file__', '?')}")
    return mod


# ─────────────── 阶段 2：注册（静态扫全部入口类型）───────────────
def scan_declared() -> list[tuple[str, str]]:
    src = (ROOT / "__init__.py").read_text(encoding="utf-8", errors="replace")
    clean = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    out: list[tuple[str, str]] = []
    for deco in ENTRY_DECOS:
        # 带参数写法，可能跨行；用非贪婪匹配到配对括号的第一个 )
        for m in re.finditer(r"@" + deco + r"\s*\(([^)]*)\)", clean, re.S):
            body = m.group(1)
            nm = re.search(r"\bid\s*=\s*[\"']([^\"']+)[\"']", body) or \
                 re.search(r"\bname\s*=\s*[\"']([^\"']+)[\"']", body)
            if nm:
                out.append((deco, nm.group(1)))
            else:
                # 取紧跟装饰器后的 def 名
                tail = clean[m.end():m.end() + 400]
                fn = re.search(r"def\s+([A-Za-z_]\w*)\s*\(", tail)
                out.append((deco, fn.group(1) if fn else "(未知)"))
        # 无参数写法 @timer_interval 单独一行（也有带参数形式，上面已覆盖）
    return out


def stage_register(mod, want_cls: str):
    from plugin.sdk.plugin import NekoPluginBase

    cls = getattr(mod, want_cls, None)
    if cls is None:
        say("2", False, f"模块里找不到类 {want_cls}")
        return None
    if not (isinstance(cls, type) and issubclass(cls, NekoPluginBase)):
        say("2", False, f"{want_cls} 没继承 NekoPluginBase")
        return None
    say("2", True, f"{want_cls} 继承 NekoPluginBase")

    src = (ROOT / "__init__.py").read_text(encoding="utf-8", errors="replace")
    if re.search(r"@neko_plugin\s*\n\s*class\s+" + re.escape(want_cls), src):
        say("2", True, "@neko_plugin 装饰存在", "源码命中")
    else:
        say("2", False, "没找到 @neko_plugin 装饰", "宿主不会把它当插件注册")

    decl = scan_declared()
    from collections import Counter

    c = Counter(d[0] for d in decl)
    say("2", True, "源码声明的入口总数", f"{len(decl)} 个　明细 {dict(c)}")
    for i, (k, n) in enumerate(decl, 1):
        print(f"       {i:2d}. [{k}] {n}")
    return {"cls": cls, "declared": decl}


# ─────────────── 阶段 3a：宿主日志对账 ───────────────
def read_host_entries(plugin_id: str):
    logdir = Path("C:/Users/Administrator/AppData/Local/N.E.K.O/logs/plugin")
    if not logdir.is_dir():
        return None, "日志目录不存在"
    logs = sorted(logdir.glob(f"N.E.K.O_Plugin_{plugin_id}_2*.log"), reverse=True)
    if not logs:
        return None, f"找不到 {plugin_id} 的日志"
    log = logs[0]
    txt = log.read_text(encoding="utf-8", errors="replace")
    hits = re.findall(r"entries collected via collect_entries\(\):\s*(\[[^\]]*\])", txt)
    if not hits:
        return None, f"{log.name} 里没有 entries collected 记录"
    try:
        lst = ast.literal_eval(hits[-1])
    except (ValueError, SyntaxError) as e:
        return None, f"解析失败: {e}"
    return (lst, log.name), ""


def stage_reconcile(declared: list[tuple[str, str]], plugin_id: str):
    host_entries, why = read_host_entries(plugin_id)
    if host_entries is None:
        warn("3", "拿不到宿主实际清单，跳过对账", why)
        return None
    entries, logname = host_entries
    print(f"[√] 阶段3 宿主实际注册入口 {len(entries)} 个（来源 {logname}）")

    # 源码静态清单里能对上宿主清单的判定：名字相等，或宿主项以 __llm_tool__ 开头且尾部匹配
    def matched(name: str) -> bool:
        return name in entries or ("__llm_tool__" + name) in entries

    missing = [n for _, n in declared if not matched(n)]
    if missing:
        say("3", False, "这些入口源码里有、宿主没注册上", f"{missing}")
    else:
        say("3", True, "源码声明的入口全部在宿主清单里", f"{len(declared)} 项")

    extra = [e for e in entries if e not in {n for _, n in declared}
             and not e.startswith("__llm_tool__")]
    if extra:
        warn("3", "宿主有、源码没声明的（可能是别处注册，需人看一眼）", f"{extra}")

    return entries


# ─────────────── 阶段 3b：结构 + 基线下滑判红 ───────────────
def stage_structure(cls, host_entries: list[str] | None, plugin_id: str):
    import inspect

    try:
        sig = inspect.signature(cls.__init__)
        params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(params) >= 1:
            say("3", True, "__init__ 接受宿主 ctx", f"参数={[p.name for p in params]}")
        else:
            say("3", False, "__init__ 不接受 ctx")
    except (ValueError, TypeError) as e:
        say("3", False, "读不到 __init__ 签名", str(e))

    if host_entries is None:
        warn("3", "没有宿主清单，跳过「比上一轮少」判断")
        return
    seam = ROOT / "seam"
    seam.mkdir(exist_ok=True)

    base = seam / "entries.json"
    prev = None
    if base.exists():
        try:
            prev = json.loads(base.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = None
    cur = {"plugin": plugin_id, "count": len(host_entries), "entries": sorted(host_entries)}
    if prev and isinstance(prev.get("entries"), list):
        lost = [e for e in prev["entries"] if e not in host_entries]
        if lost:
            say("3", False, "入口比上一轮少（等于悄悄删功能）", f"少了 {len(lost)} 个: {lost}")
        else:
            print(f"[√] 阶段3 入口没有减少 — 上一轮 {prev.get('count')} → 现在 {len(host_entries)}")
    else:
        print(f"[√] 阶段3 首次建立入口基线 — {len(host_entries)} 个 → seam/entries.json")
    # newline="\n" 必须显式指定：Windows 上 Path.write_text 默认会把 \n 转成 \r\n，
    # 而本脚本自己就有「seam/ 不许含 CRLF」的判据（判红 6）—— 不加这个参数会自打脸。
    base.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    # 判红 6：**产物写完之后**才验 CRLF。
    # 早先写成"先验后写"，结果每轮验的都是上一轮的旧文件 ⟹ 永远报红（自己踩过这个坑）。
    # \r 进了快照会让哈希跨机器乱跳（本地绿、别人红），所以这条必须是红，不是黄。
    bad_cr = []
    for f in sorted(seam.rglob("*")):
        if f.is_file() and b"\r" in f.read_bytes():
            bad_cr.append(f.relative_to(ROOT).as_posix())
    if bad_cr:
        say("3", False, "seam/ 下有文件含 CRLF（哈希会跨机器乱跳）", f"{bad_cr}")
    else:
        print("[√] 阶段3 seam/ 下产物无 CRLF（.gitattributes 已强制 LF）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    args = ap.parse_args()

    modpath, want_cls, plugin_id = read_toml()
    host = find_host(args.host)
    if host is None:
        print("[x] 阶段0 找不到宿主仓库（需含 plugin/sdk）。用 --host 指定。")
        return 1
    print(f"插件: {plugin_id}　入口: {modpath}:{want_cls}")
    print(f"宿主: {host}")
    print("-" * 62)

    mod = stage_import(host, modpath)
    reg = stage_register(mod, want_cls) if mod else None
    if reg:
        he = stage_reconcile(reg["declared"], plugin_id)
        stage_structure(reg["cls"], he, plugin_id)

    print("-" * 62)
    if FAIL:
        print(f"[x] 装配检查失败（{len(FAIL)} 项）：")
        for f in FAIL:
            print("   - " + f)
        rc = 1
    else:
        print("[√] 装配检查通过")
        rc = 0
    print("\n注：本脚本验「代码结构接得上 + 声明与宿主实际一致」，**不含真启动**。")
    print("    真启动看宿主日志 logs/plugin/<插件名>_<日期>.log（完工定义第 4 条）。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
