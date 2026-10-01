/**
 * E2E 的共享 fixture：**每条用例自动断言"控制台零错误"**。
 *
 * 为什么把"没有 console.error / pageerror"做成自动断言而不是各用例自己写：
 * React 的 key 冲突、无效嵌套、未捕获的 Promise、被静默吞掉的渲染异常——
 * 这些都只出现在控制台里，而页面看上去"能跑"。不自动守住的话，E2E 会给出
 * "全绿"的假象，实际带着一堆运行时告警。要豁免的用例显式调 `allowConsoleError`。
 */

import { expect, test as base } from '@playwright/test'
import type { Page } from '@playwright/test'

import { installApiStubs } from './api'
import type { ApiScenario, ApiStub } from './api'

/** 挂在 page 上的旁路数据。用 symbol 避免与 Playwright 内部字段撞名。 */
const COLLECTED = Symbol('console-errors')
const ALLOWED = Symbol('allowed-console-errors')

interface InstrumentedPage extends Page {
  [COLLECTED]?: string[]
  [ALLOWED]?: Array<string | RegExp>
}

export interface E2EFixtures {
  /** 装好 `/api/**` 打桩；传 scenario 覆盖本用例关心的端点。 */
  stubApi: (scenario?: ApiScenario) => Promise<ApiStub>
  /** 放行匹配这些子串/正则的控制台错误（其余仍然算失败）。 */
  allowConsoleError: (pattern: string | RegExp) => void
}

export const test = base.extend<E2EFixtures>({
  page: async ({ page }, use) => {
    const instrumented = page as InstrumentedPage
    const errors: string[] = []
    instrumented[COLLECTED] = errors
    instrumented[ALLOWED] = []
    page.on('console', (message) => {
      if (message.type() !== 'error') return
      const text = message.text()
      /*
       * 不把"某个请求 404"当成失败。
       *
       * 浏览器对任何加载失败的资源都发一条 `console.error("Failed to load resource: …")`，
       * 而这条消息**不带 URL**（只有 `location()` 里有），所以既不能判断是不是我们关心的
       * 接口，也会把 `/favicon.ico` 这类静态噪声算进来。把它计成失败会让"控制台零错误"
       * 这条有用的断言迅速变成噪声，最后被大家 `allowConsoleError` 掉——那就白设了。
       *
       * 接口层的失败本来就有更准的判据：打桩对未打桩端点返回指明路径的 404，
       * 用例断言 `stub.calls(...)` 或界面上的错误文案，比这条泛化消息可靠得多。
       */
      if (text.startsWith('Failed to load resource')) return
      errors.push(text)
    })
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))

    await use(page)

    /*
     * 豁免由用例自己声明（`allowConsoleError`），断言在**用例体跑完之后**做。
     * 放这里而不是 afterEach：afterEach 的执行顺序在 Playwright 里是由注册顺序决定的，
     * 而这个检查必须晚于用例体最后一次声明豁免——放在 fixture teardown 里就没有歧义。
     */
    const allowed = instrumented[ALLOWED] ?? []
    const unexpected = errors.filter(
      (text) => !allowed.some((pattern) => (typeof pattern === 'string' ? text.includes(pattern) : pattern.test(text))),
    )
    expect(unexpected, `控制台出现未豁免的错误：\n${unexpected.join('\n')}`).toEqual([])
  },

  allowConsoleError: async ({ page }, use) => {
    await use((pattern) => {
      ;(page as InstrumentedPage)[ALLOWED]?.push(pattern)
    })
  },

  stubApi: async ({ page }, use) => {
    let installed: ApiStub | null = null
    await use(async (scenario) => {
      installed = await installApiStubs(page, scenario)
      return installed
    })
    // 用例没装打桩就说明它压根不该跑（会打真后端，结果不可复现）。
    expect(installed, '这条用例没有调用 stubApi()：E2E 必须使用受控后端').not.toBeNull()
  },
})

export { expect } from '@playwright/test'
