#!/usr/bin/env node
/**
 * neko_review_gate.js —— 猫娘插件「改完必会审」机器闸 v2
 *
 * v1 (2026-10-18 早) 被红队三击打穿，v2 针对性堵洞：
 *   S7 逻辑搬家（把实现搬进新文件，核心文件只加一行 import）→ 受控集改为「全量 git 跟踪的代码文件」，
 *      不再手写白名单；新增未跟踪代码文件直接红（S17）。
 *   S8 闸自改（改掉 CORE 数组删文件）→ 闸自身纳入受控集，改自己同样红。
 *   S16 扩展名白名单 → 不再按扩展名猜，按「git 跟踪 + 排除非运行物」判定。
 *   台账可手写（S6）→ 加 prev_hash 链，篡改会断链（本地可重算，属残余风险，已在 skill 记为已知边界）。
 *   S3 stamp 无脑过 → 裁决表必须内嵌 `GATE-FILES` 证据块，逐项比对当前哈希才准登记。
 *   S1/S2 源与宿主副本漂移 → --check 内嵌 sync。
 *
 * 用法：
 *   node neko_review_gate.js                 # 检查（含 sync）；0=绿可交付 / 1=红
 *   node neko_review_gate.js --status        # 台账（含链）
 *   node neko_review_gate.js --files         # 受控文件与哈希（可直接粘进裁决表）
 *   node neko_review_gate.js --evidence      # 打印 GATE-FILES 证据块
 *   node neko_review_gate.js --stamp <裁决表.md> [说明]
 *   node neko_review_gate.js --sync          # 单独看源/宿主副本一致性
 *   node neko_review_gate.js --selftest      # 负向对照：注入改动必须变红
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execSync } = require('child_process');

const ROOT = __dirname;
const LEDGER = path.join(ROOT, 'docs', 'review', 'review_ledger.json');
const LEDGER_REL = 'docs/review/review_ledger.json';
const GATE_SELF = path.basename(__filename);
const HOST_COPY = path.join(ROOT, '..', 'N.E.K.O', 'plugin', 'plugins', 'dignity_guard');

// 证1（default-deny）：目录递归全收，只有**文档类型**放行。
// ⚠️ 不按目录放行（红队 S23：docs/xxx.py + sys.path.append 即可绕过），
//    所以 docs/ 里的 .py 照样受控；只有 *.md / *.log / *.bak 这类非可执行物放行。
const EXCLUDE_DIRS = ['__pycache__', '.git', 'node_modules', '.pytest_cache', '.mypy_cache'];
const DOC_WHITELIST = /\.(md|log)$|\.bak[^/]*$/i;

function sha16(p) {
  try {
    // ★ 2026-10-25 修：**先归一化行尾再算哈希**，不直接拿工作区字节去算。
    //
    // 为什么必须改（实测，不是推测）：
    //   仓库存的是 LF（.gitattributes 保证），但 Windows 上 core.autocrlf=true，
    //   而且在加 .gitattributes **之前** checkout 过的文件，工作区至今还是 CRLF。
    //   于是同一个仓库里两种行尾并存：__init__.py 工作区 CRLF、CT-v2.md 是 LF。
    //   直接读字节算哈希 ⟹ __init__.py 得 d2d4c25d…，而 git blob 是 80f06b9c…，
    //   **同一份代码两个哈希**。台账里存的又是工作区哈希 ⟹ 换台机器全部对不上，
    //   判红 7 会把"什么都没改"报成"全改了"，然后被人当噪声关掉。
    //
    // 归一化到 LF 之后：工作区怎么变都不影响结果，且与 git blob 一致。
    // 用 latin1 是为了逐字节保真（二进制文件不能被 UTF-8 解码损坏）。
    const raw = fs.readFileSync(p).toString('latin1').replace(/\r\n/g, '\n');
    return crypto.createHash('sha256').update(Buffer.from(raw, 'latin1')).digest('hex').slice(0, 16);
  } catch (e) {
    return null;
  }
}

function git(args) {
  try {
    return execSync('git ' + args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  } catch (e) {
    return '';
  }
}

// ── 受控集：目录递归全收（default-deny），只白名单放文档 ──
function walk(dir, rel, out) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return;
  }
  entries.forEach(function (ent) {
    const r = rel ? rel + '/' + ent.name : ent.name;
    if (ent.isDirectory()) {
      if (EXCLUDE_DIRS.indexOf(ent.name) !== -1) return;
      walk(path.join(dir, ent.name), r, out);
    } else if (ent.isFile()) {
      // 台账是闸自己的产物：若把它纳入受控集，写台账 → 哈希变 → 永久红（自指死循环）
      if (r === LEDGER_REL) return;
      // 文档放行，但 contracts/ 下的 .md 例外 —— 那是契约，必须受控
      if (DOC_WHITELIST.test(ent.name) && !/^contracts\//.test(r)) return;
      out.push(r);
    }
  });
}

function controlledFiles() {
  const out = [];
  walk(ROOT, '', out);
  out.sort();
  return out;
}

// 证1 硬判（红队 S23）：**docs/ 里**出现可执行脚本 ⟹ 红。
// docs/ 本来只放文档；藏脚本进去 + `sys.path.append('docs')` 就等于开了一个不受控的后门
// （行为全改，而受控集看起来没变）。
// tests/ 不在此列：那是正常的测试目录，测试文件本来就有 .py。
function scriptsInDocDirs() {
  const hits = [];
  const base = path.join(ROOT, 'docs');
  (function rec(dir, rel) {
    let ents;
    try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    ents.forEach(function (ent) {
      const r = rel + '/' + ent.name;
      if (ent.isDirectory()) { rec(path.join(dir, ent.name), r); return; }
      if (/\.(py|js|ts|tsx|sh|bat|cmd|ps1)$/i.test(ent.name)) hits.push(r);
    });
  })(base, 'docs');
  return hits;
}


function currentHashes(root) {
  const r = root || ROOT;
  const o = {};
  controlledFiles().forEach(function (f) { o[f] = sha16(path.join(r, f)); });
  return o;
}

function readLedger() {
  try {
    const j = JSON.parse(fs.readFileSync(LEDGER, 'utf8'));
    if (!Array.isArray(j.rounds)) j.rounds = [];
    return j;
  } catch (e) {
    return { rounds: [] };
  }
}

function lastRound(led) {
  return led.rounds.length ? led.rounds[led.rounds.length - 1] : null;
}

// 放行基线只认真会审。演练轮（kind=drill）证明链路能跑，不证明代码被审过，
// 因此 check() 必须跳过它们去找最近一次 review —— 否则一次演练 stamp 就能替交付背书。
function lastReviewRound(led) {
  for (let i = led.rounds.length - 1; i >= 0; i--) {
    if ((led.rounds[i].kind || 'review') === 'review') return led.rounds[i];
  }
  return null;
}

function chainOf(round) {
  return crypto.createHash('sha256')
    .update(JSON.stringify(round.files || {}))
    .update(String(round.prev_hash || ''))
    .digest('hex').slice(0, 16);
}

function printFiles() {
  const files = controlledFiles();
  console.log('受控文件（' + files.length + ' 个，git 跟踪的运行物 + 闸自身）：');
  files.forEach(function (f) {
    console.log('  ' + (sha16(path.join(ROOT, f)) || '(缺失!)').padEnd(18) + ' ' + f);
  });
  const miss = files.filter(function (f) { return !sha16(path.join(ROOT, f)); });
  if (miss.length) {
    console.log('\n[x] 受控文件缺失：' + miss.join(', '));
    return 1;
  }
  return 0;
}

// 证据块：粘进裁决表，stamp 时逐项比对
function evidenceBlock() {
  const files = controlledFiles();
  const lines = ['<!-- GATE-FILES ' + new Date().toISOString().slice(0, 19) + ' -->'];
  files.forEach(function (f) { lines.push(f + ' ' + (sha16(path.join(ROOT, f)) || 'MISSING')); });
  lines.push('<!-- /GATE-FILES -->');
  return lines.join('\n');
}

function printEvidence() {
  console.log(evidenceBlock());
  return 0;
}

function printStatus() {
  const led = readLedger();
  console.log('台账：' + LEDGER);
  if (!led.rounds.length) {
    console.log('  (空) —— 从未登记。先跑一次真会审拿到裁决表再 --stamp。');
    return 1;
  }
  led.rounds.forEach(function (r, i) {
    console.log('  第 ' + (i + 1) + ' 轮  ' + (r.at || '?') + '  链=' + (r.chain || '?')
      + '  kind=' + (r.kind || 'review'));
    console.log('    裁决表：' + (r.verdict || '?'));
    if (r.note) console.log('    说明：' + r.note);
    console.log('    覆盖：' + Object.keys(r.files || {}).length + ' 个');
  });
  return 0;
}

// 只有"宿主会加载的运行物"才参与源/宿主比对。
// contracts/、docs/、tests/ 是开发期产物，宿主根本没有这些目录；
// 闸自己也不在宿主副本里 —— 不排除这三类会让 --sync 恒红（红到被绕过）。
function hostRuntime(f) {
  if (!/\.(py|toml|json|tsx)$/i.test(f)) return false;
  if (/^(docs|tests|contracts|\.github|\.vscode)\//i.test(f)) return false;
  if (/\.bak/i.test(f)) return false;
  if (f === GATE_SELF) return false;
  return true;
}

function syncReport() {
  const src = currentHashes(ROOT);
  const dst = currentHashes(HOST_COPY);
  const diff = [];
  Object.keys(src).filter(hostRuntime).forEach(function (f) {
    if (src[f] !== dst[f]) diff.push('    ' + f + '  源=' + (src[f] || '缺失') + ' 宿主=' + (dst[f] || '缺失'));
  });
  return diff;
}

function check() {
  const files = controlledFiles();
  const cur = currentHashes();
  const miss = files.filter(function (f) { return !cur[f]; });

  if (miss.length) {
    console.log('[x] 受控文件缺失：' + miss.join(', '));
    return 1;
  }

  // 证1：docs/ tests/ 里不许藏可执行脚本（S23）
  const sneaky = scriptsInDocDirs();
  if (sneaky.length) {
    console.log('[x] docs/ 或 tests/ 里出现可执行脚本 —— 这是不受控后门：');
    sneaky.forEach(function (f) { console.log('    ' + f); });
    console.log('    处理：移出这两个目录，或纳入受控清单后再会审。');
    return 1;
  }

  // 证2：放行前工作区必须干净（会审后要 commit，未提交=还没定稿）
  const dirty = git('status --porcelain').split('\n').map(function (s) { return s.trim(); })
    .filter(function (s) {
      if (!s) return false;
      if (/\.md$/.test(s)) return false;            // 文档不算
      if (/\.bak/i.test(s)) return false;           // 备份文件不算（建议 gitignore）
      if (/(^|\s)docs\/?/.test(s)) return false;    // 文档/会审证据目录：产物，不是运行改动
      if (/docs\/review\//.test(s)) return false;
      if (/\.bak[^/]*$/.test(s)) return false;      // 手工备份：本地留档，不进受控集
      return true;
    });
  if (dirty.length) {
    console.log('[x] 工作区不干净（会审后必须先提交）：');
    dirty.slice(0, 10).forEach(function (s) { console.log('    ' + s); });
    if (dirty.length > 10) console.log('    …还有 ' + (dirty.length - 10) + ' 项');
    return 1;
  }

  // S1/S2：源与宿主副本必须一致，否则测的是旧代码
  const diff = syncReport();
  if (diff.length) {
    console.log('[x] 源仓库与宿主副本不同步 —— 宿主里测的是旧代码：');
    diff.forEach(function (d) { console.log(d); });
    return 1;
  }

  const led = readLedger();
  const prev = lastReviewRound(led);
  if (!prev) {
    console.log('[x] 台账里没有 review 轮 —— 演练（drill）不构成放行依据，不得交付。');
    console.log('    要用真裁决表 --stamp（不带 --drill）。');
    return 1;
  }

  // 链校验（S6：手写台账会断链）
  if (prev.chain && prev.chain !== chainOf(prev)) {
    console.log('[x] 台账链断裂 —— 台账被手工改过或被截断。');
    return 1;
  }

  const base = prev.files || {};
  const changed = [];
  const added = [];
  Object.keys(cur).forEach(function (f) {
    if (!(f in base)) added.push(f);
    else if (base[f] !== cur[f]) changed.push(f);
  });
  const removed = Object.keys(base).filter(function (f) { return !(f in cur); });

  if (!changed.length && !added.length && !removed.length) {
    console.log('[√] 全部受控文件与会审登记一致（' + files.length + ' 个）。可交付。');
    console.log('    最近一轮：' + (prev.at || '?') + '  ' + (prev.verdict || '?'));
    return 0;
  }

  console.log('[x] 有未会审的改动 —— 不得交付 / 不得发布。');
  if (changed.length) {
    console.log('\n  改动过：');
    changed.forEach(function (f) { console.log('    ' + f + '   ' + (base[f] || '?') + ' -> ' + cur[f]); });
  }
  if (added.length) {
    console.log('\n  最近会审未覆盖：');
    added.forEach(function (f) { console.log('    ' + f); });
  }
  if (removed.length) {
    console.log('\n  已消失（登记里有、现在没有）：');
    removed.forEach(function (f) { console.log('    ' + f); });
  }
  console.log('\n  下一步：改 → 投包三家会审 → 裁决表(内嵌 GATE-FILES + 过 lint) → 修 → 复审 → --stamp → 才可交付');
  return 1;
}

function stamp(verdictPath, note, isDrill) {
  if (!verdictPath) { console.log('[x] --stamp 需要一个裁决表路径'); return 1; }
  const vp = path.isAbsolute(verdictPath) ? verdictPath : path.join(ROOT, verdictPath);
  if (!fs.existsSync(vp)) { console.log('[x] 裁决表不存在：' + vp); return 1; }

  const body = fs.readFileSync(vp, 'utf8');
  if (body.trim().length < 200) { console.log('[x] 裁决表太短（<200 字符）—— 空表不算会审。'); return 1; }
  if (!/裁决|verdict|采纳|驳回/.test(body)) { console.log('[x] 找不到「裁决/采纳/驳回」字样 —— 疑似不是裁决表。'); return 1; }

  // S3：必须有 GATE-FILES 证据块，且与当前哈希逐项一致
  const m = body.match(/<!-- GATE-FILES[\s\S]*?-->([\s\S]*?)<!-- \/GATE-FILES -->/);
  if (!m) {
    console.log('[x] 裁决表缺少 GATE-FILES 证据块。');
    console.log('    先跑：node neko_review_gate.js --evidence   然后整段粘进裁决表。');
    return 1;
  }
  const declared = {};
  m[1].split('\n').forEach(function (line) {
    const t = line.trim();
    if (!t) return;
    const i = t.lastIndexOf(' ');
    if (i > 0) declared[t.slice(0, i)] = t.slice(i + 1);
  });
  const cur = currentHashes();
  const mismatched = Object.keys(declared).filter(function (f) { return (cur[f] || 'MISSING') !== declared[f]; });
  if (mismatched.length) {
    console.log('[x] 证据块与当前代码不一致（裁决表里的哈希是旧的）：');
    mismatched.forEach(function (f) { console.log('    ' + f + '  表内=' + declared[f] + ' 现在=' + (cur[f] || 'MISSING')); });
    return 1;
  }
  const uncovered = Object.keys(cur).filter(function (f) { return !(f in declared); });
  if (uncovered.length) {
    console.log('[x] 证据块没覆盖这些受控文件：' + uncovered.join(', '));
    return 1;
  }

  const led = readLedger();
  const prev = lastRound(led);

  // 证2 关键支点（主审 CT-v1）：裁决表必须声明 CONTRACT=<版本> <契约表路径> <哈希>。
  // 没有这一行，契约改了以后拿旧裁决表 stamp 照样绿 —— 冻结就是纸面。
  const cm = body.match(/CONTRACT=(\S+)\s+(\S+)\s+([0-9a-f]{8,64})/);
  if (!cm) {
    console.log('[x] 裁决表缺少契约版本行。格式：');
    console.log('    CONTRACT=CT-v1 contracts/CT-v1.md <契约表哈希前16位>');
    return 1;
  }
  const cver = cm[1];
  const cfile = cm[2];
  const chash = cm[3];
  const cp = path.isAbsolute(cfile) ? cfile : path.join(ROOT, cfile);
  if (!fs.existsSync(cp)) {
    console.log('[x] 契约表不存在：' + cp);
    return 1;
  }
  const realChash = sha16(cp);
  if (realChash !== chash) {
    console.log('[x] 契约表哈希不符：裁决表=' + chash + ' 现在=' + realChash);
    console.log('    契约改过就必须重新会审，不能拿旧裁决表登记。');
    return 1;
  }

  // 证2：裁决表必须比最后一处受控改动更晚（防"拿旧裁决表登记新代码"）
  let maxMtime = 0;
  controlledFiles().forEach(function (f) {
    try { maxMtime = Math.max(maxMtime, fs.statSync(path.join(ROOT, f)).mtimeMs); } catch (e) {}
  });
  const vMtime = fs.statSync(vp).mtimeMs;
  if (vMtime < maxMtime - 1000) {
    console.log('[x] 裁决表早于最后一次代码改动 —— 它审的不是现在这份代码。');
    console.log('    裁决表: ' + new Date(vMtime).toISOString());
    console.log('    最后改动: ' + new Date(maxMtime).toISOString());
    return 1;
  }

  const round = {
    at: new Date().toISOString(),
    kind: isDrill ? 'drill' : 'review',   // ★ 闸只认 review；drill 永远不构成放行依据
    verdict: path.relative(ROOT, vp),
    note: note || '',
    files: cur,
    contract: { ver: cver, path: path.relative(ROOT, cp), hash: realChash },
    prev_hash: prev ? (prev.chain || '') : '',
  };
  round.chain = chainOf(round);
  led.rounds.push(round);
  fs.mkdirSync(path.dirname(LEDGER), { recursive: true });
  fs.writeFileSync(LEDGER, JSON.stringify(led, null, 2) + '\n', 'utf8');
  console.log('[√] 已登记第 ' + led.rounds.length + ' 轮（' + Object.keys(cur).length + ' 个受控文件，链=' + round.chain + '）');
  return 0;
}

function syncOnly() {
  const diff = syncReport();
  if (!diff.length) {
    console.log('[√] 源仓库与宿主副本一致（' + controlledFiles().length + ' 个受控文件）。');
    return 0;
  }
  console.log('[x] 源仓库与宿主副本不同步 —— 宿主里测的是旧代码：');
  diff.forEach(function (d) { console.log(d); });
  return 1;
}

// 负向对照：注入改动必须变红（堵"闸自己假绿"）
function selftest() {
  const target = controlledFiles().filter(function (f) { return /\.py$/.test(f); })[0];
  if (!target) { console.log('[x] 自测失败：没有可注入的 .py 文件'); return 1; }
  const p = path.join(ROOT, target);
  const orig = fs.readFileSync(p);
  try {
    fs.appendFileSync(p, '\n# gate-selftest-probe\n');
    const led = readLedger();
    const prev = lastRound(led);
    const cur = currentHashes();
    const red = prev ? (prev.files[target] !== cur[target]) : true;
    if (!red) { console.log('[x] 自测失败：注入改动后闸没有变红 —— 闸无效！'); return 1; }
    console.log('[√] 自测通过：注入改动后闸变红（' + target + '）');
    return 0;
  } finally {
    fs.writeFileSync(p, orig);
    console.log('    已还原 ' + target);
  }
}

const argv = process.argv.slice(2);
const mode = argv[0] || '--check';
let code = 0;
if (mode === '--check') code = check();
else if (mode === '--status') code = printStatus();
else if (mode === '--files') code = printFiles();
else if (mode === '--evidence') code = printEvidence();
else if (mode === '--sync') code = syncOnly();
else if (mode === '--stamp') {
  if (argv[1] === '--drill') code = stamp(argv[2], argv.slice(3).join(' '), true);
  else code = stamp(argv[1], argv.slice(2).join(' '), false);
}
else if (mode === '--selftest') code = selftest();
else { console.log('用法：node neko_review_gate.js [--check|--status|--files|--evidence|--sync|--stamp [--drill] <裁决表>|--selftest]'); code = 2; }
process.exit(code);
