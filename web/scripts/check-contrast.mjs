#!/usr/bin/env node
/**
 * 对比度门禁：把 token 表里**必须成立**的配对逐条按 alpha 合成后算 WCAG 比值，
 * 正文（<24px 且非「≥18.66px 加粗」）要求 ≥ 4.5。
 *
 * 为什么需要它：阶段 23b 之前的 9 处不达标全是"没人算过"的结果——旧设计声称修掉了
 * purrcat 的对比度缺陷，但那四色状态文字自己从没被验过（实测 2.73–3.93），
 * 玻璃底只会让它更糟。颜色是不是可读，不该靠人记得算。
 *
 * 这里的**配对清单是声明**（哪些组合必须成立），**取值全部从 ui/tokens.css 读**
 * （不写字面量）。所以改 token 值会立刻被这条门禁审一遍，而改配对清单是一次显式的决定。
 *
 * 合成模型（与 docs/design/frontend-architecture.md §8.2 一致）：
 *   最坏底色 = 最深的那档光斑（--avid-blob-4-rgb）
 *   → 叠 N 层玻璃（--glass-face 的 alpha）
 *   → 叠文字（token 自己的 alpha）
 * 模糊只会把极端值往均值拉，所以按"最深光斑"取是保守的。
 */

import { readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const TOKENS = join(WEB_ROOT, 'src', 'ui', 'tokens.css')

/** 正文门槛；大字（≥24px，或 ≥18.66px 加粗）是 3。 */
const AA_TEXT = 4.5
/** 设计目标：比门槛高 0.3，免得"刚好通过"变成巧合。 */
const TARGET = 4.8

// ---------------------------------------------------------------- token 解析

const source = readFileSync(TOKENS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const raw = new Map()
for (const match of source.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
  raw.set(match[1], match[2].trim())
}

/** 反复替换 `var(--x)` 直到没有 var（值里可以引用别的 token）。 */
function resolveToken(name, seen = new Set()) {
  if (seen.has(name)) throw new Error(`token 循环引用：${name}`)
  seen.add(name)
  const value = raw.get(name)
  if (value === undefined) throw new Error(`token 不存在：${name}`)
  return value.replace(/var\((--[a-z0-9-]+)\)/gi, (_, inner) => resolveToken(inner, seen))
}

/** `rgb(201 123 63 / 70%)` 或 `201 123 63` → `{ rgb:[r,g,b], alpha }`。 */
function parseColor(value, where) {
  const match = /rgb\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:\/\s*([\d.]+)%)?\s*\)/i.exec(
    value,
  )
  if (match) {
    return {
      rgb: [Number(match[1]), Number(match[2]), Number(match[3])],
      alpha: match[4] === undefined ? 1 : Number(match[4]) / 100,
    }
  }
  const triplet = /^\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*$/.exec(value)
  if (triplet) return { rgb: [Number(triplet[1]), Number(triplet[2]), Number(triplet[3])], alpha: 1 }
  throw new Error(`${where} 不是可解析的颜色：${value}`)
}

const token = (name) => resolveToken(name)

// ---------------------------------------------------------------- 色彩数学

const toLinear = (channel) => {
  const c = channel / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

const luminance = ([r, g, b]) =>
  0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b)

const contrast = (fg, bg) => {
  const a = luminance(fg)
  const b = luminance(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/** `top` 以 alpha 叠在 `bottom` 上。 */
const over = (top, bottom) => top.rgb.map((channel, index) => top.alpha * channel + (1 - top.alpha) * bottom[index])

const hex = (rgb) => `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`

// ---------------------------------------------------------------- 合成链

const DARKEST_BLOB = parseColor(token('--avid-blob-4-rgb'), '--avid-blob-4-rgb').rgb
const GLASS = parseColor(token('--glass-face'), '--glass-face')

/** 最深光斑上叠 `layers` 层玻璃。 */
function panel(layers) {
  let bg = DARKEST_BLOB
  for (let i = 0; i < layers; i += 1) bg = over(GLASS, bg)
  return bg
}

/** 文字色以自身 alpha 叠在底上。 */
const textOn = (color, bg) => over(color, bg)

// ---------------------------------------------------------------- 配对清单

/**
 * 每条：`{ label, fg, bgLayers, need? }`。
 * `need` 默认取 TARGET（设计目标），所以"刚好过 4.5"也会红——那不该算通过。
 */
const CHECKS = [
  { label: '正文墨 / 1 层玻璃面板', fg: '--avid-ink-rgb', glass: 1 },
  { label: '正文墨 / 2 层（面板内卡片）', fg: '--avid-ink-rgb', glass: 2 },
  { label: '正文墨 / 3 层（卡片内嵌套）', fg: '--avid-ink-rgb', glass: 3 },
  { label: '次级墨 / 2 层（唯一允许的次级档）', fg: '--avid-ink-muted', glass: 2 },
  { label: '次级墨 / 1 层', fg: '--avid-ink-muted', glass: 1 },
  { label: '状态 ok 文字 / 1 层', fg: '--avid-ok-rgb', glass: 1 },
  { label: '状态 warn 文字 / 1 层', fg: '--avid-warn-rgb', glass: 1 },
  { label: '状态 danger 文字 / 1 层', fg: '--avid-danger-rgb', glass: 1 },
  { label: '状态 info 文字 / 1 层', fg: '--avid-info-rgb', glass: 1 },
  { label: '强调（文字档）/ 1 层', fg: '--avid-accent-ink-rgb', glass: 1 },
]

/** 实心填充上的文字：底是填充本身（不叠玻璃）。 */
const FILLS = [
  { label: '强调实心底 + 近白字', fg: '--avid-glass-rgb', bg: '--avid-accent-deep-rgb' },
  { label: '强调浅底 + 墨字', fg: '--avid-ink-rgb', bg: '--avid-accent-soft-rgb' },
  { label: 'ok 底 + 墨字', fg: '--avid-ink-rgb', bg: '--avid-ok-bg-rgb' },
  { label: 'warn 底 + 墨字', fg: '--avid-ink-rgb', bg: '--avid-warn-bg-rgb' },
  { label: 'danger 底 + 墨字', fg: '--avid-ink-rgb', bg: '--avid-danger-bg-rgb' },
  { label: 'info 底 + 墨字', fg: '--avid-ink-rgb', bg: '--avid-info-bg-rgb' },
  { label: 'mark 底 + 墨字', fg: '--avid-ink-rgb', bg: '--avid-mark-rgb' },
]

// ---------------------------------------------------------------- 执行

const failures = []
const lines = []

lines.push('== check:contrast（按 alpha 合成；最坏底色 = 最深光斑 + 玻璃层）==')
lines.push(`最坏底色：1 层 ${hex(panel(1))} · 2 层 ${hex(panel(2))} · 3 层 ${hex(panel(3))}`)
lines.push('')
lines.push(`门槛 ${AA_TEXT}（正文）· 设计目标 ${TARGET}`)
lines.push('')

for (const check of CHECKS) {
  const bg = panel(check.glass)
  const fgToken = parseColor(token(check.fg), check.fg)
  const fg = textOn(fgToken, bg)
  const ratio = contrast(fg, bg)
  const need = check.need ?? TARGET
  const ok = ratio >= need
  if (!ok) failures.push(`${check.label}：${ratio.toFixed(2)} < ${need}`)
  lines.push(
    `  ${ok ? 'OK  ' : '失败'} ${check.label.padEnd(34)} ${ratio.toFixed(2)}:1  （${hex(fg)} on ${hex(bg)}）`,
  )
}

lines.push('')
for (const fill of FILLS) {
  const bg = parseColor(token(fill.bg), fill.bg).rgb
  const fgToken = parseColor(token(fill.fg), fill.fg)
  // 填充底的 alpha 已经合进 fill 本身；文字再叠上去。
  const fg = textOn(fgToken, bg)
  const ratio = contrast(fg, bg)
  const ok = ratio >= TARGET
  if (!ok) failures.push(`${fill.label}：${ratio.toFixed(2)} < ${TARGET}`)
  lines.push(
    `  ${ok ? 'OK  ' : '失败'} ${fill.label.padEnd(34)} ${ratio.toFixed(2)}:1  （${hex(fg)} on ${hex(bg)}）`,
  )
}

if (failures.length > 0) {
  for (const line of lines) console.error(line)
  console.error('')
  for (const failure of failures) console.error(`check:contrast 失败：${failure}`)
  console.error(`check:contrast 失败：${failures.length} 处不达标`)
  process.exitCode = 1
} else {
  console.log(`check:contrast OK（${CHECKS.length + FILLS.length} 对，全部 ≥ ${TARGET}）`)
}
