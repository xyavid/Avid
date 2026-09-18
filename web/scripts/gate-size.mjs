#!/usr/bin/env node
/**
 * C1 / C17 / P9 的体积门禁（`pnpm gate:size`）。
 *
 * 读 `dist/index.html` 里真正进首屏的 JS（`<script src>` + `<link rel=modulepreload>`），
 * 逐块 gzip 后求和；再检查单块上限、字体、位图纹理，以及有没有把 Google Fonts 带回来。
 * 上限值来自 `budget.json`（P1：先拦住明显失控的引入，冻结时补 frozen_at）。
 *
 * `dist/` 不存在或为空 = 没 build，直接报「先 pnpm build」并 exit 1。
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { basename, dirname, extname, join, relative, resolve } from 'node:path'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(WEB_ROOT, 'dist')
const BUDGET_FILE = join(WEB_ROOT, 'budget.json')
const INDEX_HTML = join(DIST, 'index.html')

const TEXT_EXTS = new Set(['.html', '.js', '.mjs', '.cjs', '.css', '.json', '.svg', '.txt', '.map', '.webmanifest'])
const FONT_EXTS = new Set(['.woff2'])
const TEXTURE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico'])
const EXTERNAL_FONT_HOSTS = ['fonts.googleapis', 'fonts.gstatic']

const problems = []

function fail(message) {
  problems.push(message)
}

function listFiles(dir, out = []) {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return out
  }
  for (const entry of entries) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) listFiles(full, out)
    else out.push(full)
  }
  return out.sort()
}

function gzipBytes(file) {
  return gzipSync(readFileSync(file), { level: 9 }).length
}

function each(regex, text, callback) {
  regex.lastIndex = 0
  let match
  while ((match = regex.exec(text)) !== null) {
    callback(match)
    if (match[0] === '') regex.lastIndex += 1
  }
}

const fmt = (bytes) => `${bytes.toLocaleString('en-US')} B`

// ---------- 前置条件 ----------

if (!existsSync(BUDGET_FILE)) {
  console.error(`gate:size 失败：缺少 ${relative(WEB_ROOT, BUDGET_FILE)}`)
  process.exit(1)
}
const budget = JSON.parse(readFileSync(BUDGET_FILE, 'utf8'))

const distFiles = existsSync(DIST) ? listFiles(DIST) : []
if (distFiles.length === 0) {
  console.error('先 pnpm build')
  process.exit(1)
}
if (!existsSync(INDEX_HTML)) {
  console.error('先 pnpm build')
  console.error(`gate:size 失败：${relative(WEB_ROOT, INDEX_HTML)} 不存在`)
  process.exit(1)
}

// ---------- 首屏 JS ----------

const html = readFileSync(INDEX_HTML, 'utf8')
const refs = new Set()
each(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']/gi, html, (match) => {
  refs.add(match[1])
})
each(/<link\b[^>]*>/gi, html, (match) => {
  const tag = match[0]
  const rel = /\brel\s*=\s*["']([^"']+)["']/i.exec(tag)
  const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag)
  if (rel && href && rel[1].split(/\s+/).includes('modulepreload')) refs.add(href[1])
})

const entryFiles = []
for (const ref of refs) {
  if (/^(data:|https?:|\/\/)/.test(ref)) {
    console.error(`warn: 首屏引用了外部资源（不参与本地体积统计）：${ref}`)
    continue
  }
  const clean = ref.replace(/^\.?\//, '').split('?')[0].split('#')[0]
  const target = join(DIST, clean)
  if (!existsSync(target)) {
    fail(`首屏引用在 dist 里不存在：${ref}`)
    continue
  }
  if (!entryFiles.includes(target)) entryFiles.push(target)
}

let entryRaw = 0
let entryGzip = 0
for (const file of entryFiles) {
  entryRaw += statSync(file).size
  entryGzip += gzipBytes(file)
}
if (entryGzip > budget.entry_gzip_bytes) {
  fail(`首屏 JS gzip ${fmt(entryGzip)} 超过 entry_gzip_bytes ${fmt(budget.entry_gzip_bytes)}`)
}

// ---------- 单块上限与豁免 ----------

const exempt = []
for (const raw of budget.EXEMPT ?? []) {
  const entry =
    typeof raw === 'string'
      ? { name: raw, reason: null }
      : raw && typeof raw === 'object'
        ? { name: raw.name ?? raw.path ?? raw.chunk ?? null, reason: raw.reason ?? null }
        : { name: null, reason: null }
  if (!entry.name) {
    fail('budget.EXEMPT 里的项缺少 name（文件名或 dist 相对路径）')
    continue
  }
  if (!entry.reason) {
    fail(`budget.EXEMPT 里的「${entry.name}」缺少 reason（豁免必须写清为什么）`)
    continue
  }
  exempt.push(entry)
}

const jsFiles = distFiles.filter((file) => extname(file) === '.js')
let biggest = null
let exemptRaw = 0
let exemptGzip = 0
for (const file of jsFiles) {
  const raw = statSync(file).size
  const gz = gzipBytes(file)
  const rel = relative(DIST, file).split('\\').join('/')
  if (!biggest || gz > biggest.gzip) biggest = { file, rel, raw, gzip: gz }
  const matched = exempt.find((entry) => entry.name === rel || entry.name === basename(file))
  if (matched) {
    exemptRaw += raw
    exemptGzip += gz
    matched.hit = (matched.hit ?? 0) + 1
    matched.bytes = (matched.bytes ?? 0) + gz
    continue
  }
  if (gz > budget.chunk_gzip_bytes) {
    fail(`单块 gzip ${fmt(gz)} 超过 chunk_gzip_bytes ${fmt(budget.chunk_gzip_bytes)}：${rel}`)
  }
}
if (exemptGzip > budget.exempt_gzip_bytes) {
  fail(`豁免块合计 gzip ${fmt(exemptGzip)} 超过 exempt_gzip_bytes ${fmt(budget.exempt_gzip_bytes)}`)
}
for (const entry of exempt) {
  if (!entry.hit) console.error(`warn: budget.EXEMPT 里的「${entry.name}」没有匹配到任何块`)
}

// ---------- 字体 ----------

let fontRaw = 0
let fontGzip = 0
let fontCount = 0
for (const file of distFiles) {
  if (!FONT_EXTS.has(extname(file).toLowerCase())) continue
  fontRaw += statSync(file).size
  fontGzip += gzipBytes(file)
  fontCount += 1
}
if (fontRaw > budget.font_bytes) {
  fail(`字体合计 raw ${fmt(fontRaw)} 超过 font_bytes ${fmt(budget.font_bytes)}`)
}

// ---------- 位图纹理 ----------

let textureRaw = 0
let textureGzip = 0
const textureFiles = []
for (const file of distFiles) {
  if (!TEXTURE_EXTS.has(extname(file).toLowerCase())) continue
  const name = basename(file).toLowerCase()
  textureFiles.push(relative(DIST, file).split('\\').join('/'))
  if (!name.includes('favicon') && !name.includes('logo')) {
    fail(`位图纹理只允许 favicon/logo（C17）：${relative(DIST, file)}`)
  }
  textureRaw += statSync(file).size
  textureGzip += gzipBytes(file)
}
if (textureRaw > budget.texture_bytes) {
  fail(`位图纹理合计 raw ${fmt(textureRaw)} 超过 texture_bytes ${fmt(budget.texture_bytes)}`)
}

// ---------- 外部字体请求 ----------

const externalHits = []
for (const file of distFiles) {
  if (!TEXT_EXTS.has(extname(file).toLowerCase())) continue
  if (statSync(file).size > 8 * 1024 * 1024) continue
  const text = readFileSync(file, 'utf8')
  for (const host of EXTERNAL_FONT_HOSTS) {
    if (text.includes(host)) externalHits.push(`${relative(DIST, file)} → ${host}`)
  }
}
for (const hit of externalHits) {
  fail(`产物里出现外部字体请求（必须自托管）：${hit}`)
}

// ---------- 报告 ----------

const ok = (flag) => (flag ? 'OK' : 'FAIL')
const limit = budget.chunk_gzip_bytes
const biggestOk = !biggest || biggest.gzip <= limit || exempt.some((entry) => entry.name === biggest.rel)

const lines = []
lines.push('== gate:size（raw = 文件原始字节；gzip = zlib 默认等级压缩后字节）==')
lines.push('首屏 JS（index.html 的 <script src> + <link rel=modulepreload>）')
if (entryFiles.length === 0) lines.push('  （没有引用到任何本地 JS）')
for (const file of entryFiles) {
  lines.push(`  ${relative(DIST, file).split('\\').join('/')}  raw ${fmt(statSync(file).size)}  gzip ${fmt(gzipBytes(file))}`)
}
lines.push(
  `  合计  raw ${fmt(entryRaw)}  gzip ${fmt(entryGzip)}  / 上限 entry_gzip_bytes ${fmt(budget.entry_gzip_bytes)}  ${ok(entryGzip <= budget.entry_gzip_bytes)}`,
)
lines.push('单块最大 JS')
if (biggest) {
  const exemptMark = exempt.some((entry) => entry.name === biggest.rel) ? '（已豁免）' : ''
  lines.push(
    `  ${biggest.rel}  raw ${fmt(biggest.raw)}  gzip ${fmt(biggest.gzip)}  / 上限 chunk_gzip_bytes ${fmt(limit)}  ${ok(biggestOk)}${exemptMark}`,
  )
} else {
  lines.push('  （没有 .js 文件）')
}
lines.push(
  `豁免块  ${exemptGzip > 0 ? `${exempt.length} 项，合计 raw ${fmt(exemptRaw)}  gzip ${fmt(exemptGzip)}` : '无'}  / 上限 exempt_gzip_bytes ${fmt(budget.exempt_gzip_bytes)}  ${ok(exemptGzip <= budget.exempt_gzip_bytes)}`,
)
lines.push(
  `字体（.woff2）  ${fontCount} 个文件  raw ${fmt(fontRaw)}  gzip ${fmt(fontGzip)}  / 上限 font_bytes ${fmt(budget.font_bytes)}  ${ok(fontRaw <= budget.font_bytes)}`,
)
lines.push(
  `位图纹理（png/jpg/jpeg/webp/gif/ico）  ${textureFiles.length} 个文件  raw ${fmt(textureRaw)}  gzip ${fmt(textureGzip)}  / 上限 texture_bytes ${fmt(budget.texture_bytes)}  ${ok(textureRaw <= budget.texture_bytes)}`,
)
for (const file of textureFiles) lines.push(`  · ${file}`)
lines.push(
  `外部字体请求（fonts.googleapis / fonts.gstatic）  ${externalHits.length === 0 ? '未命中' : `${externalHits.length} 处`}  ${ok(externalHits.length === 0)}`,
)

for (const line of lines) console.log(line)

if (problems.length > 0) {
  for (const problem of problems) console.error(`gate:size 失败：${problem}`)
  process.exitCode = 1
} else {
  console.log('gate:size OK')
}
