# CT-v1 过程裁决表（process-only，不得用于放行）

CONTRACT=CT-v1 contracts/CT-v1.md e52ad604f81dc2d6
SCOPE=process-only（闸 v2 + CT-v1 文本；不含 __init__.py 业务语义）
BASE=4c4e2be

| id | sev | 裁决 | 锚点（原话/产物） | 可重跑证据 |
| M1 | P0 | 采纳 | 红队 S7/S8：逻辑搬家 + 改闸 | `--files` 列 34 项含闸自身 |
| M2 | P0 | 采纳 | 红队 S23：docs 藏脚本 | `scriptsInDocDirs=0` |
| M3 | P1 | 采纳 | 主审 D1：演练轮曾可作基线 | `lastReviewRound()`；`--check=1` |
| M4 | P2 | 采纳 | 主审 D2：.bak 卡 dirty | `git status` 空 |
| M5 | P0 | 采纳 | 主审 CT-v1 缺陷 D1~D3 / E1~E2 | `contracts/CT-v1.md` |
| M6 | P0 | **未决** | `__init__.py` 业务语义：本会话无行级锚点 | 无 ⟹ 阻塞放行 |

未证实项：
- U1 单次改动是否通知她（notified）：未验证（C-REV-1 正例不校验）
- U2 C-REV-2 的 block_reported 恒为 null：未验证（该路径不产生 blocked）

接受人：（空）
结论：**不构成放行**。本表只证明"闸链路可跑"，不证明 34 个受控文件的业务语义被审过。

<!-- GATE-FILES 2026-10-18T08:28:30 -->
.github/workflows/release.yml 1d98a9cf2e7b61df
.github/workflows/verify.yml a2be669364f2bc13
.gitignore 6b4483ac43c8a3a9
.vscode/settings.json 024876b21291d898
.vscode/tasks.json 242f7e79b7c4f1b4
__init__.py 6bed884b9efe7f59
config.example.toml b08196159654d2c9
contracts/CT-v1.md e52ad604f81dc2d6
diagnostics.py f74e354c9ffde92f
feedback.py df33f4d52fa39c7b
i18n/en.json 9332ee7d6c11c068
i18n/ja.json 18c39705e604f004
i18n/zh-CN.json 66e09696ba85409d
main_server_client.py 21604ae315641de2
memory_backup.py 284f7b3d0bd20032
memory_guard.py 3a1bc79c4b5b25db
neko_review_gate.js fb7af3d1830ce405
plugin.toml 73fb20e128248ff9
pyproject.toml 584f64fd70dc3427
pytest.ini e948d708a0343933
ruff.toml b4303b147c481bc7
settings_guard.py e186e95073231f4c
tests/conftest.py 055e28dc1f2f5305
tests/test_change_detection_live.py a09598174d2e7165
tests/test_diagnostics.py 8293ac9e9e94826f
tests/test_exclusion.py 171905deee6ac9de
tests/test_feedback.py 365a0acc65a5e555
tests/test_guard_tiers.py 9cd1d6b9b2860c16
tests/test_memory_guard.py a5e1fdff51a8f540
tests/test_plugin_runtime.py f9754119db79184f
tests/test_settings_guard.py d732f1b24fe50fcc
tests/test_smoke.py 3230b2d2cda79a66
ui/onboarding.tsx 90c5df131c461cc1
ui/panel.tsx 6203f9b99fc28073
<!-- /GATE-FILES -->
