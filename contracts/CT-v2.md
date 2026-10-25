# CT-v2 契约表（在 CT-v1 基础上加「允许态 + when」）

> **为什么升版本**：CT-v1 的 `out:` 只是一串字段名，看不出"这个字段是不是必须有值"。
> 于是运行时实测出来一个 `null`，没人知道那算允许还是算漏——**判不了红，只能靠人吵**。
> CT-v2 只做一件事：把每个 out 字段的**允许态**和**生效条件**写进契约，让机器能判。
>
> **与 CT-v1 的关系**：语义完全一致，**只补格式，不改行为**。CT-v1 保留不删（冻结版原样留档）。
> 升版依据：skill §2.5 第 5 条「字段格式定死：`名:类型:{present|null|missing}:when=<谓词>`」。
> 升版时间：2026-10-25

---

## 判等规则（沿用 CT-v1，未改）

1. schema 归一：`{error, revert, source, blocked, block_reported, notified, status_after}`
2. 比对前**双向剔除 null 键**，再断言「键集合 == ASSERTED_FIELDS」+「逐键相等」
3. 未列入 ASSERTED_FIELDS 的字段，视为**未校验**，必须写进交付说明的「未证实项」
4. **禁止**用「不等于坏样本」代替「等于正确行为」

### v2 新增三条（这是升级的全部意义）

5. **允许态**：每个 out 字段必须声明它允许出现哪几种"态"，取值只有三种——
   - `present`：**必须有值**（缺了 ⟹ 红）
   - `null`：**允许是 null**
   - `missing`：**允许整个键不存在**
   实测态 ∉ 声明允许态 ⟹ **红**（不是黄、不是记一笔，是红）
6. **when 谓词**：字段在什么条件下才要求出现。写 `always` 表示无条件。
   **`when` 解析失败 ⟹ 按最严处理**（当作无条件必现），不许"解析不了就放过"。
7. **声明含 `null` 且实测 `null`** ⟹ 允许，但该字段必须进「未证实项」并具名——
   因为它到底该不该有值，现在没人知道。

---

## C-REV-1 单次改动

**in**

| 字段 | 类型 | 说明 |
|---|---|---|
| tier | str | 当前档位 |
| change | obj | `{path, before, after, level}` |
| now | num | 当前时间戳 |
| ledger | obj | 台账 |

**out（允许态 + when）**

| 字段 | 类型 | 允许态 | when | 说明 |
|---|---|---|---|---|
| error | str\|null | `{null}` | `always` | 只在异常路径有值；正常必须是 null |
| revert | bool | `{present}` | `always` | 是否已回滚 |
| source | str\|null | `{null,present}` | `always` | 回滚来源字段路径；未回滚时为 null |
| blocked | bool | `{present}` | `always` | 是否被拦 |
| block_reported | bool\|null | `{present,null}` | `always` | 拦了必须有出口；未进 blocked 分支时为 null |
| notified | bool\|null | `{present,null}` | `always` | **未证实，见 U1** |
| status_after | str\|null | `{present,null}` | `always` | 改动后的状态 |

- **ASSERTED_FIELDS**（正例）：`[error, revert, source, blocked]`
- **正例**：`in_high.json → out_high.json`
  期望 `{"error":null,"revert":true,"source":"characters.猫娘.昵称","blocked":false}`
- **反例**：`bad_no_base.in.json → bad_no_base.out.json`（想要回滚但无可信 base）
  期望 `{"error":null,"revert":false,"source":null,"blocked":true,"block_reported":true}`
  - 正向断言：`blocked is True and block_reported is True and revert is False and source is None`
  - 只把状态塞进 `revert_blocked`、不给上层可见出口 ⟹ `block_reported=false` ⟹ **判红**
- **depends**：`[]`

**v2 判红**：正例实测里 `error` 若出现非 null 值 ⟹ 红（`allowed={null}`）；
`revert`/`blocked` 若整个键缺失 ⟹ 红（`allowed={present}`）。

---

## C-REV-2 同 path 第二次改动

**in**

| 字段 | 类型 | 说明 |
|---|---|---|
| tier | str | 当前档位 |
| change | obj | `{path, before, after, level}` |
| prior | obj | `{status, granted_at, expires_at}` |
| now | num | 当前时间戳 |

**out（允许态 + when）**

| 字段 | 类型 | 允许态 | when | 说明 |
|---|---|---|---|---|
| error | str\|null | `{null}` | `always` | 正常必须是 null |
| revert | bool | `{present}` | `always` | |
| source | str\|null | `{null,present}` | `always` | |
| blocked | bool | `{present}` | `always` | |
| block_reported | bool\|null | `{present,null}` | `always` | **该路径不产生 blocked，恒为 null，见 U2** |
| notified | bool\|null | `{present,null}` | `always` | |
| status_after | str | `{present}` | `always` | **v2 收紧了**：v1 允许 null，v2 要求必有值 |

- **ASSERTED_FIELDS**（正/反同集合）：`[error, revert, source, blocked, status_after, notified]`
- **正例**（TTL 内，她此前 accepted）：`in_second_in_ttl.json → out_second_in_ttl.json`
  期望 `{"error":null,"revert":false,"source":null,"blocked":false,"status_after":"accepted"}`
- **反例**（TTL 已过，应重问）：`bad_second_edit.in.json → bad_second_edit.out.json`
  期望 `{"error":null,"revert":false,"source":null,"blocked":false,"status_after":"pending"}`
  - **★ 必须在独立用例中跑**：单次改动全覆盖也发现不了「第二次改动把她此前的反对降级回 pending」
- **depends**：`[C-REV-1]`

**v2 判红**：`status_after` 若缺失或是 null ⟹ 红（v1 允许、v2 不允许——这是本次升级唯一一处语义收紧，
已列入交付说明；如不同意，改回 `{present,null}` 即可，但要书面说明理由）。

---

## 未证实项（交付说明必须逐条列出 + 具名）

- **U1** C-REV-1 的 `notified`：**字段已存在（2026-10-25 晚），但"送达"仍是弱结论**。
  单元测试验证了 `_delivered()` 对四种回执形状的判定，**没有**验证"宿主真的把消息
  交到了用户眼前"——插件看不到用户的屏幕。所以 `notified=true` 只等于"宿主收下了"。
- **U2** C-REV-2 的 `block_reported`：**该路径不产生 blocked，恒为 null**（设计如此，
  按 v2 第 7 条进未证实项）。它不是"没做"，是"这条路走不到"。
- **U3（新增 2026-10-25 晚）** 三个新字段 `block_reported` / `notified` / `status_after`
  **只有单元级验证，没有端到端验证**。缺的那条实验很具体：

  ```
  启动插件 → 让一项 L1 设置在她声明之后被改 → 高档位 → 制造"证明不了原值还是她的"
  → 断言 summary["block_reported"] is True 且 summary["status_after"] == "blocked"
  ```

  现在做的只是把 `RoundOutcome` / `_state_after` / `_delivered` 三块单独测通；
  **"它们真的被填上值"这件事，还没有任何一条测试证明过**。
  在跑通上面那条之前，对外只能说"字段有了"，不能说"效果验证过了"。

---

## 执行

```bash
# 必须从宿主仓库跑（2026-10-25 实测：在插件仓库里跑会把根 __init__.py 当测试收集，全 error）
cd N.E.K.O && python -m pytest plugin/plugins/dignity_guard/tests/ -q -k "rev"
```

**文件位置**：`tests/test_rev_contract.py`（**不是** `tests/contract/`）。
2026-10-25 实测：放 `tests/contract/` 子目录时，单独指定该路径会撞宿主 `tests/conftest.py` 的依赖，
且 `pytest tests/` 收集总数不变（155，不含新文件）⟹ 移回 `tests/` 平级后总数 155→162、`-k rev` 20 passed。

通过 = 输出与断言**逐字段相等**且退出码 0。「应当报错」不采信。

### 契约与实现的字段对照（2026-10-25 晚更新：缺的三个已补）

白天查实：7 个 out 字段里只有 3 个在实现中存在，`block_reported` / `notified` /
`status_after` **全代码库 0 命中**。**当晚已补齐**，现状如下：

| 声明字段 | 实现位置 | 说明 |
|---|---|---|
| `error` | **无独立字段（刻意不加）** | 见下方说明 |
| `revert` | ✅ `RoundOutcome.put_back` → `summary["revert"]` (bool) | **本轮**是否真的放回去至少一项 |
| `source` | **无独立字段（刻意不加）** | 见下方说明 |
| `blocked` | ✅ `RoundOutcome.blocked` → `summary["blocked"]` (bool) | **本轮**有放不回去的；此前只有内部状态 `revert_blocked` |
| `block_reported` | ✅ `RoundOutcome.blocked_reported` → `summary["block_reported"]` | 有拦住 ⟹ 那条通知有没有递到；**没拦住 ⟹ `null`**（不写成 false） |
| `notified` | ✅ `RoundOutcome.notice_delivered` → `summary["notified"]` | 有争议 ⟹ 通知有没有递到；**没争议 ⟹ `null`** |
| `status_after` | ✅ `_state_after()` → `summary["status_after"]` | 六值见下 |

**`status_after` 的六个取值**（全部由真实状态推出，不是挑标签）：

| 值 | 条件 |
|---|---|
| `blocked` | 有改动她要求放回、但证明不了原值还是她的 |
| `pending` | 还有未获认可的改动挂着，等她表态 |
| `reverted` | 本轮真的把东西放回去了 |
| `revoked` | 她自己把改动撤回了（不是我们动的） |
| `accepted` | 本轮她认可了至少一项 |
| `unchanged` | 本轮什么都没有（**也必须有个名字**，v2 要求恒有值） |

优先级按"用户有多需要知道"排：出事 > 等她表态 > 已修复 > 她改主意 > 她认了 > 无事。

**`error` 和 `source` 为什么故意不补**（不是漏了）：

- `error`：实现里用「返回值 / 异常 / `last_error`」表达失败。再加一个 `error` 字符串字段，
  会诱导调用方去读字符串、而不是读错误码 —— 那就是"同一件事两套口径"。
- `source`：`_revert_source()` 是**一个推导过程**（证明这个值还是不是她的），
  不是一个字符串标签。把它压成一个字符串，等于把推导降级成标签。

⟹ **C-REV-1 / C-REV-2 现在的分类是「已实现（单元级），端到端未证实」**，
未证实项记为 **U3**。不写"完全已实现"，因为没有任何一条测试真正启动插件、
制造一次回滚失败、再从 `summary` 里读出 `block_reported=true`。

`tests/test_rev_contract.py` 里那条旧测试（原来叫
`test_rev_contract_missing_out_fields_are_still_missing`）**已翻面**成
`test_rev_contract_out_fields_are_no_longer_missing`：它现在保证字段不会悄悄复发丢失，
并且额外断言这三个字段**不许**挪进 `Evaluation`（判断与行动是两个层次）。

---

## v1 → v2 变更摘要（给会审用，别让人以为偷偷改了行为）

| 项 | CT-v1 | CT-v2 | 性质 |
|---|---|---|---|
| out 字段格式 | 裸字段名列表 | 表格：类型 / 允许态 / when / 说明 | **格式** |
| 允许态 | 无（靠人读） | `present` / `null` / `missing` | **新增判据** |
| when 谓词 | 无 | `always` 起步；解析失败按最严 | **新增判据** |
| C-REV-1 语义 | — | 不变 | — |
| C-REV-2 `status_after` | 未声明 | `{present}`（收紧） | **⚠ 语义变化，唯一一处** |
| 正例/反例/ASSERTED_FIELDS | — | 全部不变 | — |
