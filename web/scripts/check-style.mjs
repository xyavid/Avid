#!/usr/bin/env node
/**
 * A9 / C11 / C14 / C15 / C18 / C22 的样式与纪律门禁（`pnpm lint`）。
 *
 * 扫描 `src/**\/*.{ts,tsx}` 与 `src/**\/*.css`（规格的最低集是「.css 只扫
 * `src/ui/sketch.css`」；这里把全部 css 都扫上——否则 `src/layouts/*.css` 这类
 * 文件里的颜色字面量与 `transition-all` 会漏网），命中任一规则即
 * `process.exitCode = 1`。`tokens.css` 是颜色与形状的唯一来源，规则 1 对它豁免：
 *   1. hex / `rgb(` / `hsl(` 字面量出现在 tokens.css 之外（`rgb(var(--…))` 引用不算）。
 *   2. `dark:` 修饰符。
 *   3. Tailwind 内建调色板类（`bg-slate-500` 之类）。
 *   4. `shadow-[`、`z-[`、内联 `borderRadius` / `fontFamily`、`transition-all`。
 *   5. `src/ui/**` 之外的裸 `<button` / `<input` / `<select`（应走 primitives）。
 *   6. `src/ui/**`、`src/layouts/**` 之外的 `rotate-`（正文与代码禁止倾斜）。
 *   7. 空 `catch {}`，或 catch 块里只有一句 noop 注释（等价于空块）。
 *   8. 每个 `fetch(` / `fetchImpl(` 调用点都在 `src/api/` 且选项里有 `signal`；
 *      `src/api/client.ts` 还要有 `setTimeout` + `abort` 超时。
 *   9. i18n key 完整性：源码里 `t('x.y')` 用到的 key 必须都在 `dictionary.ts`。
 *  10. `.tsx` 的 JSX 文本节点不得含字母或中日韩文字（`src/lib/i18n/**`、
 *      `src/ui/**` 例外；后者只允许符号）。
 *
 * 注释先剥掉再匹配：设计文档与本文件的注释里会写 `z-[…`、`transition-all` 这些
 * 反面样本，不剥注释会自伤。`stripComments` 保留字符串字面量（className 在字符串里）
 * 与行号，所以规则 2–6、8、9 仍能看见真正要查的东西。
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

/** 去掉注释但保留字符串字面量与行号（被剥掉的位置补空格）。 */
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

function readArgs(text, openIndex) {
  let depth = 0
  for (let i = openIndex; i < text.length; i += 1) {
    const char = text[i]
    if (char === '(') depth += 1
    else if (char === ')') {
      depth -= 1
      if (depth === 0) return text.slice(openIndex + 1, i)
    }
  }
  return text.slice(openIndex + 1)
}

const TOKENS_CSS = join(SRC, 'ui', 'tokens.css')
const DICTIONARY = join(SRC, 'lib', 'i18n', 'dictionary.ts')
const API_CLIENT = join(SRC, 'api', 'client.ts')

const TAILWIND_PALETTE =
  /\b(bg|text|border|from|to|via|ring|fill|stroke|divide|outline|shadow|accent|caret|decoration|placeholder)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/g
const JSX_LITERAL = />([^<>{}]*[A-Za-z\u4e00-\u9fff][^<>{}]*)</g
const EMPTY_CATCH = /(?<![\w.$])catch\s*(?:\([^)]*\))?\s*\{\s*\}/g
const FETCH_CALL = /\bfetch(?:Impl)?\s*\(/g

const files = [...walk(SRC, ['.ts', '.tsx']), ...walk(SRC, ['.css'])]

const inUi = (rel) => rel.startsWith('src/ui/')
const inLayouts = (rel) => rel.startsWith('src/layouts/')
const inI18n = (rel) => rel.startsWith('src/lib/i18n/')
const inApi = (rel) => rel.startsWith('src/api/')

for (const file of files) {
  const rel = relative(WEB_ROOT, file)
  const raw = readSource(file)
  const code = stripComments(raw)
  const isCss = file.endsWith('.css')
  const isTsx = file.endsWith('.tsx')

  // ---- 规则 1：颜色字面量 ----
  if (file !== TOKENS_CSS) {
    each(/#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])/g, code, (match) => {
      report(file, lineAt(code, match.index), `颜色字面量只允许放在 src/ui/tokens.css：${match[0]}`)
    })
    each(/\b(rgba?|hsla?)\s*\(/g, code, (match) => {
      const after = code.slice(match.index + match[0].length)
      if (/^\s*var\s*\(/.test(after)) return // rgb(var(--x) / 40%) 是 token 引用，放行。
      report(file, lineAt(code, match.index), `颜色字面量只允许放在 src/ui/tokens.css：${match[0].trim()}`)
    })
  }

  // ---- 规则 2：dark: ----
  each(/\bdark:/g, code, (match) => {
    report(file, lineAt(code, match.index), '不支持 dark: 修饰符（只有一套纸墨 token）')
  })

  // ---- 规则 3：Tailwind 内建调色板 ----
  each(TAILWIND_PALETTE, code, (match) => {
    report(file, lineAt(code, match.index), `禁止 Tailwind 内建调色板类：${match[0]}`)
  })

  // ---- 规则 4：任意值与内联样式 ----
  each(/\bshadow-\[/g, code, (match) => {
    report(file, lineAt(code, match.index), '阴影只能用 --sticker-* token，禁止 shadow-[…]')
  })
  each(/\bz-\[/g, code, (match) => {
    report(file, lineAt(code, match.index), '层级只能用 --z-* token，禁止 z-[…]')
  })
  each(/\bborderRadius\s*[:=]/g, code, (match) => {
    report(file, lineAt(code, match.index), '圆角只能用 --sketch-r* token，禁止内联 borderRadius')
  })
  each(/\bfontFamily\s*[:=]/g, code, (match) => {
    report(file, lineAt(code, match.index), '字体只能用 --font-* token，禁止内联 fontFamily')
  })
  each(/\btransition-all\b/g, code, (match) => {
    report(file, lineAt(code, match.index), '禁止 transition-all（只过渡 transform / box-shadow / background-color）')
  })

  // ---- 规则 5：裸表单元素 ----
  if (!inUi(rel)) {
    each(/<(button|input|select)[\s>]/g, code, (match) => {
      report(file, lineAt(code, match.index), `裸 <${match[1]}> 只允许出现在 src/ui/**（请用 primitives）`)
    })
  }

  // ---- 规则 6：倾斜 ----
  if (!inUi(rel) && !inLayouts(rel)) {
    each(/\brotate-/g, code, (match) => {
      report(file, lineAt(code, match.index), 'rotate- 只允许出现在装饰外壳（src/ui/**、src/layouts/**）')
    })
  }

  // ---- 规则 7 / 11：catch 必须处理 ----
  each(EMPTY_CATCH, code, (match) => {
    report(file, lineAt(code, match.index), '空 catch：必须处理（console.error / logger / return / throw…）或显式注释说明')
  })

  // ---- 规则 8：fetch 必须带 signal，client 必须带超时 ----
  each(FETCH_CALL, code, (match) => {
    const openIndex = code.indexOf('(', match.index)
    const args = readArgs(code, openIndex)
    if (!inApi(rel)) {
      report(file, lineAt(code, match.index), 'fetch 调用点只允许在 src/api/**')
    } else if (!/\bsignal\b/.test(args)) {
      report(file, lineAt(code, match.index), 'fetch 选项里必须有 signal（C11：每个网络调用都可取消）')
    }
  })

  // ---- 规则 10：JSX 内联字面量 ----
  if (isTsx && !inI18n(rel) && !inUi(rel)) {
    const lines = code.split('\n')
    lines.forEach((text, index) => {
      each(JSX_LITERAL, text, (match) => {
        report(file, index + 1, `JSX 文本节点不得写死文案（改用 t(...)）：${match[1].trim().slice(0, 40)}`)
      })
    })
    // 跨行文本节点：只在匹配片段「含换行」（单行已由上面处理）且「看起来不像代码」
    // 时才算命中，否则 `useState<X>(...)` 与三元表达式的 `>`…`<` 会误报。
    each(JSX_LITERAL, code, (match) => {
      if (!match[1].includes('\n')) return
      if (/[;()=|[\]&'"`?!]/.test(match[1])) return
      report(file, lineAt(code, match.index), `JSX 文本节点不得写死文案（改用 t(...)）：${match[1].trim().slice(0, 40)}`)
    })
  }
}

// ---- 规则 8 续：client.ts 的超时 ----
if (readSource(API_CLIENT).length > 0) {
  const clientCode = stripComments(readSource(API_CLIENT))
  const hasTimeout = /\bsetTimeout\s*\(/.test(clientCode) && /\babort\s*\(/.test(clientCode)
  if (!hasTimeout) {
    report(API_CLIENT, 1, 'src/api/client.ts 必须有超时：setTimeout + abort')
  }
}

// ---- 规则 9：i18n key 完整性 ----
const dictionarySource = stripComments(readSource(DICTIONARY))
const dictionaryKeys = new Set()
each(/^\s*'([^']+)'\s*:/gm, dictionarySource, (match) => {
  dictionaryKeys.add(match[1])
})

const used = new Map() // key -> [{file, line}]
for (const file of walk(SRC, ['.ts', '.tsx'])) {
  const code = stripComments(readSource(file))
  each(/\bt\(\s*(['"])([A-Za-z][\w.-]*)\1/g, code, (match) => {
    const key = match[2]
    const where = used.get(key) ?? []
    where.push({ file, line: lineAt(code, match.index) })
    used.set(key, where)
  })
}
for (const [key, places] of [...used.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
  if (dictionaryKeys.has(key)) continue
  for (const place of places) {
    report(place.file, place.line, `i18n key 不在 dictionary.ts 里：t('${key}')`)
  }
}

if (violations.length > 0) {
  for (const violation of violations) console.error(violation)
  console.error(`lint 失败：${violations.length} 处违规`)
  process.exitCode = 1
} else {
  console.log('lint OK')
}
