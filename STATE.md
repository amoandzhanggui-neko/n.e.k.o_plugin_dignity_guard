# STATE —— 现在到底能不能跑

> 本文件由 `state.sh` 生成（2026-10-25 12:00），**禁止手改**。
> 手写的文档会和代码漂移；命令生成的不一样，它只能反映真实。
> 唯一铁律：跑不出来就写「跑不了」，不写历史数字。

## 1. 代码

| 项 | 值 |
|---|---|
| 最新提交 | da312e4 2026-10-18 16:45:19 +0800 chore: 移除演练裁决表（drill 不入库）+ .gitignore 拦截演练产物 |
| 未提交 | 5 个　（已跟踪修改 1 个： 1 file changed, 59 insertions(+), 9 deletions(-) ｜ 未跟踪新文件 4 个） |
| 未推送 | 2 个提交 |

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

## 4. 下一步

一条就好，必须带判据。当前判据：**0 failed、0 error**。
