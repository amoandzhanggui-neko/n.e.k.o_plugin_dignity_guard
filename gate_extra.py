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


#: 当前哈希口径。台账每轮的 ``hash_algo`` 记的就是这个字符串。
#:
#: 2026-10-25 之前是 ``sha256-16-raw``（直接算工作区字节）。因为仓库里两种行尾并存
#: （.gitattributes 加之前 checkout 的是 CRLF、之后写的是 LF），同一份代码在工作区和
#: git blob 里哈希不同 ⟹ 台账跨机器不可比。改成归一化到 LF 之后跨机器一致。
#:
#: **两种口径的数字不能互相比。** 台账里老轮次保留了旧口径的真实值，
#: 只加了标签，没有篡改历史；但拿它当基线会得出完全错误的结论，
#: 所以 `ledger_files()` 会拒绝口径不符的轮次。
HASH_ALGO = "sha256-16-lf"


def file_hash16(path: Path) -> str:
    """与 ``neko_review_gate.js`` **同一口径**：``sha256(归一化到 LF 的字节).hex()[:16]``。

    为什么必须逐字对齐：台账里的哈希是闸算的，这里若用 md5 或不同截断，
    两边永远不相等 ⟹ 判红 7 会把"什么都没改"判成"改了很多"，
    然后被当成噪声关掉。口径不一致的判定，比没有判定更坏。

    ★ 2026-10-25 加归一化。之前直接算工作区字节，而本仓库里**两种行尾并存**
    （`.gitattributes` 加之前 checkout 的文件是 CRLF，之后写的是 LF）⟹
    同一个文件"工作区哈希"与"git blob 哈希"不同，换台机器台账全部对不上。
    归一化到 LF 后：与工作区行尾无关、与 git blob 一致、跨机器可比。

    实测对齐（``__init__.py``）：归一化后 = git blob = ``80f06b9c2372d399``。
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def ledger_algo() -> str | None:
    """台账最后一轮用的哈希口径；没有台账或没标 ⟹ ``None``。"""
    try:
        rounds = (json.loads(LEDGER.read_text(encoding="utf-8")).get("rounds") or [])
        if not rounds:
            return None
        return (rounds[-1] or {}).get("hash_algo")
    except Exception:  # noqa: BLE001
        return None


def ledger_files() -> dict[str, str]:
    """台账**最后一轮**登记的 ``{路径: 哈希}``；口径不符或没有台账 ⟹ 空。

    ★ 2026-10-25 加口径检查（T5）。第 1–4 轮存的是 ``sha256-16-raw``（原始字节），
    第 5 轮起是 ``sha256-16-lf``。两种数字**不可比**：拿旧轮当基线，
    会把所有 CRLF 文件判成"改了"，然后被判红 7 报成一片红 —— 而那是假红，
    假红的下场是有人把它当噪声关掉，连真红一起关掉。

    所以这里宁可返回空（调用方退回只看 git status，能力弱一点但不会说谎），
    也不拿口径不符的数字去比。
    """
    if ledger_algo() != HASH_ALGO:
        return {}
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
    algo = ledger_algo()
    if algo is not None and algo != HASH_ALGO:
        yellow(
            f"台账最后一轮用的是 {algo}，当前口径是 {HASH_ALGO} ⟹ 两边数字不可比，"
            "本轮退回只看工作区改动（能力弱一点，但不会拿不可比的数字骗人）"
        )
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

    # ★ 2026-10-25 修：这里原来用 `changed_since_ledger()`，会和 `git log` **重复计数**。
    #
    #   症状（实测踩到）：这一轮只提交了一次 CT-v2 改动，工作区也干净，
    #   它却报「契约改了 2 次 ⟹ 本轮验收作废」。原因是：
    #     · `git log --since=<上轮>` 数到了那次提交（1 次）✓
    #     · `changed_since_ledger()` 的判据是"相对台账哈希变了"，那次提交也在里面 ⟹ 又 +1 ✗
    #   两个来源重叠，1 次改动被数成 2 次，然后触发"验收作废"的黄。
    #
    #   正确分工：
    #     · 已提交的 ⟹ 只由 `git log` 数
    #     · 还没提交的 ⟹ 只看**工作区**（`changed_files()`），那部分 `git log` 数不到
    dirty_paths = {p for p in changed_files()
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


# ────────────── 完工定义第 11 条：效果逻辑图的锚点 ──────────────
def check_doc_anchors() -> None:
    """判「效果逻辑图」还在不在描述当前代码。

    两件事，方向相反，缺一件都补不上这个洞：

      **正向** —— 图里每个 ``[锚: 文件::符号]`` 引用的符号必须还在。
      代码改名/删函数 ⟹ 锚点失效 ⟹ 红。这是"图说的东西代码里还有没有"。

      **逆向** —— 实现文件变了，但图没跟着变 ⟹ 黄。
      锚点全绿不代表图还对：符号还在、行为改了，图就是错的，而锚点看不出来。
      这条只提醒（黄），因为纯注释/排版的改动也会命中，不该阻断。

    ⚠ 锚点管不到的两件事（写在这里免得以后误以为它万能）：
      ① 注释里的内容（比如三档矩阵那张表是注释，没有符号可锚）；
      ② 语义是否还对（符号在、意思变了）。
      这两件只能靠会审看 diff —— 机器只能保证"引用的东西存在"。
    """
    print("\n=== 完工定义第 11 条：效果逻辑图 ===")
    try:
        sys.path.insert(0, str(ROOT))
        import doc_anchor_check as dac
    except Exception as exc:  # noqa: BLE001
        yellow(f"锚点检查器载入失败（{exc}），本条无法核对")
        return

    docs = sorted(ROOT.glob(dac.DOC_GLOB))
    if not docs:
        yellow(f"没有 {dac.DOC_GLOB} —— 按完工定义第 11 条这属于缺项")
        return

    total = 0
    bad = 0
    for doc in docs:
        text = doc.read_text(encoding="utf-8", errors="replace")
        hits = dac.ANCHOR_RE.findall(text)
        if not hits:
            red(f"{doc.name} 零锚点 —— 这份图没法核对（第 11 条判据③）")
            bad += 1
            continue
        seen: dict[tuple[str, str], list[int]] = {}
        for lineno, line in enumerate(text.split("\n"), start=1):
            for file, symbol in dac.ANCHOR_RE.findall(line):
                seen.setdefault((file, symbol), []).append(lineno)
        for (file, symbol), lines in sorted(seen.items()):
            total += 1
            reason = dac.check_anchor(file, symbol)
            if reason:
                where = "、".join(f"第 {n} 行" for n in lines)
                red(f"{doc.name} {where} [锚: {file}::{symbol}] 失效 —— {reason}")
                bad += 1
    if not bad:
        ok(f"{total} 个锚点全部有效（{len(docs)} 份图）")

    # 逆向：实现动了而图没动 ⟹ 复核提醒
    moved = changed_since_ledger() | changed_files()
    impl = sorted(p for p in moved if p in {"__init__.py", "settings_guard.py"})
    doc_moved = [p for p in moved if Path(p).name.startswith("效果逻辑图-")]
    if impl and not doc_moved:
        yellow(
            f"{impl} 变了，但效果逻辑图没跟着改 ⟹ 按第 11 条必须人工复核图是否仍准确"
            "（锚点只能证明符号还在，证明不了意思没变）"
        )


# ────────── 完工定义第 4 条②：此刻还活着吗 ──────────
def check_alive() -> None:
    """完工定义第 4 条②：插件**此刻**还活着吗。

    ## 为什么这条要放进闸，而不只放在 state.sh

    会审跑的是 `gate_extra.py` + `neko_review_gate.js`；`state.sh` 是给人看的状态板。
    判据只在状态板上 = **会审时可能漏**。而这条判据存在的理由就是"漏了会真假绿"。

    ## 为什么结论是**黄**不是红

    插件没在跑 ≠ 代码有问题。把它变红，会逼人为了过闸去起宿主 ——
    拿"起宿主"换"过闸"，本末倒置。但会审时**必须看得见**，所以不能沉默。

    ## 三种"不适用"要跳过，不能判红

    别的机器 clone 下来跑闸时没有宿主日志目录（`no_host`）。
    那种情况判红会让这条判据在 CI 上永远红，然后被人连真红一起关掉。
    """
    print("\n=== 完工定义第 4 条②：此刻还活着吗 ===")
    try:
        sys.path.insert(0, str(ROOT))
        import liveness_check as lc
    except Exception as exc:  # noqa: BLE001
        yellow(f"liveness_check 载入失败（{exc}），本条无法核对")
        return

    v = lc.evaluate()
    # `v.line()` 自带 `[√] ` / `[x] ` 前缀（状态板要 grep 它）。这里要套自己的
    # ok/yellow 标记，所以先剥掉 —— 不剥就会出现「[√] [√] 在跑」这种双符号。
    body = re.sub(r"^\[[√x—]\]\s*", "", v.line())
    if v.state == "no_host":
        print(f"  [—] {body}")
        return
    if v.state == "running":
        ok(body)
        return
    yellow(
        f"{body} ⟹ 不影响代码结论（插件没在跑 ≠ 代码有问题），"
        "但会审时必须知道：这段时间实际没人守"
    )


def _preflight() -> str | None:
    """跑之前的体检：这个目录到底是不是**初始化过**的插件仓库。

    ★ 为什么必须做（2026-10-26 在空目录上实测踩到，先从 skill/tools 的通用版修的）：

      没有这一步时，在空目录上跑，每条检查都会「缺文件 ⟹ 跳过」，
      最后打出「全部判定通过（6 条黄，不阻断）」——
      **6 条黄里面包着一句"通过"，那就是假绿。** 它比红更危险：
      红会让人停手，假绿会让人放心。

      工具必须**拒绝在没初始化的目录上给结论** —— 这是它的责任边界，
      不是"尽量不报错"。给不出有意义的结论时，说"给不出"，别说"通过"。

    返回 ``None`` = 可以跑；否则返回人话原因。
    """
    must = [
        (ROOT / "seam", "seam/（接缝基线）"),
        (ROOT / "contracts", "contracts/（契约）"),
        (ROOT / "seam" / "blocks.json", "seam/blocks.json（块清单，由 block_check.py 生成）"),
        # `.git` 是 2026-10-27 补的：判红 3（工作区干净）、判红 9（契约变更次数）、ledger 链，
        # 全靠 `git status` / `git log`。非 git 目录下它们**全部返回空**，
        # 于是「无变更 ⟹ 全绿」—— 又一个假绿入口。与 skill/tools 通用版同口径。
        (ROOT / ".git", ".git/（变更集与台账链都靠它，没有它判定是空的）"),
    ]
    missing = [desc for path, desc in must if not path.exists()]
    if missing:
        return f"{ROOT} 下缺 {'、'.join(missing)}"
    return None


def main() -> int:
    pre = _preflight()
    if pre:
        print("\n=== 前置体检 ===")
        red(
            f"{pre} ⟹ 这不像一个初始化过的插件仓库，**拒绝给结论**。"
            "先按 skill 的九步流程把 seam/ 与 contracts/ 建起来"
        )
        print("\n" + "-" * 56)
        print("[x] 有 1 条红：")
        print(f"   - {pre}")
        return 1

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
    if run_all or "--doc" in want:
        check_doc_anchors()
    if run_all or "--alive" in want:
        check_alive()

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
