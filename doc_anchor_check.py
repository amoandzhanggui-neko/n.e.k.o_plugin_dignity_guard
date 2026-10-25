#!/usr/bin/env python
"""效果逻辑图的锚点检查。

## 它解决的问题

完工定义第 11 条要求有一份「效果逻辑图」，用来让不读代码的人看懂插件实际会怎么做。
但**一张自己写的图，最容易变成"看起来做了"** —— 图和代码各自演化，
三个月后图里说的和代码跑的不是一回事，而没人会发现，因为没人会去核对一张文档。

这个脚本让"图与代码一致"变成**可机器核对**的：

  图里每条结论后面挂一个 ``[锚: 文件.py::符号]``，
  脚本检查那个符号**是不是真的还在**。代码改名/删掉 ⟹ 锚点失效 ⟹ 红。

## 为什么用符号名而不是行号

行号一改就漂（这份文档写完当天就漂过一次：``SENSITIVITY_RULES`` 从 316 行到了 302 行）。
符号名的寿命长得多；真要找行，``grep -n`` 一下就有。

## 两条判据

1. **锚点必须有效**：引用的文件存在、符号存在（支持 `Class.method`）。
2. **图不能没有锚点**：一份零锚点的逻辑图 = 一段散文，没法核对 ⟹ 红。

## 用法

    python doc_anchor_check.py            # 检查 docs/效果逻辑图-*.md
    python doc_anchor_check.py 某份.md     # 只查指定文档

退出码：0 = 全过；1 = 有失效锚点或零锚点。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent
DOC_GLOB = "docs/效果逻辑图-*.md"

#: ``[锚: settings_guard.py::GuardState.evaluate]``
ANCHOR_RE = re.compile(r"\[锚:\s*([^\s:\]]+\.py)::([A-Za-z_][A-Za-z0-9_.]*)\s*\]")

RED: list[str] = []
YELLOW: list[str] = []


def red(msg: str) -> None:
    print("[红] " + msg)
    RED.append(msg)


def yellow(msg: str) -> None:
    print("[黄] " + msg)
    YELLOW.append(msg)


def ok(msg: str) -> None:
    print("[√] " + msg)


def _module_symbols(tree: ast.Module) -> dict[str, ast.AST]:
    """模块顶层的「有名字的东西」：函数、类、赋值（含带注解的）。"""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
    return out


def _class_members(node: ast.ClassDef) -> dict[str, ast.AST]:
    """类体里的成员：方法、嵌套类、类属性。"""
    out: dict[str, ast.AST] = {}
    for sub in node.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[sub.name] = sub
        elif isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = sub
        elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            out[sub.target.id] = sub
    return out


def check_anchor(file: str, symbol: str) -> str:
    """返回空字符串 = 有效；否则返回人话原因。"""
    path = ROOT / file
    if not path.is_file():
        return f"文件不存在：{file}"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return f"{file} 语法读不通（{exc}）—— 锚点无法核对"

    parts = symbol.split(".")
    modules = _module_symbols(tree)
    if parts[0] not in modules:
        return f"{file} 里没有顶层符号「{parts[0]}」"
    node = modules[parts[0]]
    for depth, name in enumerate(parts[1:], start=2):
        if not isinstance(node, ast.ClassDef):
            return f"{file}::{'.'.join(parts[: depth - 1])} 不是类，取不到「{name}」"
        members = _class_members(node)
        if name not in members:
            return f"{file}::{'.'.join(parts[: depth - 1])} 里没有「{name}」"
        node = members[name]
    return ""


def scan_doc(doc: Path) -> tuple[int, int]:
    """返回 (锚点总数, 失效数)。"""
    text = doc.read_text(encoding="utf-8", errors="replace")
    hits = ANCHOR_RE.findall(text)
    try:
        rel = doc.relative_to(ROOT).as_posix()
    except ValueError:
        # 指定了仓库外的文档（自查/负向测试时会这样）。
        # 不用 try 就会 ValueError 崩掉 —— 而"崩掉"比"报错"更糟：
        # 崩掉的输出看起来像跑了但没结果，容易被当成"没发现问题"。
        rel = str(doc)
    print(f"\n--- {rel} ---")
    if not hits:
        red(f"{rel} 一个锚点都没有 —— 这份图等于一段散文，没法核对（第 11 条判据③）")
        return 0, 1
    bad = 0
    seen: dict[tuple[str, str], list[int]] = {}
    for file, symbol in hits:
        seen.setdefault((file, symbol), [])
    for lineno, line in enumerate(text.split("\n"), start=1):
        for file, symbol in ANCHOR_RE.findall(line):
            seen[(file, symbol)].append(lineno)
    for (file, symbol), lines in sorted(seen.items()):
        reason = check_anchor(file, symbol)
        where = "、".join(f"第 {n} 行" for n in lines)
        if reason:
            red(f"{where} [锚: {file}::{symbol}] 失效 —— {reason}")
            bad += 1
        else:
            print(f"  [√] {file}::{symbol}（{where}）")
    return len(seen), bad


def main() -> int:
    print("=== 效果逻辑图锚点检查 ===")
    if len(sys.argv) > 1:
        docs = [Path(a) if Path(a).is_absolute() else ROOT / a for a in sys.argv[1:]]
    else:
        docs = sorted(ROOT.glob(DOC_GLOB))

    if not docs:
        # 不在这里判红：有没有图是**完工定义第 11 条**管的事，会审判三分类。
        # 这个脚本只负责"有图的时候，图与代码有没有脱钩"。
        yellow(f"没有找到 {DOC_GLOB} —— 按完工定义第 11 条这属于缺项，会审时应判未完成")
        print("\n" + "-" * 56)
        print("[√] 没有图可查（不阻断）")
        return 0

    total = 0
    bad = 0
    for doc in docs:
        if not doc.is_file():
            red(f"文档不存在：{doc}")
            bad += 1
            continue
        n, b = scan_doc(doc)
        total += n
        bad += b

    print("\n" + "-" * 56)
    if bad:
        print(f"[x] {bad} 个锚点不可用（共 {total} 个）⟹ 图与代码已脱钩，按第 11 条判未完成")
        return 1
    print(f"[√] {total} 个锚点全部有效 —— 图里引用的符号都还在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
