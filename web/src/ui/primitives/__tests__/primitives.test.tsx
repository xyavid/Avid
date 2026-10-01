// @vitest-environment jsdom
/*
 * ui 原语的行为用例。
 *
 * 只测"行为契约"，不测类名：类名是设计决策，会随视觉调整而变，
 * 把 `bg-card` 钉进测试只会让每次调色都来改测试，却保护不了任何东西。
 * 这里钉的是三件容易在重构中被弄丢、且真会伤到用户的事：
 *   1. 按钮默认不提交表单（type="button"）；
 *   2. loading 一定伴随 disabled（防重复提交）；
 *   3. 对话框关闭而不渲染、Esc 能关、可访问名等于标题。
 * 无 jest-dom 依赖（该包不在 devDependencies 里），断言直接用原生 DOM 属性。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Badge, Button, Dialog, Tooltip } from '../index'
import { CheckCircleIcon, InfoIcon, PlusIcon } from '../../icons'

// vitest 未开 globals，Testing Library 不会自动清理挂载的树；
// 不清理会让上一条用例的 DOM 留在 document 里，getByRole 直接报"找到多个"。
afterEach(cleanup)

describe('Button', () => {
  it('默认 type="button"，不会误触发表单提交', () => {
    render(<Button>发送</Button>)
    expect(screen.getByRole('button').getAttribute('type')).toBe('button')
  })

  it('显式 type="submit" 时透传', () => {
    render(<Button type="submit">提交</Button>)
    expect(screen.getByRole('button').getAttribute('type')).toBe('submit')
  })

  it('loading 时 disabled 为真，且不渲染调用方的图标', () => {
    render(
      <Button loading icon={<PlusIcon />}>
        运行
      </Button>,
    )
    const button = screen.getByRole('button') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.getAttribute('aria-busy')).toBe('true')
  })

  it('透传 onClick', () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>点我</Button>)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})

describe('Badge', () => {
  it('渲染 children', () => {
    render(<Badge tone="ok">已完成</Badge>)
    expect(screen.getByText('已完成')).toBeTruthy()
  })
})

describe('Dialog', () => {
  it('open=false 时不渲染', () => {
    render(
      <Dialog open={false} title="删除会话" onClose={() => {}}>
        内容
      </Dialog>,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('open=true 时 role="dialog" 可达，可访问名等于标题', () => {
    render(
      <Dialog open title="删除会话" description="该操作不可撤销" onClose={() => {}}>
        内容
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: '删除会话' })).toBeTruthy()
  })

  it('Esc 触发 onClose', () => {
    const onClose = vi.fn()
    render(
      <Dialog open title="删除会话" onClose={onClose}>
        内容
      </Dialog>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('点击遮罩触发 onClose', () => {
    const onClose = vi.fn()
    const { container } = render(
      <Dialog open title="删除会话" onClose={onClose}>
        内容
      </Dialog>,
    )
    const scrim = container.querySelector('[aria-hidden="true"]')
    expect(scrim).toBeTruthy()
    fireEvent.click(scrim as Element)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('Tooltip', () => {
  it('label 落在 role="tooltip" 节点上', () => {
    render(
      <Tooltip label="新建会话">
        <button type="button">新建</button>
      </Tooltip>,
    )
    expect(screen.getByRole('tooltip').textContent).toBe('新建会话')
  })
})

describe('Icons', () => {
  it('默认装饰性：aria-hidden="true" 且默认 16px', () => {
    const { container } = render(<PlusIcon />)
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(svg.getAttribute('aria-hidden')).toBe('true')
    expect(svg.getAttribute('width')).toBe('16')
    expect(svg.getAttribute('stroke-width')).toBe('1.5')
    expect(svg.getAttribute('fill')).toBe('none')
  })

  it('传 aria-label 时升级为语义节点（role="img"）', () => {
    render(<InfoIcon aria-label="提示" />)
    expect(screen.getByRole('img', { name: '提示' })).toBeTruthy()
  })

  it('size 档位逐档生效，className 可叠加', () => {
    const { container } = render(<CheckCircleIcon size={20} className="text-ok" />)
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(svg.getAttribute('width')).toBe('20')
    expect(svg.getAttribute('class')).toContain('text-ok')
  })
})
