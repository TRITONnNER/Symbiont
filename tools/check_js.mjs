#!/usr/bin/env node
// tools/check_js.mjs — синтаксическая проверка всего нашего JS и inline-скриптов в
// .dc.html оболочках. Ловит регрессии вроде сломанного `<script>` в дизайн-оболочке
// (главный экран приложения — большой .dc.html, который ничем иначе не проверяется).
// Запуск: node tools/check_js.mjs   (код возврата ≠0 при любой ошибке разбора)
import { readFileSync, writeFileSync, readdirSync, statSync, mkdtempSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, extname } from 'node:path';
import { tmpdir } from 'node:os';

const ROOT = process.cwd();
const SKIP_DIR = new Set(['.git', 'node_modules', '.venv', '.dart_tool', 'build', '__pycache__', 'vendor']);
const SKIP_FILE = /react(-dom)?\.production\.min\.js$/;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIR.has(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

// Синтаксическая проверка кода: сначала как classic script, при неудаче — как ES-модуль
// (некоторые файлы используют top-level await/import). Ошибка → бросаем текст обеих попыток.
const tmp = mkdtempSync(join(tmpdir(), 'symjs-'));
let scriptSeq = 0;
function checkSource(code, label) {
  const file = join(tmp, `s${scriptSeq++}.mjs`);
  const fileCjs = join(tmp, `s${scriptSeq++}.js`);
  writeFileSync(fileCjs, code);
  try {
    execFileSync(process.execPath, ['--check', fileCjs], { stdio: 'pipe' });
    return null; // ок как classic
  } catch (eClassic) {
    writeFileSync(file, code);
    try {
      execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
      return null; // ок как модуль
    } catch (eMod) {
      const msg = (eClassic.stderr || eMod.stderr || Buffer.from('')).toString().split('\n').slice(0, 4).join('\n');
      return `${label}\n${msg}`;
    }
  }
}

const files = walk(ROOT);
const errors = [];
let jsCount = 0, dcCount = 0, blockCount = 0;

for (const f of files) {
  const ext = extname(f);
  if (ext === '.js' && !SKIP_FILE.test(f)) {
    jsCount++;
    const err = checkSource(readFileSync(f, 'utf8'), f);
    if (err) errors.push(err);
  } else if (f.endsWith('.dc.html')) {
    dcCount++;
    const html = readFileSync(f, 'utf8');
    // берём только inline <script> без src=
    const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
    let m, i = 0;
    while ((m = re.exec(html)) !== null) {
      const body = m[1].trim();
      if (!body) continue;
      blockCount++;
      const err = checkSource(body, `${f} [inline <script> #${++i}]`);
      if (err) errors.push(err);
    }
  }
}

console.log(`checked: ${jsCount} .js files, ${dcCount} .dc.html files (${blockCount} inline script blocks)`);
if (errors.length) {
  console.error(`\nPARSE ERRORS (${errors.length}):\n`);
  for (const e of errors) console.error('— ' + e + '\n');
  process.exit(1);
}
console.log('all JS parses ✔');
