// @vitest-environment jsdom
/**
 * 富文本渲染的契约用例。
 *
 * 需要 jsdom 是因为要真的把 markdown 挂成 DOM 再查元素；本文件是**契约钉子**，
 * 钉住两类东西：
 *   1. 结构正确：标题 / 列表 / 行内 code / 代码块 / 表格 / 链接真的成为元素，
 *      而不是带着 `#`、`-`、`|` 的纯文本（这正是本次改动的目的）；
 *   2. **安全边界**：正文来自模型，原始 HTML 既不能生成元素、也不能执行。
 *      这条一旦被谁顺手加上 `rehype-raw` 就会红——它是本文件里最该红的一条。
 *
 * 另一条与 jsdom 有关的边界要说清：**tailwind 类不在这里计算**。jsdom 不加载
 * 样式表，`getComputedStyle` 只会给出 UA 默认值（`strong` 因此恒为 700），
 * 所以"字重上限 500"这条只能断言类名——与 SessionNav.test.tsx 里的同款说明一致。
 * 产物里 `.font-medium{font-weight:500}` 由构建步骤复核（见验收命令里的 css 检查）。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Markdown } from '../Markdown'

afterEach(cleanup)

describe('Markdown 结构', () => {
  it('标题 / 列表 / 行内 code / 代码块 / 表格 / 链接都渲染成元素', () => {
    const { container } = render(
      <Markdown
        source={[
          '# 一级标题',
          '',
          '段落里有 `inline code` 与 **加粗**。',
          '',
          '- 甲',
          '- 乙',
          '',
          '```ts',
          'const x = 1',
          '```',
          '',
          '| 列 A | 列 B |',
          '| --- | --- |',
          '| 1 | 2 |',
          '',
          '[外链](https://example.com)',
        ].join('\n')}
      />,
    )

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('一级标题')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)

    // 行内 code：必须是独立的 `code` 元素并且带浅底（参考截图里它有底色）。
    const inline = screen.getByText('inline code')
    expect(inline.tagName).toBe('CODE')
    expect(inline.className).toContain('bg-inset')

    // 代码块：外层是 `pre`，底色走 code-bg，并且允许横向滚动（长行不撑破正文列）。
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.className).toContain('bg-code-bg')
    expect(pre?.className).toContain('overflow-x-auto')

    expect(screen.getByRole('table')).toBeTruthy()

    // 本地应用里点链接不该把当前页导航走：必须新窗口 + noopener。
    const link = screen.getByRole('link')
    expect(link.getAttribute('href')).toBe('https://example.com')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer noopener')
  })

  it('strong 落在 font-medium（500）上，且不含 600+ 的字重类', () => {
    const { container } = render(<Markdown source={'**不要**用纯白'} />)

    const strong = container.querySelector('strong')
    expect(strong).not.toBeNull()
    expect(strong?.className).toContain('font-medium')
    expect(strong?.className).not.toMatch(/font-(semibold|bold|black)/)
    // 计算样式读不到：jsdom 无样式表，UA 默认给 strong 就是 700（见文件头说明）。
  })

  it('嵌套列表用 circle，与外层 disc 区分', () => {
    const { container } = render(<Markdown source={'- 外层\n  - 内层\n'} />)

    const lists = container.querySelectorAll('ul')
    expect(lists).toHaveLength(2)
    expect(lists[0]?.className).toContain('list-disc')
    // Tailwind 默认表里**没有** `list-circle`，写那个类名会静默不生成 CSS，
    // 所以这里钉的是任意值写法；产物侧另有 `list-style-type:circle` 的复核。
    expect(lists[0]?.className).toContain('[&_ul]:list-circle')
  })
})

describe('Markdown 安全边界', () => {
  it('原始 HTML 不生成元素、不执行，只作为字面量文本出现', () => {
    const { container } = render(
      <Markdown source={'<img src=x onerror="window.__avidXss = 1">'} />,
    )

    // 没有 `rehype-raw`：html 节点被转义成文本，因此不可能有 img / script。
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect((window as unknown as { __avidXss?: number }).__avidXss).toBeUndefined()
    expect(screen.getByText(/<img src=x onerror=/)).toBeTruthy()
  })

  it('行内 HTML 也不会被解析成元素', () => {
    const { container } = render(<Markdown source={'前 <b>粗</b> 后'} />)

    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText(/前 <b>粗<\/b> 后/)).toBeTruthy()
  })
})

describe('Markdown 边界', () => {
  it('空内容（含全空白）返回 null，不留占位节点', () => {
    const first = render(<Markdown source="" />)
    expect(first.container.firstChild).toBeNull()
    cleanup()

    const second = render(<Markdown source={'  \n  '} />)
    expect(second.container.firstChild).toBeNull()
  })

  it('流式中截断在半条语法里也不抛错，按字面量渲染', () => {
    const { container } = render(<Markdown source={'**还没写完'} streaming />)

    expect(screen.getByText('**还没写完')).toBeTruthy()
    // 流式标记挂在根节点上，供外层做"尾部极轻淡入"这类处理；
    // 本组件自己**不加**闪烁光标（本视觉禁闪烁，见 hana 报告 §8.2）。
    expect(container.querySelector('[data-streaming="true"]')).not.toBeNull()
    expect(container.querySelector('.animate-pulse')).toBeNull()
  })

  it('非流式时不带 data-streaming 标记', () => {
    const { container } = render(<Markdown source={'普通正文'} />)
    expect(container.querySelector('[data-streaming]')).toBeNull()
  })
})
