#!/usr/bin/env node
/**
 * 玻璃的代价：长任务与帧间隔的对照测量（手动工具，不进 `verify`）。
 *
 * 为什么要有它：阶段 23b 把 §8.9 那条「模糊只在 ≥960px 且非列表区启用」改成了「允许
 * backdrop-filter，但受上限约束」。上限不能靠猜——`backdrop-filter` 在大面积或数量多时
 * 是长任务来源，而时间线恰恰是"数量随数据增长"的那一屏。
 *
 * 用法（先起一个带脚本模型的 e2e 内核，再跑）：
 *   AVID_E2E_STREAM=1 uv run --extra web python dev/tmp/e2e_server.py   # 终端 1
 *   node scripts/measure-glass.mjs --base http://127.0.0.1:8877         # 终端 2
 *
 * 它做三件事：
 *   1. 用 auto_approve + system 档把条目堆到 ~200 条（一次运行 4 条左右）；
 *   2. 对三种配置各滚动一段时间，采 rAF 帧间隔并收集 `longtask`：
 *        glass   —— 现状（面板类带 backdrop-filter）
 *        noblur  —— 把 backdrop-filter 全部关掉（只剩半透明与投影）
 *        bare    —— 再把投影也关掉（"零玻璃"基线）
 *   3. 打印帧间隔 p50/p95 与长任务总时长，并数出同屏带模糊的元素数。
 *
 * 判据：`glass` 相对 `noblur` 的 p95 帧间隔与长任务总时长没有量级差 → 上限可以放宽；
 * 反之就该把上限压到观测到的同屏模糊元素数以下，并优先减少列表内的模糊元素。
 */

import { appendFileSync } from 'node:fs'

const args = new Map()
for (let index = 2; index < process.argv.length; index += 2) {
  if (process.argv[index]?.startsWith('--')) args.set(process.argv[index].slice(2), process.argv[index + 1])
}
const BASE = args.get('base') ?? 'http://127.0.0.1:8765'
const TARGET_ENTRIES = Number(args.get('entries') ?? 200)
const SCROLL_MS = Number(args.get('scroll-ms') ?? 4000)

const { chromium } = await import('playwright').catch(() =>
  import('/home/fishy/Avid/web/node_modules/@playwright/test/index.mjs'),
)

/** 一幅有细节的"插画"底：高频线稿，逼着模糊真的去采样一个信息量大的背景。 */
const ART = `*{}`.length && `body::before{content:"";position:fixed;inset:0;z-index:-1;opacity:.75;background-image:url("data:image/svg+xml;utf8,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><defs><pattern id="p" width="12" height="12" patternUnits="userSpaceOnUse"><path d="M0 6 Q3 0 6 6 T12 6" fill="none" stroke="%238a7050" stroke-width="1.1"/><circle cx="6" cy="6" r="2.4" fill="none" stroke="%236a5540" stroke-width=".9"/></pattern></defs><rect width="240" height="240" fill="%23f0e2c6"/><rect width="240" height="240" fill="url(%23p)"/></svg>`,
)}")}`

const CONFIGS = [
  { name: 'glass（现状）', css: null },
  { name: 'noblur（关掉模糊）', css: '*{backdrop-filter:none !important;-webkit-backdrop-filter:none !important}' },
  {
    name: 'bare（再关投影）',
    css: '*{backdrop-filter:none !important;-webkit-backdrop-filter:none !important;box-shadow:none !important}',
  },
  {
    // 故意违反「列表内不模糊」那条规则：把时间线条目也变成模糊元素。
    // 测这一档是为了让规则**买到的东西**有个数，而不是只说"现状没问题"。
    name: 'rule-broken（列表也模糊）',
    css: 'article.surface-card,li .surface-chip,.surface-chip{backdrop-filter:blur(28px) saturate(1.8) !important;-webkit-backdrop-filter:blur(28px) saturate(1.8) !important}',
  },
  // 计划里点名要单独测的那条：插画底下的模糊代价（模糊照片比模糊纯 CSS 渐变贵）。
  { name: 'glass + 插画底', css: ART },
  { name: 'noblur + 插画底', css: `${ART}\n*{backdrop-filter:none !important;-webkit-backdrop-filter:none !important}` },
  {
    // 规则在"最贵的条件"下到底值多少：插画底 + 列表也模糊。
    name: 'rule-broken + 插画底',
    css: `${ART}\narticle.surface-card,li .surface-chip,.surface-chip{backdrop-filter:blur(28px) saturate(1.8) !important;-webkit-backdrop-filter:blur(28px) saturate(1.8) !important}`,
  },
]

const json = async (response) => {
  if (!response.ok()) throw new Error(`${response.url()} → ${response.status}`)
  return response.json()
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

// ── 造一个长会话：每次运行 4 条左右（用户 / 空助手轮 / 工具 / 回复）
const { workspaces } = await json(await page.request.get(`${BASE}/api/workspaces`))
const workspace = (workspaces.find((item) => item.is_default) ?? workspaces[0]).id
const session = await json(
  await page.request.post(`${BASE}/api/sessions`, { data: { workspace, name: `玻璃测量 ${Date.now()}` } }),
)

process.stdout.write(`造会话：目标 ~${TARGET_ENTRIES} 条目 `)
let entries = 0
for (let round = 0; entries < TARGET_ENTRIES && round < 200; round += 1) {
  await json(
    await page.request.post(`${BASE}/api/sessions/${session.id}/runs`, {
      // auto_approve + system：脚本模型第一轮要一次 bash，不然每轮都要点一次审批。
      data: { prompt: `第 ${round} 轮`, branch: 'main', auto_approve: true, permission: 'system' },
    }),
  )
  for (let wait = 0; wait < 100; wait += 1) {
    const detail = await json(await page.request.get(`${BASE}/api/sessions/${session.id}`))
    if (!detail.active_run_id) break
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  const page1 = await json(
    await page.request.get(`${BASE}/api/sessions/${session.id}/entries?branch=main&limit=1&order=desc`),
  )
  entries = page1.entries[0]?.seq ?? 0
  if (round % 5 === 0) process.stdout.write('.')
}
console.log(` 完成（${entries} 条目）`)

await page.goto(`${BASE}/sessions/${session.id}`)
await page.getByLabel('输入指令，Enter 发送，Shift+Enter 换行').waitFor()
await page.waitForTimeout(1500)

/** 同屏带模糊的元素数（上限 N 要按它冻）。 */
const blurCount = await page.evaluate(
  () =>
    [...document.querySelectorAll('*')].filter((element) => {
      const style = getComputedStyle(element)
      return (style.backdropFilter && style.backdropFilter !== 'none') ||
        (style.webkitBackdropFilter && style.webkitBackdropFilter !== 'none')
    }).length,
)
console.log(`同屏带 backdrop-filter 的元素数：${blurCount}`)
console.log('')

const rows = []
for (const config of CONFIGS) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const probe = await context.newPage()
  await probe.clock.setFixedTime(new Date('2026-09-22T10:00:00Z'))
  await probe.addInitScript(() => {
    const state = { longTasks: [], frames: [], last: 0 }
    window.__perf = state
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) state.longTasks.push(entry.duration)
      }).observe({ entryTypes: ['longtask'] })
    } catch {
      /* 该环境不支持 longtask：只报帧间隔。 */
    }
    const tick = (now) => {
      if (state.last) state.frames.push(now - state.last)
      state.last = now
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  })
  if (config.css) await probe.addStyleTag({ content: config.css })
  await probe.goto(`${BASE}/sessions/${session.id}`)
  await probe.getByLabel('输入指令，Enter 发送、Shift+Enter 换行').waitFor().catch(() => {})
  await probe
    .getByLabel('输入指令，Enter 发送，Shift+Enter 换行')
    .waitFor({ timeout: 20_000 })
    .catch(() => {})
  await probe.waitForTimeout(800)

  // 时间线是唯一滚动容器：来回滚一段时间。
  const until = Date.now() + SCROLL_MS
  let direction = 1
  while (Date.now() < until) {
    await probe.evaluate(async (dir) => {
      const log = document.querySelector('[role="log"]')
      if (!log) return
      log.scrollTop += dir * 900
      await new Promise((resolve) => requestAnimationFrame(resolve))
    }, direction)
    const atEdge = await probe.evaluate(() => {
      const log = document.querySelector('[role="log"]')
      if (!log) return true
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 2
      const atTop = log.scrollTop <= 1
      return atBottom || atTop
    })
    if (atEdge) direction *= -1
  }

  const perf = await probe.evaluate(() => {
    const state = window.__perf
    const frames = state.frames.filter((value) => value > 0)
    frames.sort((a, b) => a - b)
    const pick = (ratio) => frames[Math.min(frames.length - 1, Math.floor(frames.length * ratio))] ?? 0
    return {
      frames: frames.length,
      p50: pick(0.5),
      p95: pick(0.95),
      worst: frames[frames.length - 1] ?? 0,
      longTasks: state.longTasks,
    }
  })
  const longTotal = perf.longTasks.reduce((sum, value) => sum + value, 0)
  rows.push({ config: config.name, ...perf, longTotal })
  await context.close()
}

console.log('配置                     帧数    p50    p95    最差   长任务数  长任务合计')
for (const row of rows) {
  console.log(
    `${row.config.padEnd(22)} ${String(row.frames).padStart(5)}  ${row.p50.toFixed(1).padStart(4)}ms ${row.p95
      .toFixed(1)
      .padStart(5)}ms ${row.worst.toFixed(1).padStart(6)}ms ${String(row.longTasks.length).padStart(7)}  ${row.longTotal
      .toFixed(0)
      .padStart(9)}ms`,
  )
}

const glass = rows[0]
const noBlur = rows[1]
const broken = rows[3]
const ratio = noBlur.p95 > 0 ? glass.p95 / noBlur.p95 : 1
console.log('')
console.log(
  `glass / noblur 的 p95 帧间隔比：${ratio.toFixed(2)}（长任务合计 ${glass.longTotal.toFixed(0)}ms vs ${noBlur.longTotal.toFixed(0)}ms）`,
)
console.log(
  ratio > 1.25 || glass.longTotal > noBlur.longTotal + 60
    ? '→ 玻璃有可测的代价：上限 N 应压到同屏模糊元素数以下，并优先清掉列表内的模糊。'
    : '→ 在 1440×900 与这个条目量级下，玻璃的代价落进噪声：上限 N 可以按最坏同屏数留余量。',
)
const art = rows.find((row) => row.config === 'glass + 插画底')
const artNoBlur = rows.find((row) => row.config === 'noblur + 插画底')
const artBroken = rows.find((row) => row.config === 'rule-broken + 插画底')
if (art && artNoBlur) {
  console.log(
    `插画底：玻璃 ${art.longTasks.length} 次长任务 / 合计 ${art.longTotal.toFixed(0)}ms，关掉模糊 ${artNoBlur.longTasks.length} 次 / ${artNoBlur.longTotal.toFixed(0)}ms`,
  )
  if (artBroken) {
    console.log(
      `插画底 + 违反列表规则：${artBroken.longTasks.length} 次 / ${artBroken.longTotal.toFixed(0)}ms（守规则的玻璃是 ${art.longTasks.length} 次 / ${art.longTotal.toFixed(0)}ms）`,
    )
  }
}
if (broken) {
  console.log(
    `违反「列表不模糊」那一档：p50 ${broken.p50.toFixed(1)}ms / p95 ${broken.p95.toFixed(1)}ms / 最差 ${broken.worst.toFixed(1)}ms，长任务 ${broken.longTasks.length} 次合计 ${broken.longTotal.toFixed(0)}ms`,
  )
  console.log(
    `→ 相比守规则的 glass（p95 ${glass.p95.toFixed(1)}ms，长任务 ${glass.longTotal.toFixed(0)}ms）：${
      broken.p95 > glass.p95 * 1.25 || broken.longTotal > glass.longTotal + 60
        ? '规则确实买到了东西（列表内的模糊是可测的代价来源）'
        : '在这一量级下还没拉开差距（条目再多或缩到窄屏时重测）'
    }`,
  )
}

appendFileSync(
  'glass-measure.log',
  `${new Date().toISOString()} entries=${entries} blurElements=${blurCount} ${JSON.stringify(rows)}\n`,
)
await browser.close()
