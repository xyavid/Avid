// @vitest-environment jsdom
/**
 * 用量指示器的渲染与位置用例（阶段 22）。
 *
 * 两件纯函数测不到的事：
 *   1. 活动域与查询域**都**能被它读到（实时优先、落盘兜底、都没有就是「—」）；
 *   2. 它真的落在输入条那一行、紧邻发送按钮之前——这是需求指定的位置，
 *      靠 DOM 顺序断言，而不是靠人肉看图。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const branches = vi.hoisted(() => ({ value: undefined as unknown }))
const meta = vi.hoisted(() => ({ value: undefined as unknown }))

vi.mock('../../../api/queries', () => ({
  useBranches: () => ({ data: branches.value }),
  useMeta: () => ({ data: meta.value }),
}))

/**
 * 摆好 `/api/meta` 的能力表：`1` = 正常内核，`undefined` = 旧内核（表里没有这一项），
 * `0` = 声明了但不可用。
 *
 * 不要给它默认参数：`featuresUsage(undefined)` 会触发默认值，把"旧内核"静默换成"正常"。
 */
function featuresUsage(value: number | undefined): void {
  meta.value = value === undefined ? { features: {} } : { features: { usage: value } }
}

import type { ReactNode } from 'react'

import type { UsageReport } from '../../../api/types'
import { LocaleProvider } from '../../../lib/i18n'
import { useRunStore } from '../../../state/runStore'
import { Composer } from '../components/Composer'
import { UsageDetail, UsageMeter } from '../components/UsageMeter'

/**
 * 必须走 `LocaleProvider`：`useTranslation` 的**默认**上下文只查字典、不插值，
 * 少了它 `{tokens}` 这类占位符会原样留在界面上（真实入口 `main.tsx` 永远有 Provider）。
 */
function withLocale(node: ReactNode) {
  return render(<LocaleProvider>{node}</LocaleProvider>)
}

function report(overrides: Partial<UsageReport> = {}): UsageReport {
  return {
    context: {
      tokens: 72_000,
      window: 200_000,
      utilization: 0.36,
      parts: { system: 2_000, tools: 6_000, messages: 64_000 },
    },
    cache: { read_tokens: 56_000, write_tokens: null, hit_ratio: 0.778 },
    compaction: { count: 2, last_compaction_tokens: 42_000, last_step: 'micro_compact' },
    ...overrides,
  }
}

/** 落盘值：分支查询里的那个分支。 */
function savedOn(branch: string, usage: UsageReport | null): void {
  branches.value = { session_id: 's1', branches: [{ name: branch, usage }] }
}

/**
 * 实时值：直接摆好活动域的状态。
 *
 * 为什么不用 `runStoreActions.apply`（走真实 reducer）：分层门禁不允许 `features/**`
 * import 活动域的写入口（只有 state / events / routes 可以）。reducer 那条流水线由
 * `events/__tests__/reducer.test.ts` 覆盖，这里只验证"组件确实读到了这个域"。
 */
function liveUsage(usage: UsageReport): void {
  useRunStore.setState((state) => ({ view: { ...state.view, usage } }))
}

beforeEach(() => {
  branches.value = undefined
  featuresUsage(1)
  useRunStore.setState((state) => ({
    view: { ...state.view, seq: 0, usage: null },
  }))
})

afterEach(() => {
  cleanup()
})

describe('UsageMeter', () => {
  it('内核没声明 features.usage 时整个指示器不画（旧内核上不留一个「用量 —」）', () => {
    savedOn('main', report())
    featuresUsage(undefined)
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    expect(screen.queryByTestId('usage-meter')).toBeNull()
  })

  it('features.usage = 0 同样不画：能力表说的是"实际可用"', () => {
    featuresUsage(0)
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    expect(screen.queryByTestId('usage-meter')).toBeNull()
  })

  it('没有读数时显示「用量 —」，不显示 0', () => {
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    expect(screen.getByTestId('usage-meter').textContent).toBe('用量 —')
  })

  it('只用落盘值也能显示：进入会话 / 刷新后就能看到上次运行', () => {
    savedOn('main', report())
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    const text = screen.getByTestId('usage-meter').textContent ?? ''
    expect(text).toContain('上下文已用 36%')
    expect(text).toContain('缓存命中 78%')
  })

  it('实时值盖过落盘值', () => {
    savedOn('main', report())
    liveUsage(
      report({
        context: { tokens: 1000, window: 200_000, utilization: 0.005, parts: null },
      }),
    )
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    expect(screen.getByTestId('usage-meter').textContent).toContain('上下文已用 0.5%')
  })

  it('用量按分支记账：别的分支的读数不串台', () => {
    savedOn('b2', report())
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    expect(screen.getByTestId('usage-meter').textContent).toBe('用量 —')
  })

  it('没有窗口时只报 tokens（不编一个占用率），没有命中数据时显示「缓存 —」', () => {
    savedOn(
      'main',
      report({
        context: { tokens: 12_000, window: null, utilization: null, parts: null },
        cache: { read_tokens: null, write_tokens: null, hit_ratio: null },
      }),
    )
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    const text = screen.getByTestId('usage-meter').textContent ?? ''
    expect(text).toContain('上下文 12k')
    expect(text).toContain('缓存 —')
    // 没有分母时把原因写在脸上：不然这一行看起来像"功能坏了"。
    expect(text).toContain('窗口未知')
  })
})

describe('Composer 里的位置', () => {
  it('指示器在发送按钮之前（权限行空位右端、紧邻按钮左侧）', () => {
    savedOn('main', report())
    withLocale(
      <Composer
        busy={false}
        canSend
        permission="manual"
        onPermissionChange={() => undefined}
        onSend={() => undefined}
        onStop={() => undefined}
        sessionId="s1"
        branch="main"
      />,
    )
    const meter = screen.getByTestId('usage-meter')
    const send = screen.getByRole('button', { name: '发送' })
    const order = meter.compareDocumentPosition(send)
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('占用分档上色', () => {
  it('超过 85% 用危险色，进度条宽度跟着占用率', () => {
    savedOn(
      'main',
      report({
        context: {
          tokens: 180_000,
          window: 200_000,
          utilization: 0.9,
          parts: null,
        },
      }),
    )
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    const meter = screen.getByTestId('usage-meter')
    expect(meter.textContent).toContain('上下文已用 90%')
    // 分档上色（阈值见 lib/usage.ts）与条宽都落在 DOM 上，不只是数字。
    expect(meter.innerHTML).toContain('text-danger')
    expect(meter.querySelector('span[style]')?.getAttribute('style')).toContain('width: 90%')
  })

  it('60–85% 用警示色，≤60% 用中性色', () => {
    savedOn(
      'main',
      report({
        context: { tokens: 140_000, window: 200_000, utilization: 0.7, parts: null },
      }),
    )
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    expect(screen.getByTestId('usage-meter').innerHTML).toContain('text-warn')
    cleanup()

    savedOn('main', report())
    withLocale(<UsageMeter sessionId="s1" branch="main" />)
    const neutral = screen.getByTestId('usage-meter').innerHTML
    expect(neutral).not.toContain('text-warn')
    expect(neutral).not.toContain('text-danger')
  })
})

describe('UsageDetail（悬浮明细）', () => {
  it('列出三块估算、标注估算口径，并给出缓存与压缩的真实数', () => {
    withLocale(<UsageDetail usage={report()} />)
    const text = screen.getByTestId('usage-detail').textContent ?? ''
    expect(text).toContain('上下文已用 36%')
    expect(text).toContain('72k')
    expect(text).toContain('200k')
    // 三块清单：每块带 ~ 前缀（估算），并注明口径
    for (const [label, value] of [
      ['系统提示词', '~2k'],
      ['工具定义', '~6k'],
      ['对话消息', '~64k'],
    ]) {
      expect(text).toContain(label)
      expect(text).toContain(value)
    }
    expect(text).toContain('分块按字符占比估算')
    // 真实数照旧
    expect(text).toContain('缓存读')
    expect(text).toContain('命中率')
    expect(text).toContain('78%')
    expect(text).toContain('压缩次数')
    expect(text).toContain('压缩后')
  })

  it('没有缓存写入计数（OpenAI 系）时那一行不出现，而不是显示 0', () => {
    withLocale(<UsageDetail usage={report()} />)
    expect(screen.getByTestId('usage-detail').textContent).not.toContain('缓存写')
  })

  it('没有分块数据时不画堆叠条与清单，但真实数照旧', () => {
    withLocale(
      <UsageDetail
        usage={report({
          context: { tokens: 3_000, window: null, utilization: null, parts: null },
          cache: { read_tokens: 0, write_tokens: null, hit_ratio: 0 },
        })}
      />,
    )
    const text = screen.getByTestId('usage-detail').textContent ?? ''
    expect(text).not.toContain('系统提示词')
    expect(text).toContain('上下文占用')  // 没有窗口就不摆占用率
    expect(text).toContain('窗口未知：可设 AVID_CONTEXT_WINDOW')
    expect(text).toContain('3k')
    expect(text).toContain('缓存读')
    expect(text).toContain('0%')
  })
})
