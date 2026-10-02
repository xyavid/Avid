// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SessionItem } from '../SessionItem'

describe('SessionItem（组件墙 §会话列表项 + 报告 §7.1）', () => {
  afterEach(cleanup)

  it('只读形态（不给回调）：一个动作按钮都不渲染（组件墙静态演示走这条）', () => {
    render(<SessionItem title="整理会议纪要" meta="09:44" />)

    expect(screen.getByText('整理会议纪要')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('给回调才有动作：默认透明，hover/focus-within 才现形', () => {
    render(<SessionItem title="整理会议纪要" meta="09:44" onRename={() => {}} onDelete={() => {}} />)

    const actions = document.querySelector('[data-testid="session-actions"]') as HTMLElement
    expect(actions.className).toContain('opacity-0')
    expect(actions.className).toContain('group-hover:opacity-100')
    expect(screen.getByRole('button', { name: '重命名：整理会议纪要' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '删除：整理会议纪要' })).toBeTruthy()
  })

  it('active 态：标题转 accent（500 字重，遵纪律不用墙的 600）', () => {
    render(<SessionItem title="Hana 界面设计报告" meta="进行中" active />)

    const title = screen.getByText('Hana 界面设计报告')
    expect(title.className).toContain('text-accent')
    expect(title.className).toContain('font-medium')
  })

  it('流式会话带 5px accent 呼吸圆点', () => {
    render(<SessionItem title="整理会议纪要" meta="流式生成中…" streaming />)

    expect(document.querySelector('[data-testid="streaming-dot"]')).toBeTruthy()
  })

  it('重命名：行内编辑，Enter 提交 trim 后的新名字', () => {
    const onRename = vi.fn()
    render(<SessionItem title="旧名字" meta="09:44" onRename={onRename} />)

    fireEvent.click(screen.getByRole('button', { name: '重命名：旧名字' }))
    const input = screen.getByLabelText('会话名称') as HTMLInputElement
    expect(input.value).toBe('旧名字')

    fireEvent.change(input, { target: { value: '  新名字  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onRename).toHaveBeenCalledWith('新名字')
    expect(screen.queryByLabelText('会话名称')).toBeNull()
  })

  it('重命名：Esc 取消；空名字与未改动都不提交', () => {
    const onRename = vi.fn()
    render(<SessionItem title="旧名字" meta="09:44" onRename={onRename} />)

    fireEvent.click(screen.getByRole('button', { name: '重命名：旧名字' }))
    fireEvent.keyDown(screen.getByLabelText('会话名称'), { key: 'Escape' })
    expect(onRename).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('会话名称')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '重命名：旧名字' }))
    const blank = screen.getByLabelText('会话名称')
    fireEvent.change(blank, { target: { value: '   ' } })
    fireEvent.keyDown(blank, { key: 'Enter' })
    expect(onRename).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '重命名：旧名字' }))
    fireEvent.keyDown(screen.getByLabelText('会话名称'), { key: 'Enter' })
    expect(onRename).not.toHaveBeenCalled()
  })

  it('删除：按下先出确认条（写清不可恢复），确认才回调', () => {
    const onDelete = vi.fn()
    render(<SessionItem title="周报草稿" meta="09:44" onDelete={onDelete} />)

    fireEvent.click(screen.getByRole('button', { name: '删除：周报草稿' }))
    expect(screen.getByText(/不可恢复/)).toBeTruthy()
    expect(onDelete).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('删除：取消收起确认条、不回调', () => {
    const onDelete = vi.fn()
    render(<SessionItem title="周报草稿" meta="09:44" onDelete={onDelete} />)

    fireEvent.click(screen.getByRole('button', { name: '删除：周报草稿' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onDelete).not.toHaveBeenCalled()
    expect(screen.queryByText(/不可恢复/)).toBeNull()
  })
})
