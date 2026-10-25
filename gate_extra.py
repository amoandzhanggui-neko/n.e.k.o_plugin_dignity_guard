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
#: 会审台账 —— 判红 7 的**真基线**。不拿它当基线会漏掉一整类情况，见 check_ast。
LEDGER = ROOT / "docs" / "review" / "review_ledger.json"
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


def file_hash16(path: Path) -> str:
    """与 ``neko_review_gate.js`` **同一口径**：``sha256(文件字节).hex()[:16]``。

    为什么必须逐字对齐：台账里的哈希是闸算的，这里若用 md5 或不同截断，
    两边永远不相等 ⟹ 判红 7 会把"什么都没改"判成"改了很多"，
    然后被当成噪声关掉。口径不一致的判定，比没有判定更坏。

    已实测核对过（``.gitattributes``：Python 与台账同为 ``fd4e865bdc7ec81a``）。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ledger_files() -> dict[str, str]:
    """台账**最后一轮**登记的 ``{路径: 哈希}``；没有台账就返回空。"""
    try:
        rounds = (json.loads(LEDGER.read_text(encoding="utf-8")).get("rounds") or [])
        return dict((rounds[-1] or {}).get("files") or {})
    except Exception:  # noqa: BLE001
        return {}


def changed_since_ledger() -> set[str]:
    """自上次会审登记以来内容变了的文件。

    为什么不能只看 ``git status``（这是判红 7 之前的真漏洞）：
      ``git status`` 只报**工作区 vs HEAD**。改动一旦 commit，工作区就干净了，
      于是「改了实现、没改契约、直接提交」这一整类情况**完全检测不到** ——
      而那正是判红 7 要拦的东西。台账是"上次会审那一刻的状态"，
      拿它当基线，提交与否都能看见。
    """
    base = ledger_files()
    if not base:
        return set()
    out: set[str] = set()
    for rel, old in base.items():
        p = ROOT / rel
        if not p.is_file():
            out.add(rel)  # 被删了也算变更
        elif file_hash16(p) != old:
            out.add(rel)
    return out


def contract_interface_changed() -> bool | None:
    """契约接口（语义）有没有变。``None`` = 查不了。

    为什么不直接比 ``contract_snapshot.json`` 的字节哈希：
      那个文件是生成物，重写一次缩进变了哈希就变，但接口一个字没变。
      那样会让"假变更"给真变更当豁免证。``seam_snapshot.py --check``
      比的是**解析出来的接口结构**，缩进无关。
    """
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "seam_snapshot.py"), "--check"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return None


# ─────────────────── 判红 1：字段路径不许动态拼 ───────────────────
# 「字段来源可追溯」的另一半。
#
# 前半（已做）：``seam/contract_snapshot.json`` 里每个字段都记了
#   ``source: contracts/CT-v2.md#C-REV-1``，能回答"这个字段从哪条契约来"。
#
# 后半（本函数）：代码里写字段路径时**不许用拼接生成**。
#
# 为什么这是判红而不是风格建议：
#   契约声明的是**精确路径**（``characters.主人.昵称``），逐字段断言靠它。
#   一旦代码写成 ``f"characters.{name}.{field}"``，静态分析看到的就只是一个
#   表达式，不是"一个已知字段"。于是契约说"我覆盖了 A"，代码实际写到了 ``A.b``
#   —— 断言全过，而真正被写的那条路径一次都没被检查过。
#   两边都能自圆其说，说的却不是同一件事。
#
# 现状（2026-10-25 实测）：代码里**零处**拼接，全部是静态常量表
# （``LEVEL_RULES`` / ``REVERTIBLE_PREFIX`` / 两条工具描述）。所以这条一上线
# 就是绿的 —— 它拦的是将来，不是现在。

_PATH_HEADS = ("characters.", "preferences.")


def _dynamic_key_spots(tree: ast.AST) -> list[tuple[int, str]]:
    """找出把字段路径拼出来的位置，返回 ``[(行号, 人话原因)]``。

    用 AST 而不是正则：正则漏跨行、漏括号嵌套、还会把注释里的示例当真。
    """
    found: list[tuple[int, str]] = []

    def has_head(value: object) -> bool:
        return isinstance(value, str) and any(h in value for h in _PATH_HEADS)

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        # f-string：f"characters.{name}.昵称"
        if isinstance(node, ast.JoinedStr):
            if any(isinstance(p, ast.Constant) and has_head(p.value) for p in node.values):
                found.append((line, "f-string 里含字段路径前缀"))
        # 加法拼接："characters." + name
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if any(isinstance(s, ast.Constant) and has_head(s.value) for s in (node.left, node.right)):
                found.append((line, "用 + 拼接字段路径"))
        # % 格式化："characters.%s.昵称" % name
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            if isinstance(node.left, ast.Constant) and has_head(node.left.value):
                found.append((line, "用 % 格式化字段路径"))
        # "characters.{}.昵称".format(name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "format" and isinstance(node.func.value, ast.Constant) \
                    and has_head(node.func.value.value):
                found.append((line, "用 .format() 生成字段路径"))
    return found


def check_dynamic_keys() -> None:
    print("\n=== 判红 1：字段路径不许动态拼 ===")
    owner: set[str] = set()
    if BLOCKS.exists():
        try:
            owner = set(json.loads(BLOCKS.read_text(encoding="utf-8")).get("file_owner", {}).keys())
        except Exception:  # noqa: BLE001
            owner = set()
    # 有块清单就只扫块受控文件（工具脚本自身不写设置，扫进来纯噪声）。
    targets = sorted(
        p for p in (owner or {f.name for f in ROOT.glob("*.py")})
        if p.endswith(".py") and (ROOT / p).is_file()
    )
    bad = 0
    for rel in targets:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            yellow(f"{rel} 语法读不通，跳过动态 key 扫描（AST 那条会更严地处理它）")
            continue
        hits = _dynamic_key_spots(tree)
        if not hits:
            continue
        bad += len({line for line, _ in hits})
        for line, why in hits:
            red(f"{rel}:{line} {why} —— 契约声明的字段必须是可静态识别的常量（判红 1）")
    if not bad:
        ok(f"受控 .py 共 {len(targets)} 个，字段路径全是静态常量，没有拼出来的")


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
    """判红 7：实现语义变了，契约必须跟着变。

    2026-10-25 重写。旧版有两个洞，都会让这条判据在关键时刻失明：

      ① **只看 ``git status``** —— 改动一旦 commit，工作区就干净了，
         「改了实现、没动契约、直接提交」整类情况检测不到。
         现在基线改成**台账最后一轮登记的哈希**（提交与否都看得见）。
      ② **"快照变了"用文件是否出现在 diff 里判断** —— 但快照是生成物，
         重新生成一次、缩进不同就算"变了"，于是"假变更"能给"真变更"当豁免证。
         现在改成看**契约文件自己**有没有变（``contracts/`` 下的哈希），
         再用 ``seam_snapshot.py --check`` 确认快照跟契约没脱钩。

    判据表（实现侧 = 块受控 .py 的 AST 实质变更）：

      ==============  ==============  ================================
      实现变了         契约变了         结论
      ==============  ==============  ================================
      是              是（快照同步）   ✅ 正常变更
      是              是（快照没更新） 🔴 契约改了却没重新生成快照
      是              否               🔴 改了语义却没动契约
      否（仅排版）    —                黄：作废本轮验收、不阻断
      ==============  ==============  ================================
    """
    print("\n=== 判红 7：实现语义变更 → 契约必须同步 ===")
    # 只看「块受控文件」。
    # 为什么不看全部 .py：改一个开发工具（block_check.py / seam_snapshot.py）也会改 AST，
    # 但它跟接缝无关 —— 全量检查会把工具维护误报成"改了语义没动契约"。
    owner: set[str] = set()
    if BLOCKS.exists():
        try:
            owner = set(json.loads(BLOCKS.read_text(encoding="utf-8")).get("file_owner", {}).keys())
        except Exception:  # noqa: BLE001
            owner = set()

    worktree = changed_files()
    since_ledger = changed_since_ledger()
    moved = worktree | since_ledger

    # ── 先单独查一件事：契约改了，快照跟上了吗 ──
    # 这条与"实现有没有变"无关：只要契约变了而快照没重生成，
    # 后面所有比对都是在拿一份过期快照说话。
    contract_touched = sorted(p for p in since_ledger if p.startswith("contracts/"))
    consistent = contract_interface_changed()
    if contract_touched:
        if consistent is True:
            ok(f"契约文件变了（{len(contract_touched)} 个），快照与之一致")
        elif consistent is False:
            red(
                "契约文件变了，但 seam/contract_snapshot.json 没跟着重生成 —— "
                "后面的比对会用过期快照（先跑 python seam_snapshot.py）"
            )
        else:
            yellow("契约文件变了，但快照检查跑不起来 ⟹ 无法确认是否同步")

    if not changed_since_ledger() and not worktree:
        ok("自上次会审以来没有任何改动")
        return

    candidates = sorted(
        f for f in moved
        if f.endswith(".py") and (ROOT / f).is_file() and (not owner or f in owner)
    )
    if not candidates:
        ok("受控 .py 没有变化（改的是文档/工具/契约，不构成本条判红）")
        return

    contract_changed = bool(contract_touched)
    for f in candidates:
        cur_src = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        cur_ast = ast_dump(cur_src)

        if cur_ast is None:
            yellow(f"{f} 语法读不通，读不到 AST（按最严当实质变更）")
            substantive = True
        elif f not in worktree:
            # 已提交：HEAD == 工作区，拿 HEAD 比 AST 得不到信息。
            # 台账只存哈希不存内容，所以"改了什么"拿不到 ⟹ 按最严当实质变更。
            # 这不是偷懒：判不了就放过，才是这条判据最坏的失效方式。
            yellow(f"{f} 自上次会审以来变过且已提交 —— 拿不到登记时的内容，按最严当实质变更")
            substantive = True
        else:
            head_src = git("show", f"HEAD:{f}")
            if not head_src.strip():
                print(f"  [新] {f}（HEAD 里没有，新增文件按实质变更）")
                substantive = True
            else:
                head_ast = ast_dump(head_src)
                if head_ast is None:
                    yellow(f"{f} HEAD 版本读不到 AST（按最严当实质变更）")
                    substantive = True
                elif head_ast == cur_ast:
                    yellow(f"{f} 只有排版变化（AST 等价）⟹ 作废本轮验收、不阻断")
                    substantive = False
                else:
                    substantive = True

        if not substantive:
            continue
        if contract_changed:
            ok(f"{f} AST 实质变更，且契约同步变了 —— 正常变更")
        else:
            red(
                f"{f} AST 实质变更，但 contracts/ 下没有改动 —— "
                "改了语义却没动契约（判红 7）。要么补契约，要么证明这次改动不触及契约"
            )


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
    """判红 9：一轮内契约改了几次（改太多次 ⟹ 契约没定下来，回去重定）。

    轮次边界用台账最后一次 stamp —— 按 skill §2.5-7，不许用 run_id 当边界（它未实现），
    改用 `--stamp` 台账的 chain head：链已存在、机器可判、语义等价。

    ★ 2026-10-25 修掉两个漏（都是实测撞出来的）：

      ① **未提交的改动不算**。旧版只数 `git log`；实测：改了 CT-v2.md 却报
         「契约改了 0 次」。工作区里那次改动完全隐形。
      ② **只盯 `seam/contract_snapshot.json`（生成物）**。改契约本身不算 ——
         只要能不重新生成快照，改几次都数不到。

      现在两者相加：已提交的往返次数（`git log`）+ 当前尚未登记的那一次。
      「改了几次」这件事只有提交历史数得准；而"还没提交的那次"必须补上，
      否则判据在最该生效的时刻（正在改）是瞎的。
    """
    print("\n=== 判红 9：一轮内契约变更次数 ===")
    round_t = None
    try:
        rounds = (json.loads(LEDGER.read_text(encoding="utf-8")).get("rounds") or []) if LEDGER.exists() else []
        if rounds:
            round_t = rounds[-1].get("at") or rounds[-1].get("t")
    except Exception:  # noqa: BLE001
        round_t = None

    args = ["log", "--oneline"]
    if round_t:
        args.append(f"--since={round_t}")
    args += ["--", "contracts/", "seam/contract_snapshot.json"]
    committed = len([line for line in git(*args).split("\n") if line.strip()])

    dirty_paths = {p for p in (changed_since_ledger() | changed_files())
                   if p.startswith("contracts/") or p == "seam/contract_snapshot.json"}
    n = committed + (1 if dirty_paths else 0)

    scope = f"自上次会审（{str(round_t)[:19]}）以来" if round_t else "全历史"
    detail = f"（已提交 {committed} 次 + 当前未登记 1 次）" if dirty_paths else ""
    if n >= 3:
        red(f"{scope}契约改了 {n} 次{detail} ⟹ 停该接缝，回工序 3 重定契约")
    elif n == 2:
        yellow(f"{scope}契约改了 2 次{detail} ⟹ 本轮验收作废、重跑（不阻断全局）")
    else:
        ok(f"{scope}契约改了 {n} 次{detail}，未越线")


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
    if run_all or "--dyn" in want:
        check_dynamic_keys()
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
    print(f"[√] 全部判定通过" + (f"（{len(YELLOW)} 条黄，不阻断）" if YELLOW else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
