// @vitest-environment jsdom
/**
 * 组装层用例：空态、分组渲染、搜索过滤、点击选中。
 *
 * 需要 jsdom 是因为要真的挂载列表并点它；纯逻辑的边界（排序、兜底组、显示名）
 * 在 lib/__tests__/navTree.test.ts 里跑，不必让它们一起背 jsdom 的成本。
 *
 * `afterEach(cleanup)` 不是可选的：vitest 没开 globals 时 Testing Library 的自动
 * 清理不会注册，两次 render 的 DOM 会叠在一起，按文本查询就要命中多个节点。
 *
 * 依赖提醒：本文件加载组件 → 组件 import `ui/primitives` 与 `ui/icons`。
 * 那两个目录由并行的 teammate 提供（task-1），未落地时本文件的失败是
 * "模块解析失败"，不是导航列逻辑错。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary, WorkspaceSummary } from '../../../api/types'
import { SessionNav } from '../components/SessionNav'
import type { SessionNavProps } from '../components/SessionNav'

afterEach(cleanup)

function ws(id: string, name: string, lastUsedAt = 0): WorkspaceSummary {
  return {
    id,
    root: `/w/${id}`,
    name,
    created_at: 0,
    last_used_at: lastUsedAt,
    default_permission: null,
    is_default: false,
  }
}

function sess(id: string, name: string | null, workspaceId: string | null, createdAt = 0): SessionSummary {
  return {
    id,
    name,
    created_at: createdAt,
    storage_version: 1,
    parent_session_id: null,
    workspace:
      workspaceId === null
        ? null
        : { id: workspaceId, root: `/w/${workspaceId}`, name: null, default_permission: null },
    message_count: 2,
    active_run_id: null,
    truncated_tail: false,
  }
}

/** 用一份"最小可渲染"的 props 起底，用例只覆写关心的字段。 */
function renderNav(overrides: Partial<SessionNavProps> = {}) {
  const props: SessionNavProps = {
    sessions: [],
    workspaces: [],
    activeSessionId: null,
    runningSessionIds: new Set<string>(),
    collapsed: false,
    error: null,
    onToggleCollapsed: vi.fn(),
    onSelect: vi.fn(),
    onNewSession: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  }
  const view = render(<SessionNav {...props} />)
  return { ...view, props }
}

describe('SessionNav 空态', () => {
  it('一条会话都没有时说「还没有会话」，不说成搜索无结果', () => {
    renderNav()

    expect(screen.getByText('还没有会话')).toBeTruthy()
    expect(screen.queryByText('没有匹配的会话')).toBeNull()
  })

  it('error 非空时把错误原文贴出来', () => {
    renderNav({ error: '删除失败：会话正在运行（409 session_busy）' })

    expect(screen.getByRole('status').textContent).toContain('session_busy')
  })
})

describe('SessionNav 分组', () => {
  it('按工作区分组渲染工作区名与会话名', () => {
    renderNav({
      sessions: [sess('a1', '甲一', 'ws-a', 10), sess('b1', '乙一', 'ws-b', 10)],
      workspaces: [ws('ws-a', '甲项目'), ws('ws-b', '乙项目')],
    })

    expect(screen.getByText('甲项目')).toBeTruthy()
    expect(screen.getByText('乙项目')).toBeTruthy()
    expect(screen.getByText('甲一')).toBeTruthy()
    expect(screen.getByText('乙一')).toBeTruthy()
  })

  it('空工作区不出组', () => {
    renderNav({
      sessions: [sess('a1', '甲一', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目'), ws('ws-empty', '空项目')],
    })

    expect(screen.queryByText('空项目')).toBeNull()
  })
})

describe('SessionNav 搜索', () => {
  it('过滤后只剩命中项', () => {
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10), sess('a2', '重构鉴权', 'ws-a', 20)],
      workspaces: [ws('ws-a', '甲项目')],
    })

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '鉴权' } })

    expect(screen.getByText('重构鉴权')).toBeTruthy()
    expect(screen.queryByText('修登录')).toBeNull()
  })

  it('搜不到时说「没有匹配的会话」，与"还没有会话"分开', () => {
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
    })

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '不存在的词' } })

    expect(screen.getByText('没有匹配的会话')).toBeTruthy()
    expect(screen.queryByText('还没有会话')).toBeNull()
  })
})

describe('SessionNav 交互', () => {
  it('点击会话用它的 id 触发 onSelect', () => {
    const onSelect = vi.fn()
    renderNav({
      sessions: [sess('sess-42', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
      onSelect,
    })

    fireEvent.click(screen.getByText('修登录'))

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('sess-42')
  })

  it('新建按钮触发 onNewSession', () => {
    const onNewSession = vi.fn()
    renderNav({ onNewSession })

    fireEvent.click(screen.getByLabelText('新建会话'))

    expect(onNewSession).toHaveBeenCalledTimes(1)
  })

  it('收起按钮按当前状态给出正确文案并回调', () => {
    const onToggleCollapsed = vi.fn()
    renderNav({ onToggleCollapsed })

    fireEvent.click(screen.getByLabelText('收起会话列表'))

    expect(onToggleCollapsed).toHaveBeenCalledTimes(1)
  })

  it('折叠态渲染首字母头像列表，点击仍用 id 触发 onSelect', () => {
    const onSelect = vi.fn()
    renderNav({
      sessions: [sess('sess-9', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
      collapsed: true,
      onSelect,
    })

    expect(screen.queryByRole('searchbox')).toBeNull()
    fireEvent.click(screen.getByLabelText('修登录'))

    expect(onSelect).toHaveBeenCalledWith('sess-9')
  })

  it('删除先弹确认：文案说清不可撤销与 409 session_busy，确认后才回调 onDelete', () => {
    const onDelete = vi.fn()
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
      onDelete,
    })

    fireEvent.click(screen.getByLabelText('删除会话 修登录'))

    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('不可撤销')
    expect(dialog.textContent).toContain('session_busy')
    // 弹窗没确认之前不该动数据
    expect(onDelete).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '删除' }))

    expect(onDelete).toHaveBeenCalledWith('a1')
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('相对时间由上层注入', () => {
  const NOW = Date.UTC(2024, 0, 10, 12, 0, 0)

  it('给了 now 才显示相对时间', () => {
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', NOW - 3 * 60_000)],
      workspaces: [ws('ws-a', '甲项目')],
      now: NOW,
    })

    expect(screen.getByText('2 条 · 3 分钟前')).toBeTruthy()
  })

  it('不给 now 就只显示条数，不自己读时钟', () => {
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', NOW - 3 * 60_000)],
      workspaces: [ws('ws-a', '甲项目')],
    })

    expect(screen.getByText('2 条')).toBeTruthy()
    expect(screen.queryByText(/分钟前|刚刚|小时前/)).toBeNull()
  })
})

describe('会话列表项的签名交互（报告 §7.1，不许省）', () => {
  it('行内操作默认 opacity-0，hover / focus-within 才淡入', () => {
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
    })

    // 这里断言 className 而不是计算样式：jsdom 不加载 Tailwind，没有可算的样式表；
    // 而"默认透明、hover/focus-within 淡入"这条恰恰只在类名上表达。它是契约钉子，
    // 不是实现细节——被删掉就再也读不出这条交互了。
    const actions = screen.getByLabelText('删除会话 修登录').parentElement
    const className = actions?.className ?? ''
    expect(className).toContain('opacity-0')
    expect(className).toContain('group-hover:opacity-100')
    expect(className).toContain('group-focus-within:opacity-100')
    expect(className).toContain('transition-opacity')
    // 看不见但可点是最糟的组合：淡出期间必须同时关掉指针事件
    expect(className).toContain('pointer-events-none')
    expect(className).toContain('group-hover:pointer-events-auto')
  })

  it('双击标题进就地编辑，Enter 提交新名字', () => {
    const onRename = vi.fn()
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
      onRename,
    })

    fireEvent.doubleClick(screen.getByText('修登录'))
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: '改过的名字' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onRename).toHaveBeenCalledTimes(1)
    expect(onRename).toHaveBeenCalledWith('a1', '改过的名字')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('blur 与 Enter 走同一条提交路径，一次编辑只提交一次', () => {
    const onRename = vi.fn()
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
      onRename,
    })

    fireEvent.doubleClick(screen.getByText('修登录'))
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: '改名后失焦' } })
    fireEvent.blur(input)
    // 卸载后可能再补一次 blur；重复提交必须被挡在 ref 那道闸上
    fireEvent.blur(input)

    expect(onRename).toHaveBeenCalledTimes(1)
    expect(onRename).toHaveBeenCalledWith('a1', '改名后失焦')
  })

  it('Esc 取消改名，不提交也不留输入框', () => {
    const onRename = vi.fn()
    renderNav({
      sessions: [sess('a1', '修登录', 'ws-a', 10)],
      workspaces: [ws('ws-a', '甲项目')],
      onRename,
    })

    fireEvent.doubleClick(screen.getByText('修登录'))
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: '不该被提交' } })
    fireEvent.keyDown(input, { key: 'Escape' })

    expect(onRename).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})
