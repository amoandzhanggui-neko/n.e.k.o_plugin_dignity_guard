# CT-v1 契约表（冻结版）

> 冻结时间：2026-10-18　　冻结依据：主审 glm5.3 / 二审 kimi k2.7 code / 红队 DeepSeek 三家收敛
> 止损口径：本表冻结后**不再逐条打补丁**；发现语义问题回第 3 步重定契约（升 CT-v2）。

## 判等规则

1. schema 归一：`{error, revert, source, blocked, block_reported, notified, status_after}`
2. 比对前**双向剔除 null 键**，再断言「键集合 == ASSERTED_FIELDS」+「逐键相等」
3. 未列入 ASSERTED_FIELDS 的字段，视为**未校验**，必须写进交付说明的「未证实项」
4. **禁止**用「不等于坏样本」代替「等于正确行为」

---

## C-REV-1 单次改动

```
C-REV-1 | in: {tier, change:{path,before,after,level}, now, ledger}
        | out: {error,revert,source,blocked,block_reported,notified,status_after}
        | 正例: in_high.json → out_high.json
        | 反例: bad_no_base.in.json → bad_no_base.out.json
        | depends: []
```

- **ASSERTED_FIELDS**（正例）：`[error, revert, source, blocked]`
- 正例期望：`{"error":null,"revert":true,"source":"characters.猫娘.昵称","blocked":false}`
- **反例**（想要回滚但无可信 base）：`{"error":null,"revert":false,"source":null,"blocked":true,"block_reported":true}`
  - 正向断言：`blocked is True and block_reported is True and revert is False and source is None`
  - 只把状态塞进 `revert_blocked`、不给上层可见出口 ⟹ `block_reported=false` ⟹ 判红

## C-REV-2 同 path 第二次改动

```
C-REV-2 | in: {tier, change:{path,before,after,level}, prior:{status, granted_at, expires_at}, now}
        | out: {error,revert,source,blocked,block_reported,notified,status_after}
        | 正例: in_second_in_ttl.json → out_second_in_ttl.json
        | 反例: bad_second_edit.in.json → bad_second_edit.out.json
        | depends: [C-REV-1]
```

- **ASSERTED_FIELDS**（正/反同集合）：`[error, revert, source, blocked, status_after, notified]`
- 正例（TTL 内，她此前 accepted）：`{"error":null,"revert":false,"source":null,"blocked":false,"status_after":"accepted","notified":false}`
- 反例（TTL 已过，应重问）：`{"error":null,"revert":false,"source":null,"blocked":false,"status_after":"pending","notified":true}`，附 `notify_count==1`（aux，不入 schema）
- **★ 必须在独立用例中跑**：单次改动全覆盖也发现不了「第二次改动把她此前的反对降级回 pending」

执行：

```bash
pytest tests/contract/test_rev_contract.py -q -k "rev1 or rev2"
```

通过 = 输出与 `.out.json` 逐字段相等且退出码 0。「应当报错」不采信。

---

## 未证实项（交付说明必须逐条列出 + 具名）

- **U1** 单次改动是否通知她（`notified`）：**未验证**（C-REV-1 的 ASSERTED_FIELDS 不含该字段）
- **U2** C-REV-2 的 `block_reported`：**未验证**（该路径不产生 blocked，恒为 null）
