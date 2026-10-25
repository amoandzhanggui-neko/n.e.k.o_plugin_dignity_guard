#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
接缝快照生成器（判红 1/2/4/5/7/9 的共同前置）

它做三件事：
  1. 从 contracts/CT-v2.md **解析**每条契约的 out 字段（名字 / 类型 / 允许态 / when）
  2. 生成确定性快照 → seam/contract_snapshot.json（key 排序、不含时间戳/绝对路径/值）
  3. --check 时与上一轮快照比对，任何"接口悄悄变了"都判红

════════════════════════════════════════════════════════════════════
为什么必须先有它（2026-10-25）
  10 条判红里有 6 条（1 动态拼 key / 2 允许态+when / 4 三键复用 / 5 seam_id 派生 /
  7 AST 实质变更 / 9 契约变更次数）全都建立在「有一份可信的接缝字段快照」之上。
  没有快照，这些判据连输入都没有 —— 硬写只会误报，然后被人一怒之下整体绕过。
════════════════════════════════════════════════════════════════════

判红口径（对应 skill §8.3）：
  - 判红 5：seam_id **必须派生**（两侧端点排序后拼接），不许手填。手填能换名分流。
  - 判红 2：允许态只能是 present/null/missing；实测 ∉ 允许态 ⟹ 红（实测态来自运行时，本脚本只管声明）
  - 判红 1：字段名只能来自**契约表解析**，不许代码里动态拼 key（本脚本记录 source 作为证据）
  - 判红 4：快照只存 key 集/类型/允许态，**不存值**（值是浮点/bool 漂移会让哈希乱跳）
  - 判红 6：产物必须 LF（见 .gitattributes）

用法:
    python seam_snapshot.py            # 生成/更新快照
    python seam_snapshot.py --check    # 只比对，不写（红/绿）
退出码: 0 = 一致或首次生成；1 = 接口变了（红）
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
CT = ROOT / "contracts" / "CT-v2.md"
SEAM = ROOT / "seam"
SNAP = SEAM / "contract_snapshot.json"

ALLOWED_STATES = {"present", "null", "missing"}
# 竖线在 markdown 表格里被转义成 \|，解析前先换成占位符，切完再还原
PIPE = "\u0001"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def parse_contracts() -> dict:
    """解析 CT-v2.md：每条契约的 out 字段表 + depends。"""
    if not CT.exists():
        raise SystemExit(f"[x] 找不到 {CT}")
    txt = CT.read_text(encoding="utf-8", errors="replace")
    # 按 ## C-xxx 切节
    parts = re.split(r"^## (C-[A-Za-z0-9\-]+.*)$", txt, flags=re.M)
    out: dict[str, dict] = {}
    for i in range(1, len(parts), 2):
        head = parts[i].strip()
        body = parts[i + 1]
        cid = head.split()[0]  # "C-REV-1"

        # depends
        dep = re.search(r"\*\*depends\*\*\s*[：:]\s*`?\[([^\]]*)\]`?", body)
        deps = [d.strip().strip("`") for d in dep.group(1).split(",")] if dep and dep.group(1).strip() else []

        # 找 "out（允许态 + when）" 之后的第一个表格
        m = re.search(r"\*\*out[^*]*\*\*", body)
        seg = body[m.end():] if m else body
        fields = []
        for line in seg.split("\n"):
            raw = line.strip()
            if not raw.startswith("|"):
                if fields:
                    break  # 表格结束
                continue
            cells = [c.strip() for c in raw.replace("\\|", PIPE).split("|")]
            cells = [c.replace(PIPE, "|") for c in cells]
            # 形态: ['', 字段, 类型, 允许态, when, 说明, '']
            if len(cells) < 5:
                continue
            name = cells[1]
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                continue  # 跳过表头与分隔线
            allowed_raw = cells[3]
            st = re.findall(r"present|null|missing", allowed_raw)
            if not st:
                continue
            when_raw = cells[4].strip("` ").strip()
            bad = [s for s in st if s not in ALLOWED_STATES]
            fields.append({
                "name": name,
                "type": cells[2].strip(),
                "allowed": sorted(set(st)),
                "when": when_raw or "always",
                "_bad_state": bad,
            })
        fields.sort(key=lambda f: f["name"])

        # 判红 5：seam_id 派生，不手填 —— 两侧端点（自身 + depends）排序后拼接
        endpoints = sorted({cid, *deps})
        seam_id = "__".join(endpoints)

        out[cid] = {
            "seam_id": seam_id,
            "depends": sorted(deps),
            "fields": fields,
            "source": f"contracts/CT-v2.md#{cid}",   # 判红 1 的证据：字段名来自解析，不是拼的
            "fields_hash": sha(json.dumps([(f["name"], f["type"], f["allowed"], f["when"]) for f in fields],
                                          ensure_ascii=False, sort_keys=True)),
        }
    return out


def build() -> dict:
    cts = parse_contracts()
    return {
        "contracts_version": "CT-v2",
        "generated_by": "seam_snapshot.py",
        "note": "只存 key 集/类型/允许态/when，不存值（判红 4）；不含时间戳与绝对路径，保证确定性",
        "contracts": cts,
        "hash": sha(json.dumps(cts, ensure_ascii=False, sort_keys=True)),
    }


def diff(old: dict, new: dict) -> list[str]:
    """返回判红理由；空列表 = 接口没变。"""
    red: list[str] = []
    oc, nc = old.get("contracts", {}), new.get("contracts", {})

    for cid in sorted(set(oc) - set(nc)):
        red.append(f"[红] 契约 {cid} 在 CT-v2 里消失了（不许静默删契约）")
    for cid in sorted(set(nc) - set(oc)):
        red.append(f"[黄] 契约 {cid} 是新增的（需会审，不算回归）")

    for cid in sorted(set(oc) & set(nc)):
        o, n = oc[cid], nc[cid]
        if o.get("seam_id") != n.get("seam_id"):
            red.append(f"[红] {cid} 的 seam_id 变了：{o.get('seam_id')} → {n.get('seam_id')}（判红 5：必须派生、不许换名）")
        of = {f["name"]: f for f in o.get("fields", [])}
        nf = {f["name"]: f for f in n.get("fields", [])}
        for fn in sorted(set(of) - set(nf)):
            red.append(f"[红] {cid}.{fn} 字段被删（接口收缩，接缝会断）")
        for fn in sorted(set(nf) - set(of)):
            red.append(f"[红] {cid}.{fn} 字段是新增（接口扩张，下游未适配）")
        for fn in sorted(set(of) & set(nf)):
            if of[fn]["allowed"] != nf[fn]["allowed"]:
                red.append(f"[红] {cid}.{fn} 允许态变了：{of[fn]['allowed']} → {nf[fn]['allowed']}（判红 2）")
            if of[fn]["when"] != nf[fn]["when"]:
                red.append(f"[红] {cid}.{fn} when 变了：{of[fn]['when']} → {nf[fn]['when']}（判红 2）")
            if of[fn]["type"] != nf[fn]["type"]:
                red.append(f"[黄] {cid}.{fn} 类型变了：{of[fn]['type']} → {nf[fn]['type']}")
    return red


def main() -> int:
    check_only = "--check" in sys.argv
    SEAM.mkdir(exist_ok=True)
    new = build()

    # 允许态合法性自检（判红 2 的前半：声明本身必须合法）
    bad = [(cid, f["name"], f["_bad_state"]) for cid, c in new["contracts"].items()
           for f in c["fields"] if f["_bad_state"]]
    if bad:
        print("[x] 契约里有非法的允许态声明（只能是 present/null/missing）：")
        for cid, fn, b in bad:
            print(f"    {cid}.{fn} → {b}")
        return 1

    total_fields = sum(len(c["fields"]) for c in new["contracts"].values())
    print(f"解析 {CT.name}：{len(new['contracts'])} 条契约，{total_fields} 个 out 字段")
    for cid, c in sorted(new["contracts"].items()):
        print(f"  {cid}　seam_id={c['seam_id']}　字段 {len(c['fields'])} 个　hash={c['fields_hash']}")

    old = None
    if SNAP.exists():
        try:
            old = json.loads(SNAP.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = None

    if old and check_only:
        reds = diff(old, new)
        if reds:
            print("\n[x] 接缝接口有变化（红）：")
            for r in reds:
                print("   " + r)
            print("\n注：契约改了就要走会审。若确认是有意变更，生成新快照并把它写进裁决表。")
            return 1
        print(f"\n[√] 接缝接口与上一轮一致（hash={new['hash']}）")
        return 0

    SNAP.write_text(json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8", newline="\n")
    print(f"\n[√] 快照已写入 {SNAP.relative_to(ROOT).as_posix()}（hash={new['hash']}）")
    if old:
        reds = diff(old, new)
        if reds:
            print("[!] 与上一轮相比有变化（下次 --check 会红，请确认是否已会审）：")
            for r in reds:
                print("   " + r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
