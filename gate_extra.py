#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三条判定逻辑（补 §4-R2 与判红 4 / 7）

之前卡在"缺输入"，现在输入齐了（块清单 + 接缝快照），把判定写出来。

────────────────────────────────────────────────────────────
§4-R2 接缝私约
  判据：一次改动涉及 ≥2 个块的文件 ⟹ 红（说明有人跨块改接缝却没走接缝流程）
  豁免：热点块（B6-entry）除外 —— 入口层天然跨块，不豁免会天天误报
  （依据：块清单「已知边界」第 2 条，__init__.py 3266 行导致块边界≠文件边界）

判红 7 快照未变 ∧ AST 实质变更 ⟹ 红
  手段：ast.dump 对比 HEAD 版与工作区版（不含位置信息），纯排版 ⟹ 黄，实质 ⟹ 红
  为什么不能用 node --check：那只证语法，证不了"语义有没有动"

判红 4 三键复用
  三键：①本块源码哈希 ②本块契约字段哈希 ③上游块的 out_fields
  只有三键全没变，旧快照才可复用；缺第三键会出现"B 复用旧快照 ⟹ 假绿"

用法:
    python gate_extra.py            # 三条一起跑
    python gate_extra.py --seam     # 只跑接缝私约
    python gate_extra.py --ast      # 只跑 AST
    python gate_extra.py --keys     # 只跑三键
退出码: 0 = 全绿；1 = 有红
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
SEAM = ROOT / "seam"
BLOCKS = SEAM / "blocks.json"
CONTRACTS = SEAM / "contract_snapshot.json"
KEYS = SEAM / "three_keys.json"
RED: list[str] = []
YELLOW: list[str] = []


def red(m: str) -> None:
    print("[红] " + m)
    RED.append(m)


def yellow(m: str) -> None:
    print("[黄] " + m)
    YELLOW.append(m)


def ok(m: str) -> None:
    print("[√] " + m)


def git(*args: str) -> str:
    try:
        r = subprocess.run(["git", "-c", "core.quotePath=false", *args],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def changed_files() -> set[str]:
    """工作区里被改动 + 未跟踪的文件（相对路径，正斜杠）。"""
    out: set[str] = set()
    for line in git("status", "--porcelain").split("\n"):
        if not line.strip():
            continue
        p = line[3:].strip()
        if " -> " in p:
            p = p.split(" -> ")[-1]
        p = p.strip().strip('"').replace("\\", "/")
        if p:
            out.add(p)
    return out


def sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ─────────────────── §4-R2 接缝私约 ───────────────────
def check_seam_private() -> None:
    print("\n=== §4-R2 接缝私约 ===")
    if not BLOCKS.exists():
        yellow("没有 seam/blocks.json（先跑 block_check.py），跳过")
        return
    data = json.loads(BLOCKS.read_text(encoding="utf-8"))
    owner = data.get("file_owner", {})
    blocks = data.get("blocks", {})
    hots = {bid for bid, b in blocks.items() if b.get("hot")}
    hot_files = {f for bid in hots for f in blocks[bid].get("files", [])}

    ch = changed_files()
    touched = {owner[f]: f for f in ch if f in owner}
    if not touched:
        ok("本次改动没碰任何块的受控文件（或只有未受控文件）")
        return

    by_block: dict[str, list[str]] = {}
    for bid, f in touched.items():
        by_block.setdefault(bid, []).append(f)

    print(f"  涉及 {len(by_block)} 个块：" + "；".join(f"{b}={v}" for b, v in sorted(by_block.items())))

    # 收紧（2026-10-25，红队指出原豁免可被绕过：顺手改一行 __init__.py 就能豁免全部）
    # 新规则：热点块只额外容忍「1 个」非热点块；跨 ≥2 个非热点块一律红。
    # 理由：入口层确实天然跨块（改一个功能要动 __init__.py + 业务模块是正常的），
    #      但"动一个入口 + 两个业务模块"就不是正常开发了，是在私连接缝。
    non_hot = {b for b in by_block if b not in hots}
    has_hot = any(b in hots for b in by_block)

    if has_hot and len(non_hot) <= 1:
        ok(f"含热点块且只牵动 {len(non_hot)} 个非热点块 ⟹ 按「入口层天然跨块」豁免")
        return
    if len(non_hot) >= 2:
        red(f"跨了 {len(non_hot)} 个非热点块（{sorted(non_hot)}）—— 接缝私约（热点豁免不掩盖跨块私连）")
        return
    if len(by_block) >= 2:
        red(f"一次改动跨了 {len(by_block)} 个块（{sorted(by_block)}）却没走接缝流程 —— 接缝私约")
    else:
        ok("单块改动，不构成接缝私约")


# ─────────────────── 判红 7：AST 实质变更 ───────────────────
def ast_dump(src: str) -> str | None:
    try:
        return ast.dump(ast.parse(src), annotate_fields=True, include_attributes=False)
    except SyntaxError:
        return None


def check_ast() -> None:
    print("\n=== 判红 7：AST 实质变更 ===")
    # 只看「块受控文件」的 AST。
    # 为什么不看全部 .py：改一个开发工具（block_check.py / seam_snapshot.py）也会改 AST，
    # 但它跟接缝无关 —— 全量检查会把工具维护误报成"改了语义没动契约"。
    owner: set[str] = set()
    if BLOCKS.exists():
        try:
            owner = set(json.loads(BLOCKS.read_text(encoding="utf-8")).get("file_owner", {}).keys())
        except Exception:  # noqa: BLE001
            owner = set()

    ch = changed_files()
    pys = sorted(f for f in ch if f.endswith(".py") and (ROOT / f).exists() and (not owner or f in owner))
    if not pys:
        ok("本次没改动任何块的受控 .py 文件")
        return

    # 判红 7 的前提："快照未变"。这里先看契约快照有没有变（当前 vs HEAD）
    snap_changed = "seam/contract_snapshot.json" in ch

    for f in pys:
        old = git("show", f"HEAD:{f}")
        if not old.strip():
            print(f"  [新] {f}（HEAD 里没有，属新增文件）")
            continue
        new = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        a, b = ast_dump(old), ast_dump(new)
        if a is None or b is None:
            yellow(f"{f} 有语法错误，读不到 AST（按最严处理：当实质变更）")
            continue
        if a == b:
            yellow(f"{f} 只有排版变化（AST 等价）⟹ 作废本轮验收、不阻断")
        else:
            if snap_changed:
                print(f"  [√] {f} AST 实质变更，且快照同步变了 —— 正常变更")
            else:
                red(f"{f} AST 实质变更，但契约快照没变 —— 改了语义却没动契约（判红 7）")


# ─────────────────── 判红 4：三键复用 ───────────────────
def check_three_keys() -> None:
    print("\n=== 判红 4：快照三键 ===")
    if not BLOCKS.exists() or not CONTRACTS.exists():
        yellow("缺 blocks.json 或 contract_snapshot.json，跳过")
        return
    blocks = json.loads(BLOCKS.read_text(encoding="utf-8")).get("blocks", {})
    cts = json.loads(CONTRACTS.read_text(encoding="utf-8")).get("contracts", {})

    cur: dict[str, dict] = {}
    for bid, b in sorted(blocks.items()):
        # 键1：本块源码哈希（受控文件内容）
        src_parts = []
        for f in b.get("files", []):
            p = ROOT / f
            src_parts.append(f + ":" + (hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else "缺失"))
        k1 = sha(*src_parts)
        # 键2：本块契约字段哈希（该块声明的契约，从快照取 fields_hash）
        k2 = sha(*[f"{c}:{cts[c]['fields_hash']}" for c in b.get("contracts", []) if c in cts])
        # 键3：上游块的 out_fields（依赖块的契约字段集）
        up = []
        for d in b.get("depends", []):
            for c in blocks.get(d, {}).get("contracts", []):
                if c in cts:
                    up.append(c + "=" + ",".join(f["name"] for f in cts[c]["fields"]))
        k3 = sha(*up)
        cur[bid] = {"src": k1, "contract": k2, "upstream": k3}

    old = None
    if KEYS.exists():
        try:
            old = json.loads(KEYS.read_text(encoding="utf-8")).get("keys", {})
        except Exception:  # noqa: BLE001
            old = None

    if old:
        print(f"  {'块':<12} {'源码':<18} {'契约':<18} {'上游'}")
        for bid in sorted(cur):
            o, n = old.get(bid, {}), cur[bid]
            mark = "  " if o == n else "≠"
            print(f"  {mark}{bid:<11} {n['src']:<18} {n['contract']:<18} {n['upstream']}")
            if o and o.get("contract") != n["contract"] and o.get("src") == n["src"]:
                red(f"{bid} 契约变了但源码没变 —— 契约与实现脱节")
            if o and o.get("src") != n["src"] and o.get("upstream") != n["upstream"]:
                yellow(f"{bid} 源码与上游都变了 —— 下游块必须重跑（快照不可复用）")
        if not any("脱节" in r for r in RED):
            ok("三键已比对（标记 ≠ 的块表示键变了）")
    else:
        ok("首次建立三键基线")

    SEAM.mkdir(exist_ok=True)
    KEYS.write_text(json.dumps({"keys": cur}, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8", newline="\n")


# ─────────────────── 判红 9：一轮内契约变更次数 ───────────────────
def check_contract_churn() -> None:
    """判红 9：一轮内契约快照改了几次。

    轮次边界用台账最后一次 stamp —— 按 skill §2.5-7，不许用 run_id 当边界（它未实现），
    改用 `--stamp` 台账的 chain head：链已存在、机器可判、语义等价。
    """
    print("\n=== 判红 9：一轮内契约变更次数 ===")
    round_t = None
    led = ROOT / "docs" / "review" / "review_ledger.json"
    if led.exists():
        try:
            rounds = json.loads(led.read_text(encoding="utf-8")).get("rounds", [])
            if rounds:
                # 台账字段是 at（不是 t）；兼容两种写法
                round_t = rounds[-1].get("at") or rounds[-1].get("t")
        except Exception:  # noqa: BLE001
            pass

    args = ["log", "--oneline", "--", "seam/contract_snapshot.json"]
    if round_t:
        args = ["log", "--oneline", f"--since={round_t}", "--", "seam/contract_snapshot.json"]
    n = len([l for l in git(*args).split("\n") if l.strip()])
    if "seam/contract_snapshot.json" in changed_files():
        n += 1  # 工作区里还有一次未提交的契约改动，也算

    scope = f"自上次会审（{str(round_t)[:19]}）以来" if round_t else "全历史"
    if n >= 3:
        red(f"{scope}契约改了 {n} 次 ⟹ 停该接缝，回工序 3 重定契约")
    elif n == 2:
        yellow(f"{scope}契约改了 2 次 ⟹ 本轮验收作废、重跑（不阻断全局）")
    else:
        ok(f"{scope}契约改了 {n} 次，未越线")


# ─────────────────── 判红 10：三零同现 ⟹ 空转 ───────────────────
def check_zero_triple() -> None:
    """判红 10：退回=0 且 未证实=0 且 不一定=0 ⟹ 空转，不算放行。

    数据来源：最近一份裁决表（docs/裁决表-*.md）里数三类。
    为什么必须查：三份意见单全是"无问题"是**最危险的信号**，不是最好的信号。
    """
    print("\n=== 判红 10：三零同现检查 ===")
    # 按修改时间排最新 —— 不能按文件名：中文名排序会把"首次会审"排在"第2轮"前面（首 U+9996 > 第 U+7B2C）
    docs = sorted((ROOT / "docs").glob("裁决表-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not docs:
        yellow("没有裁决表，无从判断（先去会审）")
        return
    t = docs[0].read_text(encoding="utf-8", errors="replace")
    # 否决：裁决表「裁决」列里写否决的行（排除"0 否决"这类总结句）
    veto = len(re.findall(r"^\|[^\n|]*\|[^\n|]*\|\s*\**\s*否决", t, re.M))
    # 未证实项：U1/U2/... 的表格行
    unv = len(re.findall(r"^\|\s*U\d+\s*\|", t, re.M))
    # 不一定：交付三分类表里那一栏
    unc = len(re.findall(r"^\|\s*C-[A-Za-z0-9\-]+\s*\|[^\n|]*不一定", t, re.M))

    print(f"  来源：{docs[0].name}　否决={veto}　未证实={unv}　不一定={unc}")
    if veto == 0 and unv == 0 and unc == 0:
        red("三零同现（否决=0 且 未证实=0 且 不一定=0）⟹ 空转/空审，不算放行")
    else:
        ok("三个数不是全零，不构成空转")


def main() -> int:
    want = set(a for a in sys.argv[1:] if a.startswith("--"))
    run_all = not want
    if run_all or "--seam" in want:
        check_seam_private()
    if run_all or "--ast" in want:
        check_ast()
    if run_all or "--keys" in want:
        check_three_keys()
    if run_all or "--churn" in want:
        check_contract_churn()
    if run_all or "--zero" in want:
        check_zero_triple()

    print("\n" + "-" * 56)
    if RED:
        print(f"[x] 有 {len(RED)} 条红：")
        for r in RED:
            print("   - " + r)
        return 1
    print(f"[√] 三条判定全绿" + (f"（{len(YELLOW)} 条黄，不阻断）" if YELLOW else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
