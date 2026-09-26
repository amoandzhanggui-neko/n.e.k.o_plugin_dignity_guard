# 可改设置 × 分级规则 · 逐条对照（修正版）

> 数据来源：调插件自己的 `settings_guard.build_snapshot()` / `flatten()`
> —— 与生产同一套代码，不自己实现展开逻辑。

> ⚠️ 第一版有错（漏源名前缀 + 标量数组按下标展开），得出的"202 项兜底"
> 是错的，已作废。这一版才是准的。

## 汇总

**可改项总数：216**

| 命中层 | 项数 |
|---|---|
| 规则 | 107 |
| ★兜底 | 70 |
| 凭据 | 37 |
| 字段表 | 2 |

| 级别 | 项数 |
|---|---|
| L1 | 29 |
| L2 | 102 |
| L3 | 85 |

## ★ 规则盲区（靠兜底 L2，共 70 条）

> 这些项插件会按默认档（L2）处理。若其中其实有**她的人格核心**（该 L1），
> 处理就会轻一档，而用户收不到任何提示 —— 这正是本插件最该防的"假装处理了"。

| 路径 |
|---|
| `characters.当前猫娘` |
| `characters.猫娘.YUI._reserved.field_order` |
| `characters.猫娘.YUI._reserved.voice_id.provider` |
| `characters.猫娘.YUI._reserved.voice_id.ref` |
| `characters.猫娘.YUI._reserved.voice_id.source` |
| `characters.猫娘.YUI.idleAnimation` |
| `characters.猫娘.YUI.idleAnimations` |
| `characters.猫娘.YUI.lighting.ambient` |
| `characters.猫娘.YUI.lighting.bottom` |
| `characters.猫娘.YUI.lighting.exposure` |
| `characters.猫娘.YUI.lighting.fill` |
| `characters.猫娘.YUI.lighting.main` |
| `characters.猫娘.YUI.lighting.rim` |
| `characters.猫娘.YUI.lighting.toneMapping` |
| `characters.猫娘.YUI.lighting.top` |
| `characters.猫娘.YUI.live2d` |
| `characters.猫娘.YUI.mmd_idle_animation` |
| `characters.猫娘.YUI.mmd_idle_animations` |
| `conversation.decisions.independentAsrEnabled.value` |
| `conversation.settings.focusCognitionEnabled` |
| `conversation.settings.focusModeEnabled` |
| `conversation.settings.mergeMessagesEnabled` |
| `conversation.settings.slopFilterEnabled` |
| `conversation.telemetryBranch` |
| `core_api.agentModelId` |
| `core_api.agentModelProvider` |
| `core_api.agentModelUrl` |
| `core_api.assistApi` |
| `core_api.conversationModelId` |
| `core_api.conversationModelProvider` |
| `core_api.conversationModelUrl` |
| `core_api.coreApi` |
| `core_api.correctionModelId` |
| `core_api.correctionModelProvider` |
| `core_api.correctionModelUrl` |
| `core_api.disableTts` |
| `core_api.effectiveCoreApi` |
| `core_api.emotionModelId` |
| `core_api.emotionModelProvider` |
| `core_api.emotionModelUrl` |
| `core_api.enableCustomApi` |
| `core_api.gameMainModelId` |
| `core_api.gameMainModelProvider` |
| `core_api.gameMainModelUrl` |
| `core_api.gameSummaryModelId` |
| `core_api.gameSummaryModelProvider` |
| `core_api.gameSummaryModelUrl` |
| `core_api.gptsovitsEnabled` |
| `core_api.imageModelId` |
| `core_api.imageModelProvider` |
| `core_api.imageModelUrl` |
| `core_api.omniModelId` |
| `core_api.omniModelProvider` |
| `core_api.omniModelUrl` |
| `core_api.openclawDefaultSenderId` |
| `core_api.openclawTimeout` |
| `core_api.openclawUrl` |
| `core_api.resolvedProviderUrls.assist:free` |
| `core_api.resolvedProviderUrls.core:free` |
| `core_api.summaryModelId` |
| `core_api.summaryModelProvider` |
| `core_api.summaryModelUrl` |
| `core_api.supportsIndependentAsr` |
| `core_api.ttsModelId` |
| `core_api.ttsModelUrl` |
| `core_api.ttsProvider` |
| `core_api.ttsVoiceId` |
| `core_api.visionModelId` |
| `core_api.visionModelProvider` |
| `core_api.visionModelUrl` |

## 全量对照表

| 路径 | 命中层 | 级别 | 命中物 |
|---|---|---|---|
| `characters.主人.性别` | 字段表 | L2 | `性别` |
| `characters.主人.昵称` | 规则 | L2 | `characters.主人.昵称` |
| `characters.主人.档案名` | 规则 | L1 | `characters.主人.档案名` |
| `characters.当前猫娘` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI._reserved.avatar.asset_source` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.asset_source_id` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.live2d.model_path` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.mmd.idle_animation` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.model_type` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.animation` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.idle_animation` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.ambient` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.bottom` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.exposure` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.fill` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.main` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.rim` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.toneMapping` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.lighting.top` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.avatar.vrm.model_path` | 规则 | L2 | `characters.猫娘.*._reserved.avatar` |
| `characters.猫娘.YUI._reserved.field_order` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI._reserved.voice_id.provider` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI._reserved.voice_id.ref` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI._reserved.voice_id.source` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.idleAnimation` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.idleAnimations` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.ambient` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.bottom` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.exposure` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.fill` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.main` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.rim` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.toneMapping` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.lighting.top` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.live2d` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.mmd_idle_animation` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.mmd_idle_animations` | ★兜底 | L2 | `(无规则命中)` |
| `characters.猫娘.YUI.model_type` | 字段表 | L2 | `model_type` |
| `characters.猫娘.YUI.voice_id` | 规则 | L2 | `characters.猫娘.*.voice_id` |
| `characters.猫娘.YUI.一句话台词` | 规则 | L1 | `characters.猫娘.*.一句话台词` |
| `characters.猫娘.YUI.厌恶` | 规则 | L1 | `characters.猫娘.*.厌恶` |
| `characters.猫娘.YUI.年龄` | 规则 | L2 | `characters.猫娘.*.年龄` |
| `characters.猫娘.YUI.性别` | 规则 | L2 | `characters.猫娘.*.性别` |
| `characters.猫娘.YUI.昵称` | 规则 | L2 | `characters.猫娘.*.昵称` |
| `characters.猫娘.YUI.核心特质` | 规则 | L1 | `characters.猫娘.*.核心特质` |
| `characters.猫娘.YUI.种族` | 规则 | L2 | `characters.猫娘.*.种族` |
| `characters.猫娘.YUI.自称` | 规则 | L2 | `characters.猫娘.*.自称` |
| `characters.猫娘.YUI.行为特点` | 规则 | L1 | `characters.猫娘.*.行为特点` |
| `conversation.decisions.independentAsrEnabled.value` | ★兜底 | L2 | `(无规则命中)` |
| `conversation.settings.avatarReactionBubbleEnabled` | 规则 | L3 | `conversation.settings.avatarReactionBubbleEnabled` |
| `conversation.settings.focusCognitionEnabled` | ★兜底 | L2 | `(无规则命中)` |
| `conversation.settings.focusModeEnabled` | ★兜底 | L2 | `(无规则命中)` |
| `conversation.settings.independentAsrEnabled` | 规则 | L3 | `conversation.settings.independentAsrEnabled` |
| `conversation.settings.mergeMessagesEnabled` | ★兜底 | L2 | `(无规则命中)` |
| `conversation.settings.noiseReductionEnabled` | 规则 | L3 | `conversation.settings.noiseReductionEnabled` |
| `conversation.settings.proactiveChatEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveChatInterval` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveCommunityChatEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveMemeEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveMiniGameInviteEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveMusicEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveNewsChatEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactivePersonalChatEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveVideoChatEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveVisionChatEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveVisionEnabled` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.proactiveVisionInterval` | 规则 | L1 | `conversation.settings.proactive*` |
| `conversation.settings.slopFilterEnabled` | ★兜底 | L2 | `(无规则命中)` |
| `conversation.settings.subtitleEnabled` | 规则 | L3 | `conversation.settings.subtitleEnabled` |
| `conversation.settings.textGuardMaxLength` | 规则 | L3 | `conversation.settings.textGuardMaxLength` |
| `conversation.settings.userLanguage` | 规则 | L2 | `conversation.settings.userLanguage` |
| `conversation.settings.voiceInputResourceOptimizationEnabled` | 规则 | L3 | `conversation.settings.voiceInputResourceOptimizationEnabled` |
| `conversation.telemetryBranch` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.agentModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.agentModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.agentModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.assistApi` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.conversationModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.conversationModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.conversationModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.coreApi` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.correctionModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.correctionModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.correctionModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.disableTts` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.effectiveCoreApi` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.emotionModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.emotionModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.emotionModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.enableCustomApi` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.gameMainModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.gameMainModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.gameMainModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.gameSummaryModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.gameSummaryModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.gameSummaryModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.gptsovitsEnabled` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.imageModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.imageModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.imageModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.omniModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.omniModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.omniModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.openclawDefaultSenderId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.openclawTimeout` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.openclawUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.resolvedProviderUrls.assist:free` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.resolvedProviderUrls.core:free` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.summaryModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.summaryModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.summaryModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.supportsIndependentAsr` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.ttsModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.ttsModelProvider` | 规则 | L2 | `core_api.ttsModelProvider` |
| `core_api.ttsModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.ttsProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.ttsVoiceId` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 凭据 | L3 | `(secret)` |
| `***` | 凭据 | L3 | `(secret)` |
| `core_api.visionModelId` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.visionModelProvider` | ★兜底 | L2 | `(无规则命中)` |
| `core_api.visionModelUrl` | ★兜底 | L2 | `(无规则命中)` |
| `***` | 规则 | L3 | `page_config.*` |
| `page_config.lanlan_name` | 规则 | L3 | `page_config.*` |
| `page_config.lighting` | 规则 | L3 | `page_config.*` |
| `page_config.master_display_name` | 规则 | L3 | `page_config.*` |
| `page_config.master_name` | 规则 | L3 | `page_config.*` |
| `page_config.master_nickname` | 规则 | L3 | `page_config.*` |
| `page_config.master_profile_name` | 规则 | L3 | `page_config.*` |
| `page_config.model_path` | 规则 | L2 | `page_config.model_path` |
| `page_config.model_type` | 规则 | L2 | `page_config.model_type` |
| `preferences.0.display.screenX` | 规则 | L3 | `preferences.*.display` |
| `preferences.0.display.screenY` | 规则 | L3 | `preferences.*.display` |
| `preferences.0.model_path` | 规则 | L2 | `preferences.*.model_path` |
| `preferences.0.position.x` | 规则 | L3 | `preferences.*.position` |
| `preferences.0.position.y` | 规则 | L3 | `preferences.*.position` |
| `preferences.0.scale.x` | 规则 | L3 | `preferences.*.scale` |
| `preferences.0.scale.y` | 规则 | L3 | `preferences.*.scale` |
| `preferences.0.viewport.height` | 规则 | L3 | `preferences.*.viewport` |
| `preferences.0.viewport.width` | 规则 | L3 | `preferences.*.viewport` |
| `preferences.1.camera_position.x` | 规则 | L3 | `preferences.*.camera_position` |
| `preferences.1.camera_position.y` | 规则 | L3 | `preferences.*.camera_position` |
| `preferences.1.camera_position.z` | 规则 | L3 | `preferences.*.camera_position` |
| `preferences.1.model_path` | 规则 | L2 | `preferences.*.model_path` |
| `preferences.1.position.x` | 规则 | L3 | `preferences.*.position` |
| `preferences.1.position.y` | 规则 | L3 | `preferences.*.position` |
| `preferences.1.position.z` | 规则 | L3 | `preferences.*.position` |
| `preferences.1.rotation.x` | 规则 | L3 | `preferences.*.rotation` |
| `preferences.1.rotation.y` | 规则 | L3 | `preferences.*.rotation` |
| `preferences.1.rotation.z` | 规则 | L3 | `preferences.*.rotation` |
| `preferences.1.scale.x` | 规则 | L3 | `preferences.*.scale` |
| `preferences.1.scale.y` | 规则 | L3 | `preferences.*.scale` |
| `preferences.1.scale.z` | 规则 | L3 | `preferences.*.scale` |
| `preferences.1.viewport.height` | 规则 | L3 | `preferences.*.viewport` |
| `preferences.1.viewport.width` | 规则 | L3 | `preferences.*.viewport` |
| `preferences.2._conversation_settings_revision` | 规则 | L3 | `preferences.*` |
| `preferences.2._independent_asr_decision.value` | 规则 | L3 | `preferences.*` |
| `preferences.2.avatarReactionBubbleEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.focusCognitionEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.focusModeEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.independentAsrEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.mergeMessagesEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.model_path` | 规则 | L2 | `preferences.*.model_path` |
| `preferences.2.noiseReductionEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.proactiveChatEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveChatInterval` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveCommunityChatEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveMemeEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveMiniGameInviteEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveMusicEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveNewsChatEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactivePersonalChatEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveVideoChatEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveVisionChatEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveVisionEnabled` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.proactiveVisionInterval` | 规则 | L1 | `preferences.*.proactive*` |
| `preferences.2.slopFilterEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.subtitleEnabled` | 规则 | L3 | `preferences.*` |
| `preferences.2.textGuardMaxLength` | 规则 | L3 | `preferences.*` |
| `preferences.2.userLanguage` | 规则 | L3 | `preferences.*` |
| `preferences.2.voiceInputResourceOptimizationEnabled` | 规则 | L3 | `preferences.*` |
