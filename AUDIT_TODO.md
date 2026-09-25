# 三方审查 · 待办清单

> 来源：`subagents/agent-2cae5804`(主审)、`agent-14a3e8db`(二审)、`agent-dcd33ff5`(红队)
> 教训：**这份清单存在的意义是让每一条都有明确状态** ——
> 否则它们会默认「已被修复」，从而永远不被处理。
> 共 **65 条**（主审 + 二审 + 红队，含重复报告同一问题的条目）。

## 进度总览（2026-09-25 23:45）

处理分五批，全部已提交。**逐条的 `[x]` 由最终复检轮填** —— 上面只是"按批"的记录，
而清单存在的意义恰恰是"别以为修了"，所以逐条状态必须由**一次独立核对**（拿当前代码
重扫）来落，**不能由改动者自己标**。

| 批次 | commit | 覆盖 |
|---|---|---|
| 后端 26 项 | `533b8ac` | 「真实会伤到用户」的那批：`keep=0` 会**删光所有备份** / `restore path=null` 抛异常 / 五个入口**丢掉落盘结果**仍回 Ok / 关守卫连带停掉记忆备份 / 换 `memory_root` 不清快照 / `pending_disable` 不落盘 / JSON null 空跑 / 退休顺序 / 过期授权不看 tier / 裁剪 / `digest` 先读后判 / locale 只读一次 |
| 前端 6 条 | `1fc688e` | `persisted` 警告 / `copyFailed` 无反应 / `openFeedbackPage` 用返回值判成败（noopener 恒 null → 假报错） / `revert_outcomes` 从不渲染 / 两阶段关闭走不完 |
| 预留 API + 加固 | `787873e` | `with_digest` / `MemoryFile` 序列化 / `fetch_snapshot` / `proactive` 规则**明确标注**；`set_pinned` 加固（原先能凭空造出空备份） |
| 注释收口 | `f3aed5a` | CF-7 默认端点非空是**有意**的 / 红队5 的安静边界 / S2 上游行号免责 |
| ★ 新发现 | `d58bd4d` | **`set_guard_level("high")` 抛 `NameError`**（`TIER_HIGH` 用了却没 import，`__init__.py:1697`）—— 由"给重启用例补档位断言"撞出来的真崩溃，面向用户的入口整条挂掉 |

**同类扫描**：用 AST 扫了全库 7 个模块的"全大写名字是否都有来源"，**没有第二个** `TIER_HIGH` 式的漏网。

## 状态说明

- `[ ]` 未处理
- `[x]` 已修（并注明提交）
- `[-]` 判定为「设计如此」或「不必改」（注明理由）


## P0

| 状态 | 位置 | 符号 | 问题 | 建议修法 |
|---|---|---|---|---|
| [x] | `__init__.py:340` | `on_startup` | on_startup 构造 SettingsWatcher 时没有把已持久化的档位传进去，watcher.tier 恒为 DEFAULT_TIER(medium)，而 _tier 已恢复为 high —— 面板显示 high、引擎按 medium 跑，high 档的「写回人格字段」重启后完全失效（全仓库只有 set_guard_level 第1329行给 watcher.tier 赋值）。已实测：重启后 plugin._tier=high / plugin._watcher.tier=medium / dashboard guard_level=high。 | on_startup 里创建 watcher 后加 self._watcher.tier = self._tier（_load_persisted_state 已先把 _tier 恢复好），on_config_change 里同样补一次。 |
| [x] | `__init__.py:554` | `_run_poll` | _speak 的门是 evaluation.has_changes(=bool(changes))，而 changes 含 L3 与「已被回退」的项，raised 才是有条目的。于是 low 档（承诺『记录但不出声』）仍被推消息，且正文是 intro+outro、条目数为 0 —— 她被要求就一个空列表发言。已实测：low 档 changes=1 raised=0 recorded=1，pushed_messages=1，bullet items=0；medium 档纯 L3 改动（subtitleEnabled）同样推出零条目消息；高档次 tick 的自我回退也会触发。 | 把出声条件改成 if evaluation.raised:（不必再看 has_changes）；_persist_state 仍按 has_changes 调用。 |
| [x] | `__init__.py:947` | `get_dashboard` | self._watcher.last_probe.revision 只在 if self._watcher 上判空，没判 last_probe。首次轮询完成前（启动后最长 20s）以及主服务不可达导致轮询永不成功时，last_probe 恒为 None → get_dashboard 直接抛 AttributeError，面板整体加载失败。已实测：on_startup 后立刻 get_dashboard → AttributeError: 'NoneType' object has no attribute 'revision'（同一次会话里 guard_status 正常）。 | 改为 (self._watcher.last_probe.revision if self._watcher and self._watcher.last_probe else None)。 |
| [x] | `__init__.py:947` | `get_dashboard` | 面板数据里 `self._watcher.last_probe.revision` 被无条件解引用，而 SettingsWatcher.last_probe 在第一次成功轮询前恒为 None（main_server_client.py:340），所以只要主服务读不到（启动后 20 秒内、或主服务一直不可用），打开面板就抛 AttributeError: 'NoneType' object has no attribute 'revision'，整个面板打不开——恰恰是它该显示 last_error 的时刻。 | 改成 `self._watcher.last_probe.revision if self._watcher and self._watcher.last_probe else None`，_summary 里第 582 行同样处理。 |
| [x] | `ui/panel.tsx:194` | `sendFeedback` | call() 内部 try/catch 后不重抛，所以 await call(...) 永远正常返回；sendFeedback 随后无条件 setFbText("") 并关闭弹窗。发送失败（feedback_too_long / feedback_busy / feedback_failed）时用户手写的正文被清空 + 弹窗关闭，而 Python 侧特意做到「不截断、说清原因、不丢内容」的努力被前端一次抹掉。 | 让 call() 返回 boolean（成功 true / catch 里 false），sendFeedback 只在成功时清空并关闭弹窗。 |

## P1

| 状态 | 位置 | 符号 | 问题 | 建议修法 |
|---|---|---|---|---|
| [x] | `__init__.py:525` | `_run_poll` | 拿不到 poll 锁时直接 return Ok({"status":"busy"})，check_now 把它原样返回；面板 call() 只要调用不抛就 toast 成功。于是用户在后台轮询进行中点『立即检查』，界面提示『已检查』，实际这次检查被整个丢弃（force=True 也一样丢）。这正是插件存在的意义所针对的『报告成功、实际没干活』。 | busy 时返回 Err(code='busy')，或让 check_now 在 busy 时等待锁而不是放弃；面板据 Err 显示『正在检查中，请稍候』。 |
| [x] | `__init__.py:371` | `on_config_change` | on_config_change 调 _reload_config 更新了 self._base_url，并返回 {"watching": self._base_url} 对外宣称新地址，但 self._client 只在 on_startup 第339行构造过一次、此后从不重建，_make_client 用的仍是旧 base_url。改了 main_server_base_url 后插件继续打旧端口，却报告已生效。 | on_config_change 里检测 base_url 变化并重建 MainServerClient（顺带重建/更新 watcher.client），或让 MainServerClient 从 getter 读 base_url。 |
| [x] | `__init__.py:748` | `_maybe_check_memory` | memory 的扫描/备份/还原全部是同步文件系统 IO，直接跑在事件循环上：scan_memory 用 Path.rglob 递归 + 逐文件 stat，create_backup 递归 shutil.copy2 整棵目录（_copy_database 还开 sqlite 连接），restore_file 逐个 copy2。全插件搜不到任何 asyncio.to_thread / run_in_executor。在装有 360 实时防护的机器上，备份一次可能让事件循环阻塞数秒到数十秒，正是任务里说的『同步 IO 卡住导致进程假死』。 | 把这些同步调用统一包进 asyncio.to_thread(...)（scan_memory / create_backup / prune_backups / list_backups / restore_file），并给 entry 加与耗时相称的超时。 |
| [x] | `__init__.py:1222` | `set_guard_enabled` | secrets.compare_digest(str(consent_token), expected) 对非 ASCII 字符串直接抛 TypeError（CPython 限制），而 input_schema 只限制 type=string/maxLength=128，中文 token 是合法输入，且宿主对 input_schema 型 entry 不做校验（entry_runtime.py:71 走 _drop_unsupported_host_ctx）。 | 先 encode 再比较：`secrets.compare_digest(consent_token.encode('utf-8','surrogatepass'), expected.encode('utf-8'))`，或在入口处校验 token 为 ASCII。 |
| [x] | `__init__.py:748` | `_maybe_check_memory` | 整条记忆链路都是同步阻塞 IO 却直接跑在事件循环上：scan_memory（rglob + 逐文件 stat）、create_backup / shutil.copy2、sqlite3 在线备份、prune_backups 的 shutil.rmtree、restore_file，全插件 0 处 asyncio.to_thread（宿主侧 ui_query_service.py 自己是用 asyncio.to_thread 的），在带实时防护的机器上备份一个含活 SQLite 的记忆目录会卡住定时器和其它回调。 | 把 _maybe_check_memory / _backup_memory / restore_memory 里的文件与 sqlite 操作放 `await asyncio.to_thread(...)`。 |
| [x] | `feedback.py:202` | `deliver` | 末尾只用 "429" in last_error 判定『通道忙』。HTTP 429 分支把 last_error 设成字符串 'rate limited (HTTP 429)' 所以能命中；但 body 级限流（HTTP 200 + success:false + 'Too many requests, slow down.'）走第189行 last_error=reason，字符串里没有 429 → 抛 FeedbackUndeliverable，用户看到『消息发不出去，见日志』而不是『通道忙，稍后重按发送，内容没丢』。已实测：(a) HTTP 429 → FeedbackRateLimit | 在 body 级限流分支设一个独立的布尔/类型标记（例如 last_error = _RateLimited(reason) 或另置 rate_limited=True），末尾按标记而非字符串内容分类。 |
| [x] | `feedback.py:192` | `deliver` | wait = retry_after 时，_retry_after_seconds 只把值夹到 30s，而重试共 3 次、前两次都会 sleep → 最坏 60s 睡眠，再加 3×DEFAULT_FEEDBACK_TIMEOUT(15s)=45s HTTP，合计 105s，超过 submit_feedback 声明的 timeout=75.0（__init__.py:1485）。测试 test_the_backoff_fits_inside_the_entry_timeout 只断言常量 sum(FEEDBACK_RETRY_DELAYS)<=30（实测 21s），完全没覆盖 Retry-Af | 按整个调用预算分配等待（记录起始时刻，剩余预算不足就停止重试），或把 Retry-After 的采用值压到 min(剩余预算, 单次上限)，并让该测试覆盖 Retry-After 路径。 |
| [x] | `feedback.py:202` | `deliver` | 最终分类用 `"429" in last_error` 判断限流，但 body 级拒绝分支（第 189 行 last_error = reason）存的是人话（如 'Too many requests… slow down'），不含 '429'，于是限流被报成不可投递：用户看到 errors.feedbackFailed（自己找问题）而不是 errors.feedbackBusy（稍后重按）——正是模块 docstring 说要避免的那种误导。 | 用一个布尔量记录限流（HTTP 429 或命中 _RATE_LIMIT_HINTS 时置 True），最后按该布尔量选 FeedbackRateLimited / FeedbackUndeliverable，别拿消息文本当分类依据。 |
| [x] | `settings_guard.py:865` | `GuardState.evaluate` | 有限期授权过期后的「重新回到 pending」只 append 到 evaluation.raised，不 append 到 changes；而 __init__.py:554 只在 has_changes 为真时 _speak + _persist_state。于是这次回归 (a) 永远不会被她念出来（pushed_messages 增量为 0），(b) 状态翻转不落盘，store 里仍是 accepted —— 有限期授权在重启后变成永久静默，正是 accept() docstring 声称已修好的那个 bug。已实测：check_now 返回 status=unchanged/chang | 把该循环的 dispute 也记进 evaluation.changes（或单独置一个需要持久化的标志），并让 _speak 只在 raised 非空时发声（见 P0 第2条）。 |
| [x] | `settings_guard.py:715` | `AuthorizationLedger.from_payload` | from_payload 只对容器类型做防御（isinstance Mapping/list），对数值字段直接用 float()/int()（此处及 761/765 行，diagnostics.py:79-81 同病），非数值脏值抛 ValueError；而 __init__.py:437 与 444 调用它时没有 try/except，on_startup 会直接失败，插件起不来且没有任何自愈路径。 | 把 float()/int() 包一层 coerce-or-default 辅助函数（同文件 _positive_float 的写法），或至少把 _load_persisted_state 里的两次 from_payload 包进 try/except 后退回全新状态。 |
| [x] | `ui/panel.tsx:203` | `openFeedbackPage` | window.open(url, "_blank", "noopener,noreferrer") 在带 noopener 时按规范返回 null，所以 `if (!opened)` 在**打开成功**时也成立，用户会看到「打开失败 + URL」的假报错（无接收地址时的唯一退路就这条）。 | 去掉 windowFeatures 里的 noopener（或改用 <a target="_blank" rel="noopener noreferrer">），不要用返回值判断成败。 |

## P2

| 状态 | 位置 | 符号 | 问题 | 建议修法 |
|---|---|---|---|---|
| [x] | `__init__.py:184` | `ATTACHMENTS_SUPPORTED` | ATTACHMENTS_SUPPORTED=False、FEEDBACK_LIMIT_BYTES、FEEDBACK_ENVELOPE_BYTES 三个常量定义后从未被读取（仅 FEEDBACK_BODY_LIMIT_BYTES 在第1519行真正使用）；settings_guard.REVERTIBLE_PREFIX 也未参与 revert_field 的判定（后者硬编码 parts[1]=='猫娘'）。都是死代码。 | 删除未被读取的常量，或让实际校验改用 FEEDBACK_LIMIT_BYTES 以消除『常量与实参各写一份、可能漂移』的风险。 |
| [x] | `__init__.py:1064` | `accept_setting` | _persist_state 会返回 bool（_store_write 失败或 store 被禁用时为 False），但 accept_setting / keep_objecting / set_guard_enabled / set_guard_level 的返回值全部被忽略，仍回 Ok({"status":"accepted"})。store 写失败只在 logger.warning 里留痕，用户看不到，重启后这次接受就没了 —— 属于本插件最在意的『报告成功但没落地』。 | 这些 entry 检查 _persist_state() 的返回值，失败时返回 Err(code='state_not_persisted') 并在面板提示。 |
| [x] | `__init__.py:1201` | `set_guard_enabled` | _pending_disable（含 consent_token 与 requested_at）只存在内存，_persist_state 不写它。用户被明确要求『过一会儿拿这个 token 再调一次』，若这期间插件重启（或宿主重载插件），同一个 token 再拿来会得到 invalid_consent_token；此外该 pending 请求没有过期时间，进程内可无限期反复成功。 | 把 pending 的 token 与 requested_at 一起持久化（或至少把无效 token 的错误文案改成『请重新发起一次关闭请求』），并给 pending 加一个合理的过期上限。 |
| [x] | `__init__.py:512` | `_poll_lock` | 锁在事件循环变化时被重建（self._lock_loop is not loop → 新 Lock）。main_server_client.py:124-139 的注释明确说 SDK 的 timer 回调运行在『本插件不拥有、且不能假定每次相同的 loop』上；一旦两次 tick 落在不同 loop，第二次会拿到一把全新的未锁 Lock，locked() 判断为假 → 两次 poll 并发跑，重复发声、并发读写 _state，并可能并发执行两次『读整份 profile 再整份写回』的 revert（后者会互相覆盖）。 | 改用不绑定 loop 的同步互斥（threading.Lock + 置位标志）或在 _run_poll 入口加一个不依赖 loop 身份的重入标志；并让 _run_reverts 对同一角色串行化。 |
| [x] | `__init__.py:496` | `settings_watch` | guard 被关闭时 settings_watch 提前 return，于是 _maybe_check_memory 也不再执行 —— 她的记忆目录既不再被扫描也不再被每日备份，而这是记忆唯一的自动保护。关闭提示（DEFAULT_DISABLE_REQUEST）只承诺『停止发现新的改动』，没说要停掉备份，面板却仍显示 memory 区块，用户会以为保护还在。 | 把 _maybe_check_memory() 移到 enabled 判断之前（或明确在关闭提示里写清备份也会停）。 |
| [x] | `__init__.py:428` | `_reload_config` | 配置重载时清了 _memory_root 缓存，却没有清 _memory_snapshot（内存里仍是旧根的快照）。若 memory_root 真的换了位置，下一次检查会把新根下的每个文件都当成 added、旧根每个文件都当成 removed，_memory_changes 被刷成满屏假变更；而 _memory_error 此时为空，面板看不出这是换根导致的。 | _reload_config 里同时把 _memory_snapshot 置空并记录一个 'memory_root_changed' 事件，让下一次扫描重新走 baseline 分支。 |
| [x] | `__init__.py:421` | `_reload_config` | memory_backup_keep 的解析把 bool 也当成合法数字（bool 是 int 的子类）：配置里写 false（或 JSON 里的 0）会得到 0，随后 plan_retention(daily_keep=0) 会把所有未置 pin 的备份全部列入 prune 并物理删除，只留 pinned 的。注释只说负数会夹到 0，没说 0 等于『不留任何滚动备份』。 | 对 memory_backup_keep 做下界保护（例如 min 1）或对非正整数给出显式告警并回退到默认 14；bool 应被 _non_negative_int 之外的显式判断排除。 |
| [x] | `__init__.py:545` | `_run_poll` | _user_locale 只在 first_seen 那一轮回读一次；而 _load_persisted_state 每次启动都把它置回 None。若这唯一一次 fetch_user_language 失败（返回 None）或主服务当时不可达，进程生命周期内所有面向她的文案就永远用 fallback 的 zh-CN，即使她/用户之后切了语言也不会纠正（面板也无处触发重读）。 | 在 locale 为空时于后续轮询里重试（例如每 N 分钟或检测到 revision 变化时重读一次），而不是只赌第一次。 |
| [x] | `__init__.py:164` | `FEEDBACK_LIMIT_BYTES` | FEEDBACK_LIMIT_BYTES 从未被读取（FEEDBACK_ENVELOPE_BYTES:160 也只用于算它），但注释宣称「由上面两个推导，面板与校验就不可能漂移」——真实情况是面板里另写了一个字面量 FEEDBACK_LIMIT_KB = 63（panel.tsx:152），这句话的保证没有被实现。 | 要么删掉这两个常量并改写注释，要么把限额通过 dashboard 下发、面板不再写死。 |
| [x] | `__init__.py:184` | `ATTACHMENTS_SUPPORTED` | ATTACHMENTS_SUPPORTED（184）、ISSUE_TRACKER（187）、HER_PROTECTION_REASON（137）三个常量全仓无人读取：面板自己写死了仓库 URL 字面量（panel.tsx:159），附件说明与她的理由都走 i18n key，于是注释里「面板会照这个说明」「这是她自己说的理由」的意图都没落地。 | 删常量，或把三者经 dashboard 下发给面板使用。 |
| [x] | `__init__.py:1726` | `restore_memory` | dry_run / path 直接用原始入参：`if dry_run:` 把任何假值（含模型传的 null）都当成「真还原」，`wanted = [path.strip()] ...` 在 path 为 null 时抛 AttributeError——破坏性操作的安全默认值可以被一个 null 反转，而 schema 在宿主侧不做校验（entry_runtime.py:71 对 input_schema 型 entry 跳过校验）。 | 入口先归一化：`dry_run = True if dry_run is None else bool(dry_run)`、`path = str(path or "")`，并只在 `dry_run is False` 时执行还原。 |
| [x] | `diagnostics.py:147` | `HealthLog.from_payload` | from_payload 逐条 log._events[event.code] = event，不像 record() 那样在超过 limit 时裁剪，所以从 store 读回一个超限的 health 列表会得到超过 limit 的种类数（下次 record 才会被裁回），kinds()/to_payload() 会短时失真。 | 装载完成后按 last_at 排序裁剪到 limit，或复用 record() 的裁剪路径。 |
| [x] | `diagnostics.py:131` | `clear` | HealthLog.clear() 从未被调用（也无人再写入新错误后想清空）。 | 删除该方法。 |
| [x] | `main_server_client.py:288` | `prune_mirrored_preferences` | prune_mirrored_preferences + GLOBAL_CONVERSATION_SENTINEL 在 tests/ 下 grep 结果为 0，零测试；且 tests/conftest.py:53 把 preferences 造成 dict，而生产环境是 list（代码注释与 settings_guard.py:267-271 都按 list 描述）。于是镜像剔除逻辑与 flatten 的按下标下降分支都没有端到端覆盖。 | conftest 的 preferences 改成 list（含一条 model_path=__global_conversation__ 的镜像项），并加一条断言镜像被剔除、下标路径落到正确分级的用例。 |
| [x] | `main_server_client.py:344` | `mark_full_rescan` | mark_full_rescan 从未被调用（fetch_snapshot:205、RevisionProbe.to_payload:106 同样零调用）；而且它的语义与名字相反——它把 last_full_rescan 置新，_full_rescan_due 便因此**跳过**本次比较，任何按名字理解为「强制重扫」的调用点都会得到反向行为。 | 删除三个死符号；若确实需要强制重扫，用 poll(force=True) 并把语义写进名字。 |
| [x] | `memory_backup.py:157` | `scan_memory` | with_digest 分支先 path.read_bytes() 把整个文件读进内存，再交给 digest_bytes 判 8MB 上限 —— 上限的用意『别让大文件把备份变成停顿』在最坏情况下失效（文件已全量读入）。当前生产代码没有任何调用点传 with_digest=True，所以目前只是潜在缺陷，但参数被导出且测试在跑它。 | 先 stat().st_size，超过 MAX_DIGEST_BYTES 直接跳过读取；或改成流式分块 hashlib。 |
| [x] | `memory_backup.py:253` | `set_pinned` | set_pinned 从未被调用，PINNED_MARKER 只在 list_backups 里被读（is_file()）从未被写，所以生产路径上不存在任何 .pinned 标记——「里程碑永久保留」的整条机制（BackupInfo.pinned、plan_retention 的 pinned 分支、test_memory_guard 的两个 pinned 用例）都是不可达代码。 | 删掉 pinned 机制，或补一个入口/面板动作真的能设置它。 |
| [x] | `memory_backup.py:179` | `_copy_database` | `with sqlite3.connect(...) as origin, sqlite3.connect(...) as copy:` 并不关闭连接——sqlite3 的上下文管理器只负责事务提交/回滚，于是每次调用都留下两个打开到 GC 才释放的连接句柄；在 Windows 上这期间活库文件被占用，且任何异常路径都会让句柄留得更久。 | 用 contextlib.closing 包裹，或 try/finally 显式 close()。 |
| [-] | `memory_guard.py:101` | `MemoryFile.to_payload` | MemoryFile.to_payload(101) 与 from_payload(104) 零调用；更关键的是 scan_memory 的 with_digest 参数没有任何调用点传 True（__init__.py:748/1700 都用默认 False），因此 MemoryFile.digest、digest_bytes、MAX_DIGEST_BYTES 与 _looks_changed 的 digest 分支在生产上全部不可达（只有测试能走到）。 | 删掉 to_payload/from_payload 与 with_digest 分支，或让备份路径真的去算 digest。 |
| [x] | `pyproject.toml:1` | `` | 插件自身没有声明任何 pytest 配置，13 个 async 用例依赖仓库根 pytest.ini 的 asyncio_mode = auto；用插件自己的配置跑就全部失败，发布成独立插件后这套测试不再是「126 passed」。 | 在插件 pyproject.toml 里加 [tool.pytest.ini_options] asyncio_mode = "auto"，或在每个 async 用例上加 @pytest.mark.asyncio。 |
| [ ] | `settings_guard.py:500` | `_flatten_into` | 『list of structures 按下标下降』这个分支没有测试：现有 flatten 用例只有嵌套 dict 与 {"position":[1,2,3]} 这种标量列表（tests/test_settings_guard.py:88,102）。按下标下降带来的隐患（列表中段增删会让后续所有下标重编号、整条尾巴被判为变化并批量产生争议）因此无从发现。 | 补一条 preferences 为 list[dict] 的 flatten 用例，并补一条『中段插入一项』的 diff 用例，明确当前行为是否符合预期。 |
| [x] | `settings_guard.py:865` | `GuardState.evaluate` | 过期授权触发的重新 pending 循环不检查 tier：把档位切到 low（『记录但不出声』）之后，一个此前 accepted 且已过期的争议仍会被 append 进 evaluation.raised，即 low 档并不能让她对这条保持安静（且同 P0 第2条，这条还会被推成零条目消息）。 | 该循环里加 if chosen == TIER_LOW: 只改状态不出声（或直接复用主循环同一套 tier 判定）。 |
| [x] | `settings_guard.py:881` | `GuardState.evaluate` | 「路径已不在快照里就退休 dispute」这一趟跑在 diff 循环之后，于是字段被删除时同一 tick 先 _raise 建出 dispute、再被这趟 del 掉：evaluation.raised 里报了她一句、面板却什么都不剩，流程自相矛盾（_raise 做的 after_preview/times_raised 更新全部白做）。 | 退休条件加一条：跳过出现在 evaluation.changes 里的路径，或把退休那趟挪到 diff 循环之前。 |
| [x] | `settings_guard.py:598` | `is_revertible` | 前值是 JSON null 时 kind=='null'、truncated False，于是 is_revertible 返回 True，high 档把它放进 to_revert；但 restore_payload 对同一 Value 返回 None（raw 用 None 兼作「原始值就是 null」与「原始值不可得」两个含义），_result 是每次都被判成 value_unavailable 的空跑。 | is_revertible 里加 `if before.kind == "null": return False`，或改用 `restore_payload(before) is not None` 作为判据。 |
| [x] | `settings_guard.py:184` | `REVERTIBLE_PREFIX` | 常量定义并导出但全仓无人读取；revert_field 自己硬编码了同样的 "characters"/"猫娘" 判定，两处会各自漂移（声明「每个可纠回路径都从这里开始」的保证没有被代码执行）。 | 删掉常量，或让 revert_field 真的用它做前缀判断。 |
| [x] | `settings_guard.py:311` | `ACCESS_RAISED` | ACCESS_RAISED/ACCESS_RECORDED/ACCESS_AUTHORIZED（311-313）与 LEVELS（83）都是定义了、导出到 __all__ 却从未被读取的常量；evaluate 用的是列表（raised/recorded/authorized）而非这些字符串。 | 删除这四个常量及其 __all__ 条目。 |
| [x] | `settings_guard.py:282` | `preferences.*.proactive*` | 这条 L1 规则（连同 278-281 行「她的自主权就住在 preferences.2」的注释）在真实数据上永远不触发：main_server_client.prune_mirrored_preferences 恰好丢掉带 __global_conversation__ 标记的那一条（即承载 proactive* 的镜像项），而 conversation.settings.proactive* 已经覆盖了自主权——两个模块对同一份数据的假设互相矛盾。 | 删掉这条规则与其注释，或在注释里说明镜像项已被裁剪、这条只是兼容性兜底。 |
| [ ] | `tests/conftest.py:44` | `FakeMainServer.preferences` | conftest 把 preferences 造成 {'model-a':{...}} 的字典，而 settings_guard.py:267-286 明确记载真实端点「是三元素列表，第三项装 proactive 旗标」；没有任何测试喂过「列表套映射」的数据，于是 _flatten_into 里为 preferences 专门写的按下标下钻分支（settings_guard.py:500-517）在整个套件里从未被执行。 | 让 conftest 用文档所述的真实形态（三元素列表，含 __global_conversation__ 镜像项）造数据，并加一条列表下钻的直测。 |
| [x] | `tests/test_change_detection_live.py:191` | `test_engine_metadata_is_not_mistaken_for_a_setting` | 用例名断言「引擎元数据不会被当成设置」，但它只改了 userLanguage，从未改动 telemetryBranch——一个永不变化的字段本来就不产生 diff，所以无论 conversation_slice 是否过滤掉 telemetryBranch，这条都恒过，名字声称的不变量没有被检验。 | 改成 fake.settings["telemetryBranch"] = "dev" 后断言 evaluation.changes == []。 |
| [x] | `tests/test_feedback.py:205` | `test_the_backoff_fits_inside_the_entry_timeout` | 该用例名为『退避要落在 entry 超时内』，实际只断言常量 sum(FEEDBACK_RETRY_DELAYS)<=30，没有覆盖 Retry-After 驱动的 sleep 路径（feedback.py:191-197），而正是那条路径让最坏耗时到 105s > 75s。断言与它声称保证的性质不匹配。 | 让该用例断言『最坏总耗时 <= submit_feedback 的 timeout』，并把 Retry-After 上限纳入计算或断言其被预算裁剪。 |
| [x] | `tests/test_feedback.py:199` | `test_the_backoff_fits_inside_the_entry_timeout` | 用例名声称「总等待要落在 SDK 允许的动作超时内」，实际只断言 sum(FEEDBACK_RETRY_DELAYS) <= 30.0（一个与 entry 的 timeout=75.0、次数、单次请求超时都无关的硬编码常量），所以它名字所指的漂移（submit_feedback 的 75s 被改小）测不出来。 | 用真实公式断言：attempts * DEFAULT_FEEDBACK_TIMEOUT + sum(FEEDBACK_RETRY_DELAYS) < submit_feedback 的 timeout，并把该 timeout 抽成常量供两边引用。 |
| [x] | `tests/test_plugin_runtime.py:261` | `test_the_record_survives_a_restart` | 重启用例只断言 dispute 幸存与 pending_count，从不断言档位在重启后仍生效 —— 所以 P0 第1条（tier 没种进 watcher）在 126 项测试下全绿。同理没有任何用例在 on_startup 后、首次 poll 前调用 get_dashboard，所以 P0 第3条的崩溃也没被发现。 | 在重启用例里先 set_guard_level('high')，重启后断言 plugin._watcher.tier=='high'；并加一条『on_startup 后立刻 get_dashboard 不抛异常』的用例。 |
| [x] | `tests/test_plugin_runtime.py:101` | `test_plugin_detects_a_change_and_speaks` | 本文件（以及全部用例）里每次 get_dashboard() 都发生在至少一次成功的 check_now() 之后，因此「首次轮询前打开面板」这条路径 126 个测试一个都没覆盖——P0 才能一直绿。 | 加一个用例：on_startup() 后直接 await plugin.get_dashboard()（含主服务不可达的变体），断言不抛且 revision 为 None。 |
| [x] | `tests/test_settings_guard.py:61` | `test_dotted_model_path_still_lands_on_a_known_level` | docstring 说「前缀规则匹配不上它，所以必须靠叶子名回退来兜底」，实际是错的：preferences.* 这条 2 段规则就命中了（specificity 2 > -1，best_level 非空后第 366 行的 FIELD_LEVELS 回退根本不会执行），断言是「对的结论、错的机制」。 | 改 docstring 说明真正生效的是 preferences.* 的兜底规则，或把该用例改成直接测 FIELD_LEVELS 回退（用一个不含 preferences 前缀的路径）。 |
| [x] | `ui/panel.tsx:144` | `DashboardState` | 第144行读了 state.feedback_endpoint，但 DashboardState（第64-97行）没有声明该字段且无索引签名 → 类型错误。仓库内没有 tsconfig/package.json，本地无法类型检查，只能等宿主构建时才暴露。 | 在 DashboardState 里补 feedback_endpoint?: string（顺手补 revert_outcomes?: {path:string;reason:string}[]）。 |
| [x] | `ui/panel.tsx:96` | `DashboardState` | get_dashboard 返回的 revert_outcomes（__init__.py:911-914，每条 {path, reason}）在前端完全没被消费，面板无处显示『她想把某项放回去但没成功』—— 而 Python 侧注释明确说这是更诚实、更有价值的那一半。另外 disable_ready_at / disable_confirm_after / default_level 声明了也没渲染，面板上没有任何地方能让用户拿 token 完成关闭流程。 | 在状态卡里渲染 revert_outcomes（空 reason=已放回，非空=显示原因码对应文案）；pendingDisable 分支里补上『等 N 秒后可用 token 确认』的提示与入口。 |
| [x] | `ui/panel.tsx:183` | `copyFeedback` | clipboard.write 返回 false（沙箱拒绝、无权限）时只有 if (ok) toast.success，没有 else —— 用户点了『复制』，按钮毫无反应也无任何提示，与 openFeedbackPage 那种『被拦下就把地址显示出来』的写法不一致。 | 加 else toast.error(t('ui.feedback.copyFailed'))，并把要复制的内容显示出来供手动选择。 |
| [x] | `ui/panel.tsx:156` | `buildFeedbackBody` | 面板把上限写成『正文 63 KB』并只对 fbText 计字节（feedbackTooLong），而 Python 强制的是整个 envelope（含诊断报告与 JSON 包装）≤ 64 KB（__init__.py:1518-1519）。正文 63KB 时 envelope 必然超限，于是面板实时计数显示『没超』、用户点发送却被拒绝 —— 恰好破坏了该实时计数存在的理由。 | 面板按同一口径计算：把诊断报告与包装的固定开销（Python 侧约 1KB）纳入，或让 Python 把 body 上限与已用字节回报给面板由面板复用同一数字。 |
| [x] | `ui/panel.tsx:144` | `feedbackEndpoint` | state.feedback_endpoint 被读取，但 DashboardState 类型（第 64-97 行）没有声明该字段，PluginSurfaceProps<DashboardState> 下是 TS2339；Python 侧确实返回了它（__init__.py:906），即类型声明与实际契约不一致。 | 在 DashboardState 里补 `feedback_endpoint?: string`。 |
| [x] | `ui/panel.tsx:87` | `disable_confirm_after` | disable_confirm_after(87) 与 disable_ready_at(88) 声明在 DashboardState、也由 get_dashboard 返回（__init__.py:953-957），但面板从不渲染；实际结果是面板只能发起「关闭守卫」请求，没有任何路径把 consent_token 交回去完成关闭，两个字段成了悬空契约。 | 要么在面板上用它做倒计时/确认按钮，要么从类型与返回值里删掉。 |

## -

| 状态 | 位置 | 符号 | 问题 | 建议修法 |
|---|---|---|---|---|
| [-] | `(跨文件)` | `` | DESIGN.md §6 与 README 明确写『不做 轮询+回滚』『只写自己目录』，但代码实现了完整的 revert 引擎（revert_field/restore_payload/_run_reverts）并向 /api/characters 写回人格字段——直接违反文档自己声明的两条约束，文档与行为已自相矛盾。 | confidence=高｜impact=插件对外宣称的安全/架构边界（不回滚、只写自己目录）与事实不符，审查者会基于错误前提放行。 |
| [x] | `(跨文件)` | `` | SettingsWatcher.poll 的 fetch_others 是『全有或全无』：四个快照源中任意一个 500，整个 poll 抛 MainServerUnreachable，其余三个源的变更检测全部停摆；而 build_snapshot 明明为 None 源写了容错，fetch_others 却永不返回 None（只抛异常），那段容错是死代码。 | confidence=高｜impact=任一读源长期故障（如含 API key 的 core_api 在某些配置下 500）会让插件彻底停止察觉任何设置改动，且错误信息只点名那一个源，掩盖了『全员失盲 |
| [x] | `(跨文件)` | `` | _poll_lock 按事件循环（event loop）重建锁，而 accept_setting/keep_objecting/set_guard_level/set_guard_enabled 等入口处理器从不获取该锁；DESIGN §9.1 自己承认 timer 回调不在固定 loop 上跑，因此该锁对『定时器 poll ↔ 用户操作』之间零互斥，共享的 self._state 可被并发改写。 | confidence=高｜impact=在定时器与用户操作交错时共享状态可被破坏（dispute/账本/快照丢失更新），且漏洞正是发生在插件自己文档描述的『多 loop』场景下。 |
| [x] | `(跨文件)` | `` | 后端在 get_dashboard 里发出 revert_outcomes（高挡自动纠回的逐路径成功/失败原因），但 panel.tsx 从头到尾没有渲染它——这是典型的『后端产出、前端不消费』契约漂移。 | confidence=高｜impact=高挡『自动把她的人格放回去』这一招牌功能失败时完全不可见，恰恰是该插件用来抓别人的『静默失败』，自己却犯了。 |
| [-] | `(跨文件)` | `` | Value.raw 刻意不持久化（docstring 明说）。重启后，被截断（>120 字符）的长人格值再也无法被高挡纠回（is_revertible→False、restore_payload→None），但该 dispute 仍以 pending 留在面板，且因 to_revert 不再包含它、_run_reverts 永不执行，也不会留下任何『纠回不可用』的原因。 | confidence=中｜impact=重启后长文本人格变更既不被自动还原、也不被告知失败，处于『挂着但永不动作』的静默态。 |
| [x] | `(跨文件)` | `` | _revert_change 用 change.before（上一拍快照值）做还原目标，而非 dispute.base_digest（她真正的原值）。任何『纠回失败/值又被改』的序列后，高挡会把错误的『中间值』写回去，而不是她的原始值。 | confidence=中｜impact=在失败/再编辑序列后，高挡自动纠回会把一个她从未有过的中间值当成『她的原值』写回。 |
| [-] | `(跨文件)` | `` | DEFAULT_FEEDBACK_ENDPOINT 是硬编码非空 URL，导致『未配置端点→复制并打开』的回退路径（submit_feedback 的 feedback_endpoint_missing 分支、面板 !canSendDirectly 时只显示 GitHub 按钮）在默认配置下永不可达；而 feedback.py 的 docstring 与 get_dashboard 的注释都声称相反情况。 | confidence=中｜impact=作者以为『无端点时优雅降级』的链路在出厂默认下是死的；若该 FormSubmit 表单失效，用户只看到『发送失败』而意识不到还有手动通道（尽管手动通道在 mod |
| [-] | `(跨文件)` | `` | proactive 类开关在 SENSITIVITY_RULES 里同时以 conversation.settings.proactive* 与 preferences.*.proactive* 两条 L1 规则注册，但 prune_mirrored_preferences 只剪掉了 __global_conversation__ 镜像；同一逻辑开关若同时出现在两个端点，会产生两条 dispute（问她两次），而这正是 pruning 逻辑本来要消灭的噪音。 | confidence=中｜impact=单一 proactive 设置变更可能让猫娘对同一件事被问两遍，违背插件自己强调的『不重复打扰』原则。 |
| [x] | `(跨文件)` | `` | set_guard_enabled(false) 的『待确认禁用』状态 _pending_disable 仅存内存、不持久化；插件重启会让进行中的禁用请求与令牌静默消失，面板 disable_pending/disable_ready_at 翻回 false，且无任何『曾请求过禁用』的记录。 | confidence=低｜impact=依赖禁用确认流的用户在重启（或崩溃恢复）后丢失在途请求，且面板不提示『曾有过请求』。 |
