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

- **U1** C-REV-1 的 `notified`：**未验证**（ASSERTED_FIELDS 不含该字段）
- **U2** C-REV-2 的 `block_reported`：**未验证**（该路径不产生 blocked，恒为 null → 按 v2 第 7 条进未证实项）

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

### ⚠ 契约与实现的字段差异（2026-10-25 查实，必须记住）

本表声明的 7 个 out 字段里，**只有 3 个在实现中存在**：

| 声明字段 | 实现 | 说明 |
|---|---|---|
| `error` | **无** | 靠返回值/异常，没有这个字段 |
| `revert` | ✅ `Evaluation.reverted` / `to_revert` | 名字不同，语义对应 |
| `source` | **无独立字段** | 只有内部方法 `_revert_source` |
| `blocked` | ⚠️ `Evaluation.revert_blocked` | 名字不同，且是**内部状态**不是对外 payload |
| `block_reported` | **无** | 契约要求"必须有对外出口"，未实现 |
| `notified` | **无** | 未实现 |
| `status_after` | **无** | 未实现 |

⟹ **C-REV-1 / C-REV-2 目前是「部分实现」**，交付三分类仍应记「未实现」，
直到对外 payload 补齐、或本表按实现改写。
`tests/test_rev_contract.py` 里有一条 `test_rev_contract_missing_out_fields_are_still_missing`
专门把这个缺口钉死：将来补了字段，它会失败，逼人回来更新这张表。

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
