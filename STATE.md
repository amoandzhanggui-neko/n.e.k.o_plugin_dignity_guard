# STATE —— 现在到底能不能跑

> 本文件由 `state.sh` 生成（2026-10-25 12:16），**禁止手改**。
> 手写的文档会和代码漂移；命令生成的不一样，它只能反映真实。
> 唯一铁律：跑不出来就写「跑不了」，不写历史数字。

## 1. 代码

| 项 | 值 |
|---|---|
| 最新提交 | 91075a8 2026-10-25 12:16:21 +0800 STATE.md 增运行时一节：判断插件活没活看 data 目录，不看日志（今天实测日志 6553 条里 0 条 dignity，但 data 有当天备份） |
| 未提交 | 1 个　（已跟踪修改 0 个：无 ｜ 未跟踪新文件 1 个） |
| 未推送 | 5 个提交 |

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
| 宿主日志今日总条数 / 其中 dignity | 6553 / 0 |

判据：**备份目录或 store.db 有当天更新 ⟹ 插件进程活着并在干活。**

> 陷阱（2026-10-25 实测）：日志里 dignity 记录只有 0 条，但 data 有当天写入。
> **「日志没记」≠「没在跑」** —— 判断活着没有看 data 目录，判断出了什么错才看日志。

## 5. 下一步

一条就好，必须带判据。当前判据：**0 failed、0 error**。
