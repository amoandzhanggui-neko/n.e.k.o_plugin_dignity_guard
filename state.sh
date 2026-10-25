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
#   bash state.sh            六节全出（约 10 秒，含装配检查 8 秒）+ 上一次测试结果
#   bash state.sh --test     额外真跑全量测试（约 4 分钟）后重写
#
# 六节：1 代码 / 2 源仓库↔宿主 / 3 测试 / 4 装配与接缝 / 5 运行时 / 6 下一步
#
# 落地：2026-10-25　依据：今天实测出来的真正跑法（见第 2 节命令）
# ============================================================================
set -u

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_ROOT="C:/Users/Administrator/.workbuddy/neko_test/N.E.K.O"
HOST_PLUGIN="$HOST_ROOT/plugin/plugins/dignity_guard"
OUT="$SRC/STATE.md"
# 缓存必须放仓库**外面**。
# 原因（2026-10-25 实测）：neko_review_gate.js 的受控集是 default-deny + readdirSync 递归全收，
# **它不认 .gitignore** —— 除文档白名单外，所有文件都算受控。
# 所以缓存只要留在仓库里，每跑一次 state.sh 它的哈希就变 ⟹ 闸必红，而且红得莫名其妙。
# 正确的解法不是去改闸（那要会审+stamp），而是让受控集里压根没有这个缓存。
CACHE_DIR="${HOME:-${USERPROFILE:-/c/Users/Administrator}}/.cache/neko-dignity-guard"
mkdir -p "$CACHE_DIR" 2>/dev/null || true
CACHE="$CACHE_DIR/last_test.txt"

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

# ---- 3.5 运行时证据：插件在宿主里到底跑没跑 ----
# 这一节踩过两次坑，都记在这儿，免得下次又错：
#   坑① 我第一次查的是 AppData/Roaming/N.E.K.O/neko-electron-debug.log，今天 6553 条里 0 条 dignity，
#        差点判定"插件没跑"。那是 **Electron 前端**日志，不是 Python 插件的日志。
#   坑② 插件日志的真实位置是 AppData/Local/N.E.K.O/logs/plugin/，
#        按插件名+日期分文件：N.E.K.O_Plugin_dignity_guard_YYYYMMDD.log。
#        依据：宿主 plugin/logging_config.py 明确写"日志自动落到 N.E.K.O/logs/"。
#   ⟹ 判据：看 data 目录有没有当天写入 + 插件自己那份日志最后一条是什么时候。
RUNTIME_DATA="C:/Users/Administrator/AppData/Local/N.E.K.O/plugins/dignity_guard/data"
PLUGLOG_DIR="C:/Users/Administrator/AppData/Local/N.E.K.O/logs/plugin"
ERRLOG="C:/Users/Administrator/AppData/Local/N.E.K.O/logs/N.E.K.O_Plugin_error.log"
if [ -d "$RUNTIME_DATA" ]; then
  latest_bk="$(ls -1 "$RUNTIME_DATA/memory_backups" 2>/dev/null | sort | tail -1)"
  [ -z "$latest_bk" ] && latest_bk="（无备份目录）"
  db_mtime="$(date -r "$RUNTIME_DATA/store.db" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '（无 store.db）')"
else
  latest_bk="（data 目录不存在 ⟹ 插件从未在此宿主写过东西）"
  db_mtime="—"
fi
# 插件日志：今天的、昨天的都看一眼（跨零点时"今天没日志"不代表没跑）
PLLOG="$PLUGLOG_DIR/N.E.K.O_Plugin_dignity_guard_$(date '+%Y%m%d').log"
[ -f "$PLLOG" ] || PLLOG="$(ls -1t "$PLUGLOG_DIR"/N.E.K.O_Plugin_dignity_guard_2*.log 2>/dev/null | head -1)"
if [ -n "$PLLOG" ] && [ -f "$PLLOG" ]; then
  pl_n="$(wc -l < "$PLLOG" 2>/dev/null | tr -d ' ')"
  pl_last="$(tail -1 "$PLLOG" 2>/dev/null | cut -c1-150)"
  pl_when="$(date -r "$PLLOG" '+%Y-%m-%d %H:%M' 2>/dev/null)"
else
  pl_n="0"; pl_last="（找不到插件日志）"; pl_when="—"
fi
err_dg="$(grep -ci 'dignity' "$ERRLOG" 2>/dev/null || true)"; err_dg="${err_dg:-0}"

# ---- 3.6 装配 + 接缝（结构验证，每次现跑）----
# 为什么每次都跑而不是读缓存：状态板的价值就是"现在到底是怎样"。
# 装配约 8 秒（含宿主 import），换来的是永远实时的结构结论 —— 这个代价值得。
asm_res="未检查（找不到可用 python）"
seam_res="未检查（找不到可用 python）"
gate_res="未检查（找不到可用 python）"
doc_res="未检查（找不到可用 python）"
if [ -n "$PY" ]; then
  a="$(cd "$SRC" && node assembly_check.js 2>/dev/null | grep -E '装配检查(通过|失败)' | tail -1)"
  asm_res="${a:-装配检查无输出（可能是 node 不可用或超时）}"
  s="$(cd "$SRC" && "$PY" seam_snapshot.py --check 2>/dev/null | grep -E '接缝接口' | tail -1)"
  seam_res="${s:-接缝检查无输出}"
  # ★ 2026-10-25 修一个"显示与实际不一致"：这里原来 grep 的是「三条判定」，
  #   而 gate_extra 的汇总文案早改成「全部判定通过」⟹ 全绿的时候这一格显示
  #   "三条判定无输出"，看起来像没跑。文案改了而匹配没改，不会报错，只会静静地说谎。
  #
  #   另外：红的时候只显示「[x] 有 1 条红：」等于没说 —— 看板要的是一眼看到哪条红，
  #   所以把第一条红也带上。只跑一次 gate_extra 再从中取两段。
  #
  #   ⚠ 不截断。原先写 `cut -c1-60`，在 C locale 下是**按字节**切的，会把中文砍成半个
  #   UTF-8 序列 ⟹ 生成出来的 STATE.md 里有非法字节，grep 直接把它当二进制文件
  #   （"Binary file STATE.md matches"），整个状态板就废了。红线完整显示，长就长。
  ge="$(cd "$SRC" && "$PY" gate_extra.py 2>/dev/null)"
  g="$(printf '%s\n' "$ge" | grep -E '^\[x\]|^\[√\] 全部判定通过' | tail -1)"
  first_red="$(printf '%s\n' "$ge" | grep -E '^   - ' | head -1 | sed 's/^   - //')"
  if [ -n "$first_red" ]; then
    gate_res="$g $first_red…"
  else
    gate_res="${g:-判定无输出}"
  fi
  # 效果逻辑图锚点（完工定义第 11 条）—— 放状态板里，图脱钩才看得见
  d="$(cd "$SRC" && "$PY" doc_anchor_check.py 2>/dev/null | tail -2 | grep -E '个锚点全部有效|个锚点不可用|没有图可查' | tail -1)"
  doc_res="${d:-锚点检查无输出}"
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

## 4. 装配与接缝（结构接得上吗）

| 项 | 结果 |
|---|---|
| 装配检查（导入 / 注册 / 对账） | $asm_res |
| 接缝快照（契约接口有没有变） | $seam_res |
| 全部判定（动态 key / 接缝私约 / AST / 三键 / 契约改动次数 / 三零同现） | $gate_res |
| 效果逻辑图锚点（完工定义第 11 条） | $doc_res |

判据：**装配通过 + 接缝一致**，才算结构没问题。

- 装配检查验的是「代码结构接得上、源码声明的入口与宿主实际注册的一致」。
  **它不含真启动**（真启动要宿主注入 ctx，脱离宿主造不出来）—— 真启动看第 5 节。
- 接缝快照验的是「契约声明的 out 字段有没有被悄悄改」，对应判红 2（允许态 / when）和判红 5（seam_id 必须派生）。

## 5. 运行时（插件在宿主里跑没跑）

| 证据 | 值 |
|---|---|
| 最新记忆备份 | $latest_bk |
| data/store.db 修改时间 | $db_mtime |
| 插件日志行数 / 文件时间 | $pl_n / $pl_when |
| error.log 里 dignity 相关 | $err_dg 条 |

**插件日志最后一条：**

    $pl_last

判据：**插件日志有今天的记录 ⟹ 启动过；data 有当天写入 ⟹ 启动后一直在干活。**
\`error.log\` 里 dignity 相关 > 0 就必须去看。

> 坑（2026-10-25 踩了两次，都记下来）：
> \`AppData/Roaming/N.E.K.O/neko-electron-debug.log\` 是 **Electron 前端**日志，
> 里面永远查不到 dignity（今天 6553 条里 0 条）。拿它判断插件会得出完全相反的结论。
> Python 插件日志在 \`AppData/Local/N.E.K.O/logs/plugin/<插件名>_<日期>.log\`，
> 依据是宿主 \`plugin/logging_config.py\` 自己写的"日志自动落到 N.E.K.O/logs/"。
> 另外：插件自身**不配日志**（\`plugin.toml\` 无 log 项、\`__init__.py\` 无 \`import logging\`），
> 级别由宿主环境变量 \`NEKO_LOG_LEVEL\` 统一管（TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL），
> 且仓库禁止引入 loguru（CI 有 \`scripts/check_no_loguru.py\` 守门）。

## 6. 下一步

一条就好，必须带判据。当前判据：**0 failed、0 error**。
EOF

echo "✓ 已生成 $OUT"
sed -n '1,40p' "$OUT"
