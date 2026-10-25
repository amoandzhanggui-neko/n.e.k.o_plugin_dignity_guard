# STATE —— 现在到底能不能跑

> 本文件由 `state.sh` 生成（2026-10-25 16:19），**禁止手改**。
> 手写的文档会和代码漂移；命令生成的不一样，它只能反映真实。
> 唯一铁律：跑不出来就写「跑不了」，不写历史数字。

## 1. 代码

| 项 | 值 |
|---|---|
| 最新提交 | cb6982d 2026-10-25 15:47:50 +0800 登记第5轮台账（链0f09e16f47d04a16，49受控文件，新哈希口径） |
| 未提交 | 8 个　（已跟踪修改 8 个： 8 files changed, 526 insertions(+), 327 deletions(-) ｜ 未跟踪新文件 0 个） |
| 未推送 | 0 个提交 |

## 2. 源仓库 ↔ 宿主副本

C:/Users/Administrator/.workbuddy/neko_test/N.E.K.O/plugin/plugins/dignity_guard　⟹ 与源仓库 一致

## 3. 测试

```
cd N.E.K.O && python -m pytest plugin/plugins/dignity_guard/tests -q
```

**结果：155 passed, 1 warning in 208.07s (0:03:28)　←（缓存，非本次实跑）**

测于：2026-10-25 11:54

> 为什么必须在 N.E.K.O 里跑：插件根目录的 `__init__.py` 就是包入口，
> 直接在插件仓库里跑 pytest 会把它当成测试模块收集，直接
> `ImportError: attempted relative import with no known parent package`，
> 155 个用例全部 error。这是 2026-10-25 实测出来的，不是猜的。

## 4. 装配与接缝（结构接得上吗）

| 项 | 结果 |
|---|---|
| 装配检查（导入 / 注册 / 对账） | [√] 装配检查通过 |
| 接缝快照（契约接口有没有变） | [√] 接缝接口与上一轮一致（hash=62c699c1ff8b69bc） |
| 全部判定（动态 key / 接缝私约 / AST / 三键 / 契约改动次数 / 三零同现） | [√] 全部判定通过（1 条黄，不阻断） |
| 效果逻辑图锚点（完工定义第 11 条） | [√] 24 个锚点全部有效 —— 图里引用的符号都还在 |

判据：**装配通过 + 接缝一致**，才算结构没问题。

- 装配检查验的是「代码结构接得上、源码声明的入口与宿主实际注册的一致」。
  **它不含真启动**（真启动要宿主注入 ctx，脱离宿主造不出来）—— 真启动看第 5 节。
- 接缝快照验的是「契约声明的 out 字段有没有被悄悄改」，对应判红 2（允许态 / when）和判红 5（seam_id 必须派生）。

## 5. 运行时（插件在宿主里跑没跑）

| 证据 | 值 |
|---|---|
| 最新记忆备份 | 20261025-112859 |
| data/store.db 修改时间 | 2026-10-25 12:12 |
| 插件日志行数 / 文件时间 | 11 / 2026-10-25 11:28 |
| error.log 里 dignity 相关 | 0 条 |

**插件日志最后一条：**

    2026-10-25 11:28:59 - N.E.K.O.Plugin_dignity_guard.plugin.dignity_guard - INFO - dignity_guard: memory backup 20261025-112859 (22 file(s), 1 pruned, b

判据：**插件日志有今天的记录 ⟹ 启动过；data 有当天写入 ⟹ 启动后一直在干活。**
`error.log` 里 dignity 相关 > 0 就必须去看。

> 坑（2026-10-25 踩了两次，都记下来）：
> `AppData/Roaming/N.E.K.O/neko-electron-debug.log` 是 **Electron 前端**日志，
> 里面永远查不到 dignity（今天 6553 条里 0 条）。拿它判断插件会得出完全相反的结论。
> Python 插件日志在 `AppData/Local/N.E.K.O/logs/plugin/<插件名>_<日期>.log`，
> 依据是宿主 `plugin/logging_config.py` 自己写的"日志自动落到 N.E.K.O/logs/"。
> 另外：插件自身**不配日志**（`plugin.toml` 无 log 项、`__init__.py` 无 `import logging`），
> 级别由宿主环境变量 `NEKO_LOG_LEVEL` 统一管（TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL），
> 且仓库禁止引入 loguru（CI 有 `scripts/check_no_loguru.py` 守门）。

## 6. 下一步

一条就好，必须带判据。当前判据：**0 failed、0 error**。
