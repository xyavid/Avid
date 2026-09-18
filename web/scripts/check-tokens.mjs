#!/usr/bin/env node
/**
 * C22 的 token / 资源门禁（`pnpm check:tokens`）。
 *
 * 检查项：
 *   1. `src/ui/tokens.css` 里每个 `--font-*` 用到的字体族，要么由同文件 `@font-face`
 *      声明且 `url()` 指向 `src/assets/fonts/` 下真实存在的文件，要么是显式系统栈。
 *      典型要拦的是「声明了 Inter 却从未加载」——它会静默回落到默认字体。
 *   2. `src/**` 不得出现任意值层级 `z-[`；`z-index` 只能是 `var(--z-*)`
 *      （层级刻度见 tokens.css：base/sticky/drawer/modal/toast/shell-chrome）。
 *   3. 所有 CSS `url(...)` 指向的文件必须存在（字体、纹理都算）。
 *
 * 缺文件按空处理（前端未写完时不崩），但缺了会打 `warn:`，避免看起来「检查过了」。
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(WEB_ROOT, 'src')
const TOKENS_CSS = join(SRC, 'ui', 'tokens.css')

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
      return
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

/** 去掉注释但保留字符串字面量与行号。 */
function stripComments(source) {
  let out = ''
  let i = 0
  let state = 'code'
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

function each(regex, text, callback) {
  regex.lastIndex = 0
  let match
  while ((match = regex.exec(text)) !== null) {
    callback(match)
    if (match[0] === '') regex.lastIndex += 1
  }
}

const unquote = (value) => value.trim().replace(/^['"]|['"]$/g, '')

/** 显式系统栈关键字：命中即认为「不依赖加载」。 */
const SYSTEM_STACK = [
  'system-ui',
  'ui-sans-serif',
  'ui-serif',
  'ui-monospace',
  'ui-rounded',
  '-apple-system',
  'blinkmacsystemfont',
  'sans-serif',
  'monospace',
  'cursive',
  'fantasy',
  'serif',
  'noto sans cjk',
  'noto sans sc',
  'pingfang',
  'microsoft yahei',
  'segoe ui',
  'segoe print',
  'helvetica',
  'arial',
  'roboto',
  'consolas',
  'menlo',
  'monaco',
  'cascadia',
  'fira code',
  'jetbrains mono',
  'emoji',
]

const isSystemStack = (family) => {
  const lower = family.toLowerCase()
  return SYSTEM_STACK.some((keyword) => lower.includes(keyword))
}

// ---------- 规则 1：字体族必须「加载过」或「显式系统栈」 ----------

const tokensRaw = readSource(TOKENS_CSS)
if (tokensRaw.length === 0) {
  console.error('warn: 未找到 src/ui/tokens.css，字体与层级检查跳过')
}
const tokensCode = stripComments(tokensRaw)

const declaredFamilies = new Set()
each(/@font-face\s*\{([\s\S]*?)\}/g, tokensCode, (match) => {
  const block = match[1]
  const family = /font-family\s*:\s*([^;]+);/.exec(block)
  if (family) declaredFamilies.add(unquote(family[1].split(',')[0]))

  const src = /src\s*:\s*([^;]+);/.exec(block)
  if (!src) {
    report(TOKENS_CSS, lineAt(tokensCode, match.index), '@font-face 没有 src')
    return
  }
  each(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, src[1], (urlMatch) => {
    const url = urlMatch[2]
    if (/^(data:|https?:|#)/.test(url)) return
    const target = resolve(dirname(TOKENS_CSS), url)
    if (!existsSync(target)) {
      report(TOKENS_CSS, lineAt(tokensCode, match.index), `@font-face 指向不存在的文件：${url}`)
    }
  })
})

each(/--font-[\w-]+\s*:\s*([^;}]+)/g, tokensCode, (match) => {
  const line = lineAt(tokensCode, match.index)
  const variable = match[0].slice(0, match[0].indexOf(':'))
  for (const token of match[1].split(',')) {
    const family = unquote(token)
    if (!family) continue
    if (declaredFamilies.has(family)) continue
    if (isSystemStack(family)) continue
    report(TOKENS_CSS, line, `${variable} 里的字体族「${family}」既没有 @font-face 加载，也不在系统栈里`)
  }
})

// ---------- 规则 2：层级 ----------

for (const file of walk(SRC, ['.ts', '.tsx', '.css'])) {
  const code = stripComments(readSource(file))
  each(/\bz-\[/g, code, (match) => {
    report(file, lineAt(code, match.index), '层级只能用 --z-* token，禁止 z-[…]')
  })
  if (file.endsWith('.css')) {
    each(/\bz-index\s*:\s*([^;}]+)/g, code, (match) => {
      if (!/^var\(\s*--z-/.test(match[1].trim())) {
        report(file, lineAt(code, match.index), `z-index 只允许 var(--z-*)：${match[1].trim()}`)
      }
    })
  } else {
    each(/\bzIndex\s*:\s*([^,}]+)/g, code, (match) => {
      if (!/var\(\s*--z-/.test(match[1])) {
        report(file, lineAt(code, match.index), `内联 zIndex 只允许 var(--z-*)：${match[1].trim()}`)
      }
    })
  }
}

// ---------- 规则 3：CSS url() 必须存在 ----------

for (const file of walk(SRC, ['.css'])) {
  const code = stripComments(readSource(file))
  each(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, code, (match) => {
    const url = match[2]
    if (/^(data:|https?:|#)/.test(url)) return
    const target = resolve(dirname(file), url)
    if (!existsSync(target)) {
      report(file, lineAt(code, match.index), `CSS url() 指向不存在的文件：${url}`)
    }
  })
}

if (violations.length > 0) {
  for (const violation of violations) console.error(violation)
  console.error(`check:tokens 失败：${violations.length} 处违规`)
  process.exitCode = 1
} else {
  console.log('check:tokens OK')
}
