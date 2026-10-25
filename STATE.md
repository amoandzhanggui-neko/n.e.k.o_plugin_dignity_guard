# STATE —— 现在到底能不能跑

> 本文件由 `state.sh` 生成（2026-10-25 12:25），**禁止手改**。
> 手写的文档会和代码漂移；命令生成的不一样，它只能反映真实。
> 唯一铁律：跑不出来就写「跑不了」，不写历史数字。

## 1. 代码

| 项 | 值 |
|---|---|
| 最新提交 | 52cbbd9 2026-10-25 12:20:38 +0800 入库流程 v2 裁决表（2026-10-20）：7 条提案三方会审结果。此前 5 天未跟踪，新会话看不到该流程存在 |
| 未提交 | 2 个　（已跟踪修改 2 个： 2 files changed, 54 insertions(+), 24 deletions(-) ｜ 未跟踪新文件 0 个） |
| 未推送 | 7 个提交 |

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

## 4. 运行时（插件在宿主里跑没跑）

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

## 5. 下一步

一条就好，必须带判据。当前判据：**0 failed、0 error**。
