#!/usr/bin/env bash
# ============================================================================
# state.sh —— 插件状态板生成器
#
# 它只做一件事：把"现在到底能不能跑"变成一行行可核对的事实。
#
# ★ 唯一铁律：跑不出来就写「跑不了」，不许写历史数字、不许留空。
#   为什么：写历史数字（如"155 passed"）会让人以为"现在还是好的"，
#   比"没测"危险得多。这个插件自己的 pytest.ini 注释里就写着同一句话：
#   "一个'看起来在跑、其实没跑'的测试套件，比一个显眼报错的套件危险得多。"
#
# 用法：
#   bash state.sh            静态状态（快，几秒）+ 上一次测试结果
#   bash state.sh --test     真跑全量测试（约 4 分钟）后重写
#
# 落地：2026-10-25　依据：今天实测出来的真正跑法（见第 2 节命令）
# ============================================================================
set -u

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_ROOT="C:/Users/Administrator/.workbuddy/neko_test/N.E.K.O"
HOST_PLUGIN="$HOST_ROOT/plugin/plugins/dignity_guard"
OUT="$SRC/STATE.md"
CACHE="$SRC/.state_last_test"

RUN_TEST=0
[ "${1:-}" = "--test" ] && RUN_TEST=1

# ---- 选 python：必须跳过那个 SSL 坏掉的 3.13.12.old.240 ----
# 2026-10-25 实测：PATH 第一条 python 是 3.13.12.old.240，_ssl DLL 缺失，
# 于是 `python -m pytest` 永远是 "No module named pytest"，看起来像"没装"，
# 其实是根本没走到能用的解释器。这里靠"能不能 import ssl 且 import pytest"来判。
pick_python() {
  local cand
  for cand in \
    "C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe" \
    "$(command -v python 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"
  do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if "$cand" -c "import ssl, pytest" >/dev/null 2>&1; then echo "$cand"; return 0; fi
  done
  return 1
}

PY="$(pick_python || true)"

now="$(date '+%Y-%m-%d %H:%M')"

# ---- 1. 代码状态 ----
g() { git -C "$SRC" "$@" 2>/dev/null; }
commit="$(g log -1 --format='%h %ci %s' || echo '拿不到（不是 git 仓库？）')"
dirty_n="$(g status --porcelain | wc -l | tr -d ' ')"
# git diff --stat 只统计**已跟踪**文件的改动，不含新文件。
# 只报一个总数会让人以为"5 个文件改了 59 行"，其实里面 4 个是新建的 —— 这种含糊正是要避免的。
tracked_n="$(g diff --name-only | wc -l | tr -d ' ')"
untracked_n="$(g ls-files --others --exclude-standard | wc -l | tr -d ' ')"
diffstat="$(g diff --stat | tail -1 || true)"
[ -z "$diffstat" ] && diffstat="无"
dirty_line="$dirty_n 个　（已跟踪修改 $tracked_n 个：$diffstat ｜ 未跟踪新文件 $untracked_n 个）"
ahead="$(g log origin/main..HEAD --oneline 2>/dev/null | wc -l | tr -d ' ')"

# ---- 2. 源仓库 ↔ 宿主副本 是否同一份 ----
# 为什么必须有这一项：测试跑的是**宿主那份**代码（pytest 必须在 N.E.K.O 里跑）。
# 如果源仓库改了没同步到宿主，测试结果就不代表源仓库 —— 那是最容易骗过自己的一步。
if [ -d "$HOST_PLUGIN" ]; then
  same="一致"; diff_files=""
  for f in __init__.py settings_guard.py main_server_client.py memory_backup.py memory_guard.py feedback.py diagnostics.py; do
    [ -f "$SRC/$f" ] || continue
    a="$(sha256sum "$SRC/$f" 2>/dev/null | cut -c1-12)"
    b="$(sha256sum "$HOST_PLUGIN/$f" 2>/dev/null | cut -c1-12)"
    if [ "$a" != "$b" ]; then same="★不一致"; diff_files="$diff_files $f(源$a/宿主$b)"; fi
  done
  host_line="$HOST_PLUGIN　⟹ 与源仓库 $same${diff_files:+：$diff_files}"
else
  host_line="**宿主副本不存在**（$HOST_PLUGIN）⟹ 测试无法运行"
  same="★不一致"
fi

# ---- 3. 测试 ----
TEST_CMD='cd N.E.K.O && python -m pytest plugin/plugins/dignity_guard/tests -q'
test_result="跑不了（还没跑过）"
test_when="从未"
if [ "$RUN_TEST" = "1" ]; then
  if [ -z "$PY" ]; then
    test_result="跑不了（找不到同时具备 ssl 和 pytest 的 python）"
  elif [ "$same" != "一致" ]; then
    # 仍然是跑的，但结果要打上标记：它测的不是源仓库
    test_result="（注意：源仓库与宿主不一致，下面结果测的是宿主那份）"
    raw="$(cd "$HOST_ROOT" && "$PY" -m pytest plugin/plugins/dignity_guard/tests -q 2>&1 | tail -1)"
    test_result="$test_result　$raw"
  else
    raw="$(cd "$HOST_ROOT" && "$PY" -m pytest plugin/plugins/dignity_guard/tests -q 2>&1 | tail -1)"
    test_result="${raw:-跑不了（没有任何输出，可能是解释器/环境问题）}"
  fi
  test_when="$now"
  printf '%s\t%s\n' "$test_when" "$test_result" > "$CACHE" 2>/dev/null || true
elif [ -f "$CACHE" ]; then
  test_when="$(cut -f1 "$CACHE" 2>/dev/null)"
  test_result="$(cut -f2- "$CACHE" 2>/dev/null)　←（缓存，非本次实跑）"
fi

# 缓存超过 7 天就明说它过期，别让旧数字冒充现状
if [ -f "$CACHE" ] && [ -n "${test_when:-}" ]; then
  if [ "$test_when" != "从未" ]; then
    age_d=$(( ( $(date +%s) - $(date -d "$test_when" +%s 2>/dev/null || echo 0) ) / 86400 ))
    [ "$age_d" -gt 7 ] && test_result="$test_result　★已过期 ${age_d} 天，跑 bash state.sh --test 重测"
  fi
fi

# ---- 4. 写盘 ----
cat > "$OUT" <<EOF
# STATE —— 现在到底能不能跑

> 本文件由 \`state.sh\` 生成（$now），**禁止手改**。
> 手写的文档会和代码漂移；命令生成的不一样，它只能反映真实。
> 唯一铁律：跑不出来就写「跑不了」，不写历史数字。

## 1. 代码

| 项 | 值 |
|---|---|
| 最新提交 | $commit |
| 未提交 | $dirty_line |
| 未推送 | $ahead 个提交 |

## 2. 源仓库 ↔ 宿主副本

$host_line

## 3. 测试

\`\`\`
$TEST_CMD
\`\`\`

**结果：$test_result**

测于：$test_when

> 为什么必须在 N.E.K.O 里跑：插件根目录的 \`__init__.py\` 就是包入口，
> 直接在插件仓库里跑 pytest 会把它当成测试模块收集，直接
> \`ImportError: attempted relative import with no known parent package\`，
> 155 个用例全部 error。这是 2026-10-25 实测出来的，不是猜的。

## 4. 下一步

一条就好，必须带判据。当前判据：**0 failed、0 error**。
EOF

echo "✓ 已生成 $OUT"
sed -n '1,40p' "$OUT"
