#!/usr/bin/env node
'use strict';
/**
 * 装配检查（工序 7 的产物）——带 30 秒硬超时
 *
 * 为什么要有这一层：
 *   Python 的 import 是同步的，跑起来就中断不了，自己实现的超时都是假的。
 *   **硬超时只能由父进程保证** —— spawnSync 的 timeout 会真的把子进程 kill 掉。
 *   这也是 skill 工序 7 里「30s 硬超时、超时即 fail、不重试」的唯一可靠落法。
 *
 * 用法：
 *   node assembly_check.js
 *   node assembly_check.js --host <宿主路径>
 *
 * 退出码：0 = 三阶段全过；1 = 失败或超时
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const PY_SCRIPT = path.join(ROOT, 'assembly_check.py');
const HARD_TIMEOUT_MS = 30000;

// 选 python：必须跳过那个 _ssl 坏掉的 3.13.12.old.240 残留目录
// （2026-10-25 实测：PATH 第一条就是它，_ssl DLL 缺失 ⟹ 什么都 import 不了）
function pickPython() {
  const cands = [
    'C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe',
    process.env.PYTHON,
    'python',
    'python3',
  ].filter(Boolean);
  for (const c of cands) {
    const r = spawnSync(c, ['-c', 'import ssl'], { encoding: 'utf8', timeout: 8000, windowsHide: true });
    if (r.status === 0) return c;
  }
  return null;
}

const py = pickPython();
if (!py) {
  console.log('[x] 阶段0 找不到可用的 python（要求能 import ssl）');
  console.log('    提示：PATH 里排第一的 python 可能是 3.13.12.old.240（_ssl 缺失），要用 3.13.12 那个');
  process.exit(1);
}

const args = [PY_SCRIPT];
const hi = process.argv.indexOf('--host');
if (hi > -1 && process.argv[hi + 1]) args.push('--host', process.argv[hi + 1]);

const t0 = Date.now();
const r = spawnSync(py, args, {
  encoding: 'utf8',
  timeout: HARD_TIMEOUT_MS,   // ← 真硬超时：超时会 kill
  killSignal: 'SIGKILL',
  windowsHide: true,
  cwd: ROOT,
});
const ms = Date.now() - t0;

if (r.error && r.error.code === 'ETIMEDOUT') {
  console.log(`[x] 装配检查超时（${HARD_TIMEOUT_MS / 1000}s 硬顶）——按环境问题处理，不重试`);
  console.log('    这通常不是代码问题：先看机器是不是被打满（例如看板/测试并发）、或 python 卡在 import。');
  process.exit(1);
}
if (r.stdout) process.stdout.write(r.stdout);
if (r.stderr) process.stderr.write(r.stderr);

console.log(`\n用时 ${(ms / 1000).toFixed(1)}s（硬顶 ${HARD_TIMEOUT_MS / 1000}s）`);
process.exit(r.status === null ? 1 : r.status);
