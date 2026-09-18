#!/usr/bin/env node
/**
 * A8 分层门禁：用机械检查代替「请大家自觉」。
 *
 * 检查项（任一命中即 process.exitCode = 1，按 `文件:行: 说明` 打印）：
 *   1. 网络出口唯一：`fetch(` / `fetchImpl(` / `EventSource(` / `new WebSocket(`
 *      只能出现在 `src/api/**`。
 *   2. 特性之间不互相 import：`src/features/a/**` 不得 import `src/features/b/**`（a≠b），
 *      相对路径（`../b/`、`../../features/b`）与别名（`@/features/b`）都算。
 *   3. `runStoreActions` 只允许被 `src/state` / `src/events` / `src/routes` import。
 *   4. `src/**` 不得出现 `https?://`（字符串与注释都算；只有 `src/api/**` 放接口注释）。
 *   5. L1 只接受 props：`src/ui/**` 不得 import `events/` / `state/` / `features/` /
 *      `routes/` / `api/` / `layouts/`（`frontend-architecture.md` §3.4 的分层声明）。
 *   6. 暂缺：不在 `src/ui/**` 之外 import `radix` 之外的组件库（无此约束）。
 *
 * 为什么先用「扫描文本」而不是 AST：这里查的是 import 图与少数禁用标识符，正则足够；
 * 引入 parser 会把门禁脚本本身变成需要维护的依赖，与「纯 Node、零新依赖」冲突。
 * 代价是注释需要先剥掉再匹配（`stripComments`），否则文档里写规则名就会误报。
 */

import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(WEB_ROOT, 'src')

const violations = []

function report(file, line, message) {
  violations.push(`${relative(WEB_ROOT, file)}:${line}: ${message}`)
}

function walk(dir, exts) {
  const out = []
  const visit = (current) => {
    let entries
    try {
      entries = readdirSync(current, { withFileTypes: true })
    } catch {
      return // 目录还没建：按空处理（前端未写完时不崩）。
    }
    for (const entry of entries) {
      const full = join(current, entry.name)
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules') continue
        visit(full)
      } else if (exts.some((ext) => entry.name.endsWith(ext))) {
        out.push(full)
      }
    }
  }
  visit(dir)
  return out.sort()
}

function readSource(file) {
  try {
    return readFileSync(file, 'utf8')
  } catch {
    return ''
  }
}

/** 去掉注释但保留字符串字面量与行号（注释字符替换成空格）。 */
function stripComments(source) {
  let out = ''
  let i = 0
  let state = 'code' // code | line | block | single | double | template
  while (i < source.length) {
    const char = source[i]
    const next = source[i + 1]
    if (state === 'code') {
      if (char === '/' && next === '/') {
        state = 'line'
        out += '  '
        i += 2
        continue
      }
      if (char === '/' && next === '*') {
        state = 'block'
        out += '  '
        i += 2
        continue
      }
      if (char === "'") state = 'single'
      else if (char === '"') state = 'double'
      else if (char === '`') state = 'template'
      out += char
      i += 1
      continue
    }
    if (state === 'line') {
      if (char === '\n') {
        state = 'code'
        out += '\n'
      } else {
        out += ' '
      }
      i += 1
      continue
    }
    if (state === 'block') {
      if (char === '*' && next === '/') {
        state = 'code'
        out += '  '
        i += 2
        continue
      }
      out += char === '\n' ? '\n' : ' '
      i += 1
      continue
    }
    // single / double / template：原样保留（含转义与换行）。
    if (char === '\\') {
      out += char + (next ?? '')
      i += 2
      continue
    }
    if ((state === 'single' && char === "'") || (state === 'double' && char === '"') || (state === 'template' && char === '`')) {
      state = 'code'
    }
    out += char
    i += 1
  }
  return out
}

function lineAt(text, index) {
  let line = 1
  for (let i = 0; i < index && i < text.length; i += 1) {
    if (text[i] === '\n') line += 1
  }
  return line
}

const isApi = (rel) => rel.startsWith('src/api/')
const isAllowedRunStoreImporter = (rel) =>
  rel.startsWith('src/state/') || rel.startsWith('src/events/') || rel.startsWith('src/routes/')

const featureOf = (rel) => {
  const match = /^src\/features\/([^/]+)(?:\/|$)/.exec(rel)
  return match ? match[1] : null
}

const files = walk(SRC, ['.ts', '.tsx'])

for (const file of files) {
  const rel = relative(WEB_ROOT, file)
  const raw = readSource(file)
  const code = stripComments(raw)

  // ---- 规则 1：网络出口唯一 ----
  if (!isApi(rel)) {
    const network = /\bfetch(?:Impl)?\s*\(|\bEventSource\s*\(|\bnew\s+WebSocket\s*\(/g
    let match
    while ((match = network.exec(code)) !== null) {
      report(file, lineAt(code, match.index), `网络调用只允许出现在 src/api/：${match[0].trim()}`)
    }
  }

  // ---- 规则 4：src/** 不得出现 http(s)://（字符串与注释都算，用原文） ----
  if (!isApi(rel)) {
    const urls = /https?:\/\/[^\s'"`)]*/g
    const seenLines = new Set()
    let match
    while ((match = urls.exec(raw)) !== null) {
      const line = lineAt(raw, match.index)
      if (seenLines.has(line)) continue
      seenLines.add(line)
      report(file, line, `不允许出现裸 URL（只有 src/api/** 的接口注释例外）：${match[0]}`)
    }
  }

  // ---- 规则 3：runStoreActions 的 import 面 ----
  if (!isAllowedRunStoreImporter(rel)) {
    const offenses = new Map() // line -> detail
    const statementRe = /\b(import|export)\s+(?:type\s+)?(?:([^'";]*?)\s+from\s+)?['"]([^'"]+)['"]/g
    let match
    while ((match = statementRe.exec(code)) !== null) {
      if (/\brunStoreActions\b/.test(match[2] ?? '')) {
        offenses.set(lineAt(code, match.index), `import 了 runStoreActions（来自 ${match[3]}）`)
      }
    }
    if (offenses.size === 0) {
      // 没有 import 语句提到它，却在代码里出现：同样是不该有的引用。
      const mentions = /\brunStoreActions\b/g
      while ((match = mentions.exec(code)) !== null) {
        offenses.set(lineAt(code, match.index), '引用了 runStoreActions')
      }
    }
    for (const [line, detail] of [...offenses.entries()].sort((a, b) => a[0] - b[0])) {
      report(file, line, `runStoreActions 只允许被 src/state、src/events、src/routes import（${detail}）`)
    }
  }

  // ---- 规则 2：特性之间不得互相 import ----
  const from = featureOf(rel)
  const imports = [
    ...code.matchAll(/\bimport\s+(?:type\s+)?(?:([^'";]*?)\s+from\s+)?['"]([^'"]+)['"]/g),
    ...code.matchAll(/\bimport\s*\(\s*['"]([^'"]+)['"]/g),
  ]
  if (from) {
    for (const match of imports) {
      const specifier = match[2] ?? match[1]
      if (!specifier) continue
      let target = specifier
      if (specifier.startsWith('.')) {
        target = relative(WEB_ROOT, resolve(dirname(file), specifier)).split('\\').join('/')
      }
      const targetFeature = /(?:^|\/)features\/([^/]+)(?:\/|$)/.exec(target)
      if (targetFeature && targetFeature[1] !== from) {
        report(
          file,
          lineAt(code, match.index ?? 0),
          `feature「${from}」不得 import feature「${targetFeature[1]}」：${specifier}`,
        )
      }
    }
  }

  // ---- 规则 6：L1 只接受 props —— `src/ui/**` 不得 import 上层 ----
  //
  // frontend-architecture.md §3.4 声明 L1（ui/patterns、ui/primitives、ui/sketch）
  // "只接受 props；不得读 store、不得发请求"，patterns/README 也声明"反过来没有依赖，
  // 所以这一层可以脱离 store 单测"。只写声明不写检查，这条方向就会被类型 import
  // 悄悄违反（曾经 ui/patterns 就 import 了 events/reducer 与 state/uiStore）。
  if (/^src\/ui\//.test(rel.split('\\').join('/'))) {
    for (const match of imports) {
      const specifier = match[2] ?? match[1]
      if (!specifier) continue
      const target = specifier.startsWith('.')
        ? relative(WEB_ROOT, resolve(dirname(file), specifier)).split('\\').join('/')
        : specifier
      const upper = /(?:^|\/)src\/(events|state|features|routes|api|layouts)\//.exec(target)
      if (upper) {
        report(
          file,
          lineAt(code, match.index ?? 0),
          `L1（ui/）不得 import 上层 ${upper[1]}/（只接受 props）：${specifier}`,
        )
      }
    }
  }
}

if (violations.length > 0) {
  for (const violation of violations) console.error(violation)
  console.error(`check:layers 失败：${violations.length} 处违规`)
  process.exitCode = 1
} else {
  console.log('check:layers OK')
}
