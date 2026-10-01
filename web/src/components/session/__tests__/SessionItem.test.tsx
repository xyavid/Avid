// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { SessionItem } from '../SessionItem'

describe('SessionItem（组件墙 §会话列表项 + 报告 §7.1）', () => {
  afterEach(cleanup)

  it('标题与元信息；操作按钮默认隐藏（hover/focus-within 才现形）', () => {
    render(<SessionItem title="整理会议纪要" meta="09:44" />)

    expect(screen.getByText('整理会议纪要')).toBeTruthy()
    const actions = document.querySelector('[data-testid="session-actions"]') as HTMLElement
    expect(actions.className).toContain('opacity-0')
    expect(actions.className).toContain('group-hover:opacity-100')
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

  it('置顶/归档操作按钮就位（阶段 4 接动作）', () => {
    render(<SessionItem title="周报草稿" meta="09:44" />)

    expect(screen.getByRole('button', { name: '置顶' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '归档' })).toBeTruthy()
  })
})
