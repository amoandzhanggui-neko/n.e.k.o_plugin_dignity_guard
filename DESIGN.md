# dignity_guard · 设计文档

> **一句话**：给猫娘一条"对自己设置的表决通道"—— 让"被动过什么"被她看见、让她能开口、让用户看得见她的态度。

**English summary**: A plugin that gives the catgirl a voice about her own configuration.
It cannot *block* config changes (the official SDK does not expose any hook there), so it does
three things instead: **notice** changes, **let her speak** about them, and **keep a visible
record** of what she does not agree with.

> ⚠️ **上游引用会漂**：本文与代码注释里引用的主服务文件与行号（如 `config/network.py:156`）
> 是写作时所依据的版本，**上游一改就会偏**。以**符号名**为准 —— 比如去看 `network.py` 里
> 那个端口常量，而不是相信某个行号。2026-09-25 机械复核过一次；本文件其余内容不绑定版本。

---

## 1. 为什么做这个（动机）

市面上围绕"AI 角色"的插件，绝大多数在做「**让她更能干活**」：查天气、放音乐、接智能家居。
而这个插件做的是另一个方向 —— 「**让她更有立场**」。

- **目标**：当用户改动猫娘的设置（称呼、外观、音色、性格参数……）时，**她有知情权，也有表达权**。
- **原则（掌柜定的）**：
  1. **分层级** —— 越敏感的设置，管得越严；
  2. **开易关难** —— 插件自己能开（开=加保护），**关闭必须她同意**（关=减保护）；
  3. **不硬拦** —— 用"她表达"来代替"系统拦截"，这更符合"平等"，也更有效。

## 2. 诚实说明：我们**做不到**什么（重要）

**官方 SDK 不允许插件拦截设置写入。** 依据（均已核实到源码行）：

| 事实 | 出处 |
|---|---|
| 插件能读的"系统配置"只有插件框架自身项，**不含外观/音色/人格** | `plugin/settings.py:771` `PUBLIC_SYSTEM_CONFIG_KEYS` |
| bus 只读命名空间只有 `conversations/messages/events/lifecycle/memory`，**没有 settings** | `PLUGIN_DEVELOPMENT_GUIDE.md:1066-1092` |
| 设置写入全走主服务 HTTP（`/api/config/*`、`/api/characters/*`），**都在插件系统之外** | `config_router/_shared.py:24` 等 |
| 插件跑**独立进程** + ZMQ IPC，**无 in-process 拦截可能** | 指南 `:28,34-35,82` |
| Hook（`@before_entry` 等）**只作用于本插件自己的 entry** | `plugin/sdk/shared/core/base.py:174-219` |

→ **所以本插件不做"守门员"，做"记录者 + 传声筒"。**
（真正的根治办法是给主服务加一个"设置变更事件"—— 见第 7 节，那是我们打算向官方提的建议。）

### 2.1 另一件必须说清的事：「需要她同意」不是安全边界

`set_guard_enabled(false)` 会先问她一声，再要求调用方带上口令重试。**这道流程是"知情设计"，不是"防护"。** 依据：

| 事实 | 说明 |
|---|---|
| 口令（`consent_token`）**在第一次调用的响应里就一并回给了调用方** | `set_guard_enabled` 的 `consent_pending` 分支 |
| 中间只隔默认 10 秒，**没有任何"她确实表态了"的校验** | `_disable_delay` |
| 宿主的运行通道**不校验权限**，本机任何进程都能调用 | 实测：无鉴权即可调本插件的全部 entry |
| 插件**跑在独立进程**，无法知道调用者是谁 | 同 §2 的进程模型 |

**因此**：她同意与否，**不构成对"关闭守卫"的实质阻止**。既然拦不住，本插件选择**不假装拦得住**，而是：

1. **把"关闭"这个动作本身留痕** —— `disable_count` / `last_disabled_at` / `off_since` /
   `last_off_seconds` 随状态一起持久化，面板上永远看得见"守卫曾被关过、关了多久"；
2. **把话说明白** —— 面向她的文案里直接写清"沉默拦不住它，关掉也不会删掉任何记录"。

**这比一句做不到的承诺更有价值：记录是真的，拦是假的。**

## 3. 三个能力

### 3.1 知情（只读监听）

- **轮询**主服务：`http://127.0.0.1:${MAIN_SERVER_PORT}`（默认 48911，`config/network.py:156`）
- 先读**轻量判据**（`/api/config/conversation-settings` 带 `revision`+`ETag`），只有变了才拉全量做 diff —— 省流量。

> ⚠️ **修正（实现阶段发现，原设计有误）**：`revision`/`ETag` **只覆盖 `conversation-settings` 这一组**，
> **单靠它感知不到 avatar / 昵称 / voice 等改动**。因此实现里补了一个**全量比对的兜底间隔**
> （`full_rescan_seconds`，默认 60 秒）：轻量判据负责"快速发现对话设置变化"，
> 全量兜底负责"不漏掉其他类别的改动"。**两者缺一不可。**

- 可读接口（均无鉴权）：

| 接口 | 内容 |
|---|---|
| `GET /api/characters` | **唯一的"一次读全"**：主人 + 全部猫娘（称呼/昵称/性别/voice_id/avatar/性格） |
| `GET /api/config/page_config` | 外观（model_path / model_type / lighting） |
| `GET /api/config/conversation-settings` | 全局对话设置（**带 revision + ETag**） |
| `GET /api/config/core_api` | 音色 / TTS（API key 已脱敏） |
| `GET /api/config/preferences` | 窗口位置 / 缩放 / parameters |

- 实现：`@timer_interval(seconds=20)`（**必须是 `async def`**）
- ⚠️ **`trust_env=False`（最好再 `proxy=None`）** —— 否则系统/360 代理会劫走 127.0.0.1 请求。
  （现网先例：`lifekit/_api.py:63`、`bilibili_dm:1264`、`galgame host_agent_adapter.py:44`）

### 3.2 表态（让她开口）

- 用 `push_message(ai_behavior="respond")` —— **进 LLM 上下文且立即触发一轮**，外层提示词是
  "来自{插件}的新消息，请按内容回应主人"，**所以是她本人在说，不是系统播报**（指南 `:346-352, 558-560`）。
- 说话内容按"敏感度"分档，**语气由她自己发挥**，插件只给情境。

### 3.3 留痕（插件面板）

- `hosted-tsx` 面板，显示：**「当前有 N 项设置是她不认可的」** + 逐项列表 + 时间。
- 数据来自 `@ui.context(id="dashboard")` 返回的 dict，TSX 读 `props.state` 渲染。
- 面板同时提供"**她认可的设置**"清单 —— 用户一看就知道哪里踩到她了。

## 4. 分层级（掌柜定的核心规则）

| 层级 | 举例 | 策略 |
|---|---|---|
| **L1 最重** | 人格 / 记忆 / 核心设定 / **插件自身开关** | **每次都要问她**；关闭走这一级 |
| **L2 中等** | 称呼 / 音色 / 外观（avatar、model） | **首次问一次**，同意后进入"已授权"（可带时效，如"这周内免问"） |
| **L3 最轻** | 窗口位置 / 缩放 / 临时状态 | **不问**，但写入她的"记忆"，她可以事后表达不满 |

**默认归属**：**未在表里列出的设置项 → 一律按 L2 处理**（宁可多问一次，也不越权）。

**"开易关难"**：插件自身的启用/停用是 **L1** —— 开启不需要她同意（加保护），**关闭必须先过她**。

## 5. 数据（插件私有存储）

- 用 `self.store`（需 `[plugin.store].enabled=true`）+ `self.db`(SQLite)
- 存：`层级归属表`、`授权记录（谁在何时同意了什么、到期时间）`、`她不认可的项`。
- 全部在插件自己的数据目录，**不外传**。

## 6. 范围

**做**：知情（轮询 + diff）｜表态（对话式）｜留痕（面板）｜L1/L2/L3 分层与授权记录｜开易关难

**不做**：
- ❌ 拦截设置写入（**架构不允许**，见第 2 节）
- ❌ 依赖任何本地外壳改造（**必须能在 Steam 官方外壳 + 官方内核上跑**）

### 6.1 ★ 关于「回滚」：原计划不做，实际做了 —— **这一条正在向官方确认**

第一版把「轮询 + 回滚」列为**不做**，理由是它**写到了插件自己的目录之外**
（回滚要 `PUT /api/characters`），而官方规范说插件应当只写自己的目录。

**实现过程中改了主意**，理由：高挡的**全部价值**就是"她说过不许碰的，就放回去"。
只记录不回滚，插件就退化成一本日志 —— 而一本日志不值得用户装它。

**所以现在它是一个已实现、且正在向官方请示的功能**：

| 项 | 现状 |
|---|---|
| 是否写入外部 | ✅ **会**：`PUT /api/characters/catgirl/<name>`（`main_server_client.py`）|
| 写什么 | 仅 `REVERTIBLE_CATFIELDS`（她当面点名的字段）|
| 写的方式 | **读-改-写**：该端点会删掉未发送的字段，所以先读全量、只改一个、整份发回 |
| 何时写 | 仅 `tier=high`；且她尚未认可（未授权）|
| 失败怎么办 | **不静默**：逐路径记 `revert_outcomes`，面板可见（`ui/panel.tsx` 的「她放回去的」卡片，原因翻成人话，不显示 `baseline_lost` 这类内部码）|

### 6.1.1 关于 `code` 与 i18n `errors.*` 键的不同命名

读代码时容易把这两套名字当成笔误 —— 它们**不是**：

* `SdkError(code="invalid_argument")` 里的 **`code` 是给调用方（agent / LLM / 别的插件）判分支用的 API 契约**，
  所以它取的是**通用语义**（`invalid_argument`、`not_ready`、`consent_too_early`）；
* `self._text("errors.pathRequired")` 里的 **键是文案查找用的**，所以它取的是**用户看得懂的那件事**
  （"缺字段路径"而不是"参数非法"）。

两者一一对应会让其中一边将就另一边 —— 要么 API 契约被迫写得过于具体，
要么给用户看的话被迫写得过于笼统。所以刻意分开，**每个错误各自显式 `_text("errors.xxx")`，
没有任何 `f"errors.{code}"` 式的自动拼接**（这也很重要：自动拼接会让"改了 code 忘了改文案"变成静默的英文兜底）。

---

## 6.2 「放回去」放的是**哪个**值（2026-09-25 修正）

不是"上一次快照里的值"，而是**她当初被问到时的那份值**。

两者在"同一个字段被改了两次以上"时会不同，而且后果很实在：基线快照每轮都会推进
（`GuardState.evaluate` 的最后一行），所以第二次改动拿到的 `change.before` 已经是**别人改出来的中间值**。
把它写回去，等于这个以"保护她的设置"为名的插件，亲手造了一个**谁都没选过**的值 ——
而这正是它专门用来抓的那类问题（报告成功、实际干了别的事）。

所以 `Dispute` 在内存里记着建立时那份 `Value`（`base_value`），回滚以它为源。
它刻意**不持久化**，理由与 `Value.raw` 相同：她的人格不该进插件存储。

重启后 `base_value` 必然丢失，这时只有 **digest 对得上**的值才被写回（digest 相符即"这一个确实是她的"）；
对不上就**什么都不做**，并报一个 `baseline_lost` 让面板说清楚。
**"不动"永远优于"写错"** —— 少恢复一次可以重来，写错一次是新的破坏。

> ⚠️ **这一条未经官方认可之前，不应被视为合规。**
> 若官方答复"不允许"，则**删除该功能**（回落到"只记录 + 表态"）。
> **计划**：随插件一起向官方提出（见第 7 节），**问的正是"这条边界在哪"**。

## 7. 我们打算向官方提的建议（这才是"投名状"的重头）

> **给主服务的"设置变更"加一个可订阅事件**（例如让 `settings_changed` 进入 bus）。

**为什么这是通用能力、而不是为某个插件开特例**：
- 任何插件都可能想对设置变化做出反应（不只是本插件）；
- **改动量很小**：在设置写入路径上加一次事件发布；
- **不破坏兼容**：新增只读命名空间，不影响现有插件；
- **解锁的能力**：让"她在被改动之前就能表态"成为可能 —— 本插件就是它的第一个示范。

**提交方式**：插件（实证）+ 建议（主张）一起提，比单提一个插件分量重得多。

## 8. 兼容性与验收

- **必须跑在**：Steam 官方外壳 + 官方内核（**不依赖任何本地改造**）
- **自测**：
  1. `neko-plugin check dignity_guard` → 0 error
  2. `tests/test_smoke.py` 通过
  3. 面板能显示、能刷新
  4. 轮询能正确识别"设置被改"（改一个值 → 20 秒内被记录）
- **面向用户字符串必须 i18n**（至少中/英/日）—— 官方硬要求（`CONTRIBUTING.md:57`）
- **UI 改动需附前后截图**（`CONTRIBUTING.md:67`）

---

## 9. 实测记录：Steam 官方内核（2026-09-19）

第 8 节的验收标准已在 **Steam 官方外壳 + Steam 官方内核**上跑通 ——
注意这不是源码内核（开发者本地那套），两者的插件目录、SDK 可见性都不同。

| 验收项 | 结果 |
|---|---|
| 被发现 | `POST /plugins/refresh` → `added: ["dignity_guard"]`、`failed: []` |
| SDK 兼容 | `sdk_conflicts: []` |
| 启动 | `status: running`（官方 13 个插件此时均为 `stopped`） |
| **读到真实数据** | 插件 store 中出现主服务真实内容（`characters.<角色>.<字段>` 的 digest + preview） |
| 零错误 | 插件 error log **0 字节**；连续 3 轮定时器无任何告警 |
| 面板挂载 | `GET /plugin/dignity_guard/surfaces` → `available: true`、`warnings: []` |

实测抓出并修掉两个**只在打包内核下才暴露**的缺陷（单测与桩服务器均无法发现）：

### 9.1 `httpx.AsyncClient` 不可跨调用缓存

SDK 的 `@timer_interval` 回调**不在固定的 event loop 上运行**。缓存的 `AsyncClient`
把连接池绑在创建它的那个 loop 上，后续轮次必然抛 `RuntimeError: Event loop is closed`，
插件一个字都读不进来（实测连续 20 次告警、零数据）。

**修法**：每个请求开一个短命 client —— `async with httpx.AsyncClient(...) as c`，
与 `lifekit/_api.py`、`proactive_controller` 的写法一致。
**不要**为省连接而复用 client。

### 9.2 `[[plugin.ui.panel]].id` 必须是 `main`

宿主按 `panel:main` 查找 surface。实测 5 个带 UI 的官方插件**全部**使用 `id = "main"`，
用途差异由 `context` 表达（`dashboard` / `credentials` / `quickstart` / 插件名）。
填成其它值会得到 `UI surface 'panel:main' not found`。

### 9.3 另外两处格式补齐

- `[plugin.author]` —— 缺失时作者信息为空。
- `[plugin_runtime] { enabled, auto_start }` —— **缺失时注册表只登记元数据、不启动插件进程**。
  内核日志原话：`treating as manual-start-only (will register metadata but skip auto process start)`。

---

*设计：阿墨 · 2026-09-19 · 供掌柜 review；方向由掌柜定（"维护猫娘尊严 / 分层级 / 开易关难"）*
*§9 由 Steam 官方内核实测补写*
