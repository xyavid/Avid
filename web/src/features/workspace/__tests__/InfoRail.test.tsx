// @vitest-environment jsdom
/**
 * 右栏信息面板的用例。
 *
 * 这里最重要的一条是**null ≠ 0**：`model` / `workspaceRoot` 为 null 时显示「—」，
 * 而不是渲染成 0 或空白。断言写成"取那一行的 textContent 检查"而不是"页面上没有 0"，
 * 因为 tokens / 条数确实可能是真的 0，把它们一起禁掉就把这条口径测反了。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary, WorkspaceSummary } from '../../../../api/types'
import { InfoRail } from '../components/InfoRail'

afterEach(cleanup)

function ws(id: string, name: string, isDefault = false): WorkspaceSummary {
  return {
    id,
    root: `/w/${id}`,
    name,
    created_at: 0,
    last_used_at: 0,
    default_permission: 'manual',
    is_default: isDefault,
  }
}

function session(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: 's-1',
    name: '一个会话',
    created_at: 0,
    storage_version: 1,
    parent_session_id: null,
    workspace: null,
    message_count: 3,
    active_run_id: null,
    truncated_tail: false,
    ...overrides,
  }
}

function renderRail(overrides: Partial<Parameters<typeof InfoRail>[0]> = {}) {
  const props = {
    session: session(),
    branch: 'main',
    model: 'deepseek/deepseek-v4.1-flash' as string | null,
    workspaces: [ws('ws-1', 'Avid', true)],
    workspaceRoot: '/home/fishy/Avid' as string | null,
    tokens: 12_300,
    messageCount: 3,
    onManageWorkspaces: vi.fn(),
    ...overrides,
  }
  const view = render(<InfoRail {...props} />)
  return { ...view, props }
}

/** 取某一行的值单元格：标签与值同在一个 `div` 里，这样才能只对那一行断言。 */
function rowOf(label: string): HTMLElement {
  const node = screen.getByText(label).parentElement
  if (node === null) throw new Error(`找不到「${label}」所在的行`)
  return node
}

describe('InfoRail 本次对话', () => {
  it('还没选中会话时只说「还没有选中会话」，不摆一排 0', () => {
    renderRail({ session: null })

    expect(screen.getByText('还没有选中会话')).toBeTruthy()
    expect(screen.queryByText('工作目录')).toBeNull()
    expect(screen.queryByText('累计 tokens')).toBeNull()
  })

  it('模型未知时显示「—」而不是 0 或空白', () => {
    renderRail({ model: null })

    expect(rowOf('模型').textContent).toContain('—')
    expect(rowOf('模型').textContent).not.toContain('0')
  })

  it('工作目录缺失时显示「未归属」，区别于"还没选会话"', () => {
    renderRail({ workspaceRoot: null })

    expect(rowOf('工作目录').textContent).toContain('未归属')
  })

  it('分支为空串时也显示「—」（空白不是值）', () => {
    renderRail({ branch: '' })

    expect(rowOf('分支').textContent).toContain('—')
  })

  it('条数与 tokens 照实显示，0 就是 0', () => {
    renderRail({ tokens: 0, messageCount: 0 })

    expect(rowOf('累计 tokens').textContent).toContain('0')
    expect(rowOf('条数').textContent).toContain('0')
  })
})

describe('InfoRail 工作区卡', () => {
  it('超过 4 条时只列前 4 条并给「还有 N 个」', () => {
    renderRail({
      workspaces: [ws('a', '甲'), ws('b', '乙'), ws('c', '丙'), ws('d', '丁'), ws('e', '戊')],
    })

    expect(screen.getByText('甲')).toBeTruthy()
    expect(screen.getByText('丁')).toBeTruthy()
    expect(screen.queryByText('戊')).toBeNull()
    expect(screen.getByText('还有 1 个')).toBeTruthy()
  })

  it('正好 4 条时不显示「还有」', () => {
    renderRail({
      workspaces: [ws('a', '甲'), ws('b', '乙'), ws('c', '丙'), ws('d', '丁')],
    })

    expect(screen.queryByText(/还有 \d+ 个/)).toBeNull()
  })

  it('一个工作区都没登记时说明这一点', () => {
    renderRail({ workspaces: [] })

    expect(screen.getByText('还没有登记任何工作区')).toBeTruthy()
  })

  it('底部显示进程绑定工作区的 root（title 给全路径）', () => {
    renderRail({
      workspaces: [ws('ws-0', '别的'), ws('ws-1', 'Avid', true)],
    })

    expect(screen.getByText('/w/ws-1').getAttribute('title')).toBe('/w/ws-1')
  })

  it('没有进程绑定工作区时显示「—」而不是空行', () => {
    renderRail({ workspaces: [ws('ws-0', '别的')] })

    expect(screen.getByText('—')).toBeTruthy()
  })

  it('「管理」触发 onManageWorkspaces', () => {
    const onManageWorkspaces = vi.fn()
    renderRail({ onManageWorkspaces })

    fireEvent.click(screen.getByRole('button', { name: '管理工作区' }))

    expect(onManageWorkspaces).toHaveBeenCalledTimes(1)
  })
})
