// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ToolCard } from '../ToolCard'

afterEach(cleanup)

/** 差异行 → `种类|整行文字`：元素内部被拆成多个文本节点，按 textContent 读最实在。 */
function diffRows(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-diff]')].map(
    (el) => `${el.getAttribute('data-diff')}|${el.textContent ?? ''}`,
  )
}

describe('ToolCard（双形态：折叠单行 ↔ 展开完整卡）', () => {
  it('折叠单行：动作 + 目标 + 耗时 + 状态，不渲染正文', () => {
    render(
      <ToolCard icon="terminal" verb="执行" target="uv run pytest -q" status="ok" durationMs={8400}>
        长输出内容
      </ToolCard>,
    )

    expect(screen.getByText('执行')).toBeTruthy()
    expect(screen.getByText('uv run pytest -q')).toBeTruthy()
    expect(screen.getByText('8.4s')).toBeTruthy()
    expect(screen.getByLabelText('成功')).toBeTruthy()
    expect(screen.queryByText('长输出内容')).toBeNull()
  })

  it('点开成完整卡：正文出现，点收起回到单行', () => {
    render(
      <ToolCard icon="terminal" verb="执行" target="uv run pytest -q" status="ok">
        271 passed
      </ToolCard>,
    )

    expect(screen.queryByText('271 passed')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /执行/ }))
    expect(screen.getByText('271 passed')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '收起' }))
    expect(screen.queryByText('271 passed')).toBeNull()
  })

  it('defaultExpanded 直接以完整卡呈现', () => {
    render(
      <ToolCard icon="file-frame" verb="读取" target="styles/tokens.css" defaultExpanded>
        --space-2 …
      </ToolCard>,
    )

    expect(screen.getByText('styles/tokens.css')).toBeTruthy()
    expect(screen.getByText('--space-2 …')).toBeTruthy()
  })

  it('状态三态各有标记：成功勾 / 失败叉 / 运行中呼吸点', () => {
    const ok = render(<ToolCard icon="terminal" verb="t" status="ok">x</ToolCard>)
    expect(screen.getByLabelText('成功')).toBeTruthy()
    ok.unmount()

    const failed = render(<ToolCard icon="terminal" verb="t" status="failed">x</ToolCard>)
    expect(screen.getByLabelText('失败')).toBeTruthy()
    failed.unmount()

    render(<ToolCard icon="terminal" verb="t" status="running">x</ToolCard>)
    expect(screen.getByLabelText('运行中')).toBeTruthy()
  })

  it('没有目标时只剩动作词（不摆一个空栏位）', () => {
    const { container } = render(<ToolCard icon="globe" verb="mcp__fs__stat" status="ok" />)

    expect(container.querySelector('svg')).toBeTruthy()
    expect(screen.getByText('mcp__fs__stat')).toBeTruthy()
  })

  it('子步骤按任务分组列出（展开态），各自带自己的动作与状态', () => {
    render(
      <ToolCard
        icon="git-branch"
        verb="子智能体"
        target="2 个子任务"
        status="ok"
        workspaceRoot="/w"
        defaultExpanded
        steps={[
          { task: '前端时间线', callId: 'k1', name: 'edit_file', args: '{"path":"/w/a.tsx"}', status: 'ok' },
          { task: '内核核对', callId: 'k2', name: 'read_file', args: '{"path":"/w/b.py"}', status: 'running' },
          { task: '前端时间线', callId: 'k3', name: 'bash', args: '{"command":"pnpm verify"}', status: 'failed' },
        ]}
      >
        汇总
      </ToolCard>,
    )

    expect(screen.getByText('前端时间线')).toBeTruthy()
    expect(screen.getByText('内核核对')).toBeTruthy()
    expect(screen.getByText('a.tsx')).toBeTruthy()
    expect(screen.getByText('b.py')).toBeTruthy()
    expect(screen.getByText('pnpm verify')).toBeTruthy()
    expect(screen.getByLabelText('失败')).toBeTruthy()
    expect(screen.getAllByLabelText('运行中')).toHaveLength(1)
  })
})

describe('ToolCard 详情视图（文件类工具点开之后）', () => {
  it('差异详情：增删行 + 计数 + 语言标，结果原话作注脚', () => {
    const { container } = render(
      <ToolCard
        icon="pencil"
        verb="编辑"
        target="a.py"
        status="ok"
        defaultExpanded
        detail={{
          kind: 'diff',
          lang: 'python',
          before: 'a\nb\nc',
          after: 'a\nB\nc',
          note: '已替换 a.py 中的 1 处文本',
        }}
      >
        已替换 a.py 中的 1 处文本
      </ToolCard>,
    )

    expect(screen.getByText('python')).toBeTruthy()
    expect(screen.getByText('+1')).toBeTruthy()
    expect(screen.getByText('-1')).toBeTruthy()
    expect(screen.getByText('已替换 a.py 中的 1 处文本')).toBeTruthy()
    // 差异行按种类写在 DOM 上（端到端脚本按它读，不猜 class）
    expect(diffRows(container)).toEqual(['context| a', 'del|-b', 'add|+B', 'context| c'])
  })

  it('代码详情：行号从 offset 起数；结果本身就是内容时不再重复一遍', () => {
    const { container } = render(
      <ToolCard
        icon="file-frame"
        verb="读取"
        target="a.py"
        status="ok"
        defaultExpanded
        detail={{ kind: 'code', lang: 'python', text: 'x = 1\ny = 2', startLine: 100, note: null }}
      >
        原文不该出现
      </ToolCard>,
    )

    expect(screen.getByText('python')).toBeTruthy()
    expect(container.textContent).toContain('100\n101') // 行号列从 100 起
    expect(screen.queryByText('原文不该出现')).toBeNull()
  })

  it('没有详情时不画视图：原文照旧（渲染层只照 detail 画，不自己判断）', () => {
    render(
      <ToolCard icon="terminal" verb="执行" target="ls" status="ok" defaultExpanded detail={null}>
        结果原文
      </ToolCard>,
    )

    expect(screen.getByText('结果原文')).toBeTruthy()
  })
})
