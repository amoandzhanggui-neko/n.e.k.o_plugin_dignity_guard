#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
块清单校验（工序 5 的机器闸）

它做两件事：
  1. 校验 docs/块清单-*.md 里的块表合法：块数 ≤6 / 受控文件存在且不重叠 / 热点必须唯一 writer
  2. 导出机器可读的 seam/blocks.json —— 判红 4（三键复用）和判红 7（两侧 AST）都靠它拿"块与依赖"

为什么要机器验（2026-10-25）：
  纸面的块清单会随时间烂掉：块加了忘了删、两个块抢同一个文件、文件被改名。
  而这三种烂法正好会让判红 2（接缝私约）误报——改一个文件同时碰两个块。
  所以块清单必须是"跑一次就知道还对不对"的东西。

判红口径（对应 skill）：
  §4-R1  块数 > 6 ⟹ 红
  §4-R2  接缝改动文件 ∩ 任一块受控集 ≠ ∅ ⟹ 红（本脚本提供受控集，判定在闸侧）
  另加：受控文件重叠 ⟹ 红（两个 owner 抢一个文件 = 热点冲突）

用法:
    python block_check.py           # 校验 + 导出 blocks.json
    python block_check.py --check   # 只校验，不写
退出码: 0 = 合法；1 = 有红
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
SEAM = ROOT / "seam"
OUT = SEAM / "blocks.json"
MAX_BLOCKS = 6
RED: list[str] = []


def red(msg: str) -> None:
    print("[x] " + msg)
    RED.append(msg)


def ok(msg: str) -> None:
    print("[√] " + msg)


def find_doc() -> Path:
    cands = sorted((ROOT / "docs").glob("块清单-*.md"), reverse=True)
    if not cands:
        raise SystemExit("[x] docs/ 下找不到 块清单-*.md")
    return cands[0]


def parse(doc: Path) -> dict:
    txt = doc.read_text(encoding="utf-8", errors="replace")
    # 找"## 块表"之后的第一张表
    m = re.search(r"^## 块表.*$", txt, re.M)
    seg = txt[m.end():] if m else txt
    blocks: dict[str, dict] = {}
    for line in seg.split("\n"):
        raw = line.strip()
        if not raw.startswith("|"):
            if blocks:
                break
            continue
        cells = [c.strip() for c in raw.split("|")]
        if len(cells) < 7:
            continue
        bid = cells[1]
        if not re.fullmatch(r"B[0-9]+-[a-z0-9\-]+", bid):
            continue  # 跳过表头/分隔线
        files = [f.strip() for f in cells[3].replace("，", ",").split(",") if f.strip()]
        cts = [c.strip() for c in cells[4].replace("，", ",").split(",") if c.strip() and c.strip() != "—"]
        deps = [d.strip() for d in cells[5].replace("，", ",").split(",") if d.strip() and d.strip() != "—"]
        blocks[bid] = {
            "name": cells[2],
            "files": files,
            "contracts": cts,
            "depends": deps,
            "hot": ("是" in cells[6]),
        }
    return blocks


def main() -> int:
    doc = find_doc()
    blocks = parse(doc)
    print(f"解析 {doc.name}：{len(blocks)} 个块")

    if not blocks:
        red("一个块都没解析出来（表格格式坏了？）")
        return 1

    # R1：块数 ≤6
    if len(blocks) > MAX_BLOCKS:
        red(f"块数 {len(blocks)} > {MAX_BLOCKS}（§4-R1）—— 说明范围没收住")
    else:
        ok(f"块数 {len(blocks)} ≤ {MAX_BLOCKS}")

    # 受控文件必须存在
    for bid, b in sorted(blocks.items()):
        for f in b["files"]:
            if not (ROOT / f).exists():
                red(f"{bid} 的受控文件不存在：{f}（改名了？块清单没跟上）")

    # 受控文件不许重叠（两个 owner 抢一个文件 = 热点冲突）
    owner: dict[str, str] = {}
    for bid, b in sorted(blocks.items()):
        for f in b["files"]:
            if f in owner:
                red(f"受控文件重叠：{f} 同时属于 {owner[f]} 和 {bid}（接缝私约判红会误报）")
            owner[f] = bid
    if not any("重叠" in r for r in RED):
        ok(f"受控文件无重叠（{len(owner)} 个文件，各归一块）")

    # 热点块必须且只能 1 个 writer；清单里所有块都应是 1 writer（skill §2 热点单 writer）
    hots = [bid for bid, b in blocks.items() if b["hot"]]
    if not hots:
        print("[!] 没有标任何热点块 —— 若有入口/注册/生命周期类文件，应该标出来")
    else:
        ok(f"热点块 {len(hots)} 个（恒 1 writer）：{hots}")

    # 依赖必须指向存在的块
    for bid, b in sorted(blocks.items()):
        for d in b["depends"]:
            if d not in blocks:
                red(f"{bid} 依赖的块不存在：{d}")

    SEAM.mkdir(exist_ok=True)
    payload = {
        "source": doc.name,
        "max_blocks": MAX_BLOCKS,
        "blocks": blocks,
        "file_owner": owner,
        "hash": hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
    }
    if "--check" in sys.argv:
        print("\n（--check：不写 blocks.json）")
    else:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8", newline="\n")
        print(f"\n[√] 已导出 {OUT.relative_to(ROOT).as_posix()}（hash={payload['hash']}）")

    if RED:
        print(f"\n[x] 块清单不合法（{len(RED)} 项）")
        return 1
    print("\n[√] 块清单合法")
    return 0


if __name__ == "__main__":
    sys.exit(main())
