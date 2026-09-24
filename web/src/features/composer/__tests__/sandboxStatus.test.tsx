// @vitest-environment jsdom
/**
 * 沙箱状态标记：**只做气泡**。
 *
 * 产品规则里"full 必须是明确、可见的显式授权"原先落在输入条右侧那一格常驻文字上
 * （`沙箱：工作区（无出网）`）。产品决定把它收进悬停气泡：常驻的只剩一枚图标
 * （颜色仍然区分 ok / 中性 / 警告 / 危险，形态与复制、分叉的图标按钮一致），
 * "这次运行的物理边界到底是什么"这句话改为悬停或聚焦时才出现——它不占输入条的纵向
 * 空间，在窄窗口里也不会被挤成三行。**事实没有被删掉，只是不再常驻占位。**
 *
 * 用例分两层：映射规则用纯函数验（不必碰浮层），渲染层验"图标常驻、文字不常驻"
 * 与"聚焦后气泡里说得出那句话"。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ReactNode } from 'react'

import type { SandboxState } from '../../../api/types'
import { DEFAULT_LOCALE, LocaleProvider, translate } from '../../../lib/i18n'
import { SandboxStatus, resolveSandboxLabel } from '../components/SandboxStatus'

function withLocale(node: ReactNode) {
  return render(<LocaleProvider>{node}</LocaleProvider>)
}

/** 与组件里同一个 `t`（同一份词条表、同一个语言）。 */
const t = (key: string, vars?: Record<string, string | number>) =>
  translate(DEFAULT_LOCALE, key, vars)

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

describe('沙箱状态：映射规则', () => {
  it('manual / auto + 后端可用 → 工作区（无出网）', () => {
    for (const mode of ['manual', 'auto'] as const) {
      const view = resolveSandboxLabel(mode, state(), t)
      expect(view.text).toBe('工作区（无出网）')
      expect(view.tone).toBe('ok')
      expect(view.hint).toBe('这次运行的物理边界：工作区（无出网）。它不由模型决定。')
    }
  })

  it('full → 已禁用（不依赖服务端上报）', () => {
    const view = resolveSandboxLabel('full', state(), t)
    expect(view.text).toBe('已禁用')
    expect(view.tone).toBe('danger')
    expect(view.hint).toBe('这次运行的物理边界：已禁用。它不由模型决定。')
  })

  it('后端不可用时如实说"不可用"，而不是照模式说"工作区"', () => {
    const view = resolveSandboxLabel(
      'manual',
      state({ backend: 'none', available: false, reason: '找不到 bubblewrap（bwrap）' }),
      t,
    )
    expect(view.text).toBe('不可用')
    expect(view.tone).toBe('warn')
    expect(view.hint).toContain('沙箱不可用（找不到 bubblewrap（bwrap））')
    expect(view.hint).toContain('受管命令会逐个问你')
  })

  it('meta 还没回来时不声称后端可用（按模式说工作区，色调中性）', () => {
    const view = resolveSandboxLabel('manual', null, t)
    expect(view.text).toBe('工作区（无出网）')
    expect(view.tone).toBe('neutral')
  })
})

describe('沙箱状态：渲染', () => {
  it('常驻的只有一枚图标，文字不再常驻', () => {
    withLocale(<SandboxStatus mode="manual" sandbox={state()} />)
    const chip = screen.getByLabelText('沙箱')
    expect(chip.textContent).toBe('')
    expect(chip.querySelector('svg')).toBeTruthy()
  })

  it('聚焦（键盘 Tab 同样算）后气泡里给出那句物理边界', async () => {
    withLocale(<SandboxStatus mode="manual" sandbox={state()} />)
    fireEvent.focus(screen.getByLabelText('沙箱').parentElement as HTMLElement)
    await waitFor(
      () =>
        expect(
          screen.getByText('这次运行的物理边界：工作区（无出网）。它不由模型决定。'),
        ).toBeTruthy(),
      { timeout: 3000 },
    )
  })
})
