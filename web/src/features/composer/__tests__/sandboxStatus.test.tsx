// @vitest-environment jsdom
/**
 * 沙箱状态的可见性用例（阶段 26）。
 *
 * 产品规则里"full 必须是明确、可见的显式授权"在界面上的落点就是这一格：
 * 它必须把**模式**与**服务端实测的后端可用性**分开说，而不是照模式猜。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ReactNode } from 'react'

import type { SandboxState } from '../../../api/types'
import { LocaleProvider } from '../../../lib/i18n'
import { SandboxStatus } from '../components/SandboxStatus'

function withLocale(node: ReactNode) {
  return render(<LocaleProvider>{node}</LocaleProvider>)
}

function state(overrides: Partial<SandboxState> = {}): SandboxState {
  return {
    backend: 'bwrap',
    available: true,
    network: true,
    reason: null,
    landlock_abi: 3,
    ...overrides,
  }
}

afterEach(cleanup)

describe('沙箱状态标记', () => {
  it('manual/auto + 后端可用 → 工作区（无出网）', () => {
    for (const mode of ['manual', 'auto'] as const) {
      cleanup()
      withLocale(<SandboxStatus mode={mode} sandbox={state()} />)
      expect(screen.getByText('沙箱：工作区（无出网）')).toBeTruthy()
    }
  })

  it('full → 已禁用（不依赖服务端上报）', () => {
    withLocale(<SandboxStatus mode="full" sandbox={state()} />)
    expect(screen.getByText('沙箱：已禁用')).toBeTruthy()
  })

  it('后端不可用时如实说"不可用"，而不是照模式说"工作区"', () => {
    withLocale(
      <SandboxStatus
        mode="manual"
        sandbox={state({ backend: 'none', available: false, reason: '找不到 bubblewrap（bwrap）' })}
      />,
    )
    expect(screen.getByText('沙箱：不可用')).toBeTruthy()
  })

  it('meta 还没回来时不声称后端可用（按模式说工作区，色调中性）', () => {
    withLocale(<SandboxStatus mode="manual" sandbox={null} />)
    const badge = screen.getByLabelText('沙箱')
    expect(badge.textContent).toBe('沙箱：工作区（无出网）')
    expect(badge.className).toContain('bg-sand')
  })
})
