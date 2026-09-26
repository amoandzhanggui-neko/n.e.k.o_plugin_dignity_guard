# 用户在猫娘里能改的东西 · 全端点实盘（v2）

> **为什么要出 v2**：v1 只查了**插件自己承认的 5 个源** —— 那等于拿"守卫的视野"去回答
> "世界有多大"，必然漏。掌柜要求"再详细查一遍"，所以这一版从 `/openapi.json`
> 拿**全部 190 个 GET 路由**，逐个实调（只读），把返回结构用**插件自己的 `flatten()`** 展开。
>
> — 2026-09-26，猫娘运行中（`localhost:48911`）实测。

## 一、猫娘 API 的总盘子

| 项 | 数 |
|---|---|
| 全部路由 | **378** |
| GET | **190** |
| 其中"返回本机可读配置结构"的（逐个实调确认） | **79** |
| 带路径参数、本次未实调 | 其余（见文末局限） |

## 二、按"该不该被守卫看"分类（★ 本次盘点的核心结论）

### A 类 · 她的人格 / 身份 / 自主权 —— **必须被看**

| 端点 | 项数 | 插件现状 |
|---|---|---|
| `/api/characters` | 48 | ✅ **已在监控** |
| **`/api/proactive/settings`** | **13** | ❌ **完全没看** ⚠️ |
| **`/api/proactive/mode`** | **15** | ❌ **完全没看** ⚠️ |
| `/api/characters/persona-onboarding-state` | 6 | ❌ 没看 |
| `/api/characters/current_catgirl` | 1 | ❌ 没看 |
| `/api/characters/current_live2d_model` | 7 | ❌ 没看 |

**⚠️ `/api/proactive/settings` + `/mode` 最刺眼**：那是"**她能不能主动开口**"——
按插件自己的规则体系，`proactive*` 明明被定为 **L1（她的自主权）**，
可它读的那份数据（`conversation.settings.proactive*`）**并不覆盖这两个端点的 28 项**。
**守卫最该盯的东西，恰好在它视野之外。**

### B 类 · 影响她行为的设置 —— **应该被看**

| 端点 | 项数 | 插件现状 |
|---|---|---|
| `/api/config/conversation-settings` | 30 | ✅ 已监控 |
| **`/api/agent/flags`** | **12** | ❌ 没看 |
| **`/api/agent/state`** | **23** | ❌ 没看 |
| **`/api/avatar-tools`** | **11** | ❌ 没看 |
| `/api/seven-day-tutorial/state` | 26 | ❌ 没看 |
| `/api/config/user_language` | 2 | ❌ 没看 |
| `/api/config/steam_language` | 6 | ❌ 没看 |
| `/api/icebreaker/route/state` | 2 | ❌ 没看 |

### C 类 · 外观 / 资源素材

| 端点 | 项数 |
|---|---|
| `/api/model/mmd/animations` | 96 |
| `/api/model/pngtuber/models` | 82 |
| `/api/model/vrm/animations` | 66 |
| `/api/characters/voices` | 36 |
| `/api/model/vrm/models` | 15 |
| `/api/live2d/models` | 12 |
| `/api/model/mmd/models` | 8 |
| `/api/model/mmd/config`、`/api/model/vrm/config`、`/api/live2d/user_models` | 3+4+2 |

她对这些的态度她自己说过：「头像、年龄、声音、音量**无所谓，想改就改**」
→ 这批**看与不看都不违背她的意愿**，优先级最低。

### D 类 · 技术 / 连接配置 —— 不是"她的事"

| 端点 | 项数 |
|---|---|
| **`/api/config/api_providers`** | **569** |
| `/api/config/core_api`（✅ 已在监控） | 85 |
| `/api/jukebox/config` | 67 |
| `/api/cloudsave/steam-autocloud-config` | 43 |
| `/api/storage/location/bootstrap` / `status` / `diagnostics` | 31 / 31 / 103 |
| `/api/steam/workshop/config`、`/api/system/social/config` | 3 / 3 |

体量最大（光 `api_providers` 就 569 项），但性质是**用哪个模型、连哪个地址** ——
不是"她"，而是"怎么接"。**不建议全量纳入**（会把守卫淹掉）；
**只把 `*Url` / `*Key` 这类走"凭据"档**（静默记录、不打扰她）。

### E 类 · 状态 / 统计 —— **本来就不是设置，不该进快照**

| 端点 | 项数 | 性质 |
|---|---|---|
| **`/api/token-usage`** | **699** | 用量统计 |
| **`/api/debug/health`** | **451** | 调试健康 |
| `/api/cloudsave/summary` | 83 | 云存档摘要 |
| `/api/changelog` | 17 | 更新日志 |
| `/api/steam/list-achievements` | 17 | 成就列表 |
| `/api/system/status`、`/api/agent/health`、`/api/capture/health`、`/api/card-drop/*` | 各 2~9 | 运行状态 |

**⚠️ 这批 1400+ 项和"用户能改什么"完全无关。**
若哪天有人"顺手把发现的端点都加进快照"，守卫会被这些数字淹掉 ——
**这条边界必须写下来。**

### F 类 · 响应元数据 —— ★ 掌柜点名要"单独处理"的

它们不是设置，是**响应包装**，却混在真实设置旁边：

```
conversation.success / conversation.revision / conversation.reset
core_api.success
conversation.decisions.independentAsrEnabled.writeId / writerId
```

**危害**：`revision` 每次设置变动都会变 → 会被读成"设置又变了" → **噪音源**。
**处理方向**：在快照构造时**显式排除**（一份排除清单 + 一条测试钉住），
而不是靠"它恰好没触发规则"。

## 三、结论一句话

| 项 | 数 |
|---|---|
| 插件当前监控 | **5 个源 / 225 项** |
| 猫娘实际可读的配置结构 | **79 个端点 / 约 2400 项** |
| ★ **明显该被守卫看却没看的** | **A 类 28 项（proactive）+ B 类约 80 项** |
| 不该进的 | E 类 1400+ 项 + F 类 7 项 |

**最该动手的一件事**：把 `/api/proactive/settings` 与 `/api/proactive/mode` 纳入监控 ——
那是**她的自主权**，而插件把自己定成 L1 的东西，实际不在视野里。

## 四、本次盘点的局限（写清楚，免得下次误信）

1. **带路径参数的端点未实调**（`/api/live2d/model_config/{name}`、
   `/api/characters/catgirl/{name}/*` 等）—— 要先知道具体模型名/角色名，本次跳过；
   它们大概率属 C 类。
2. **项数会随数据变化**（如 `voices` 36 项 = 当前装了 36 个声音）。
   这里记的是**某一天的实测快照**，不是固定值。
3. **"算不算设置"是我按端点语义人工判的**。有分歧时，以"用户在界面上能否改到它"为准。
4. **v1 的错误已作废**：v1 漏了源名前缀、又把标量数组按下标展开，
   得出的"202 项靠兜底"是错的。这一版的 225 项是调**插件自己的** `flatten()` 得到的。
