// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Markdown } from '../Markdown'

/**
 * 渲染层：模型输出直接进 DOM，所以这里最关键的两条是
 * **不注入 HTML**（全程 React 元素）与**SVG 走 <img> 数据地址**（不内联、不执行脚本）。
 */
afterEach(cleanup)

describe('markdown 渲染', () => {
  it('标题、段落、列表、行内代码都落成对应的元素', () => {
    const { container } = render(
      <Markdown>{'## 小标题\n\n段落里有 `code`。\n\n- 甲\n- 乙\n'}</Markdown>,
    )

    expect(container.querySelector('h2')?.textContent).toBe('小标题')
    expect(container.querySelector('p')?.textContent).toBe('段落里有 code。')
    expect(container.querySelector('p code')?.textContent).toBe('code')
    expect([...container.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['甲', '乙'])
  })

  it('围栏代码块：语言标在、内容在、有复制按钮', () => {
    const { container } = render(<Markdown>{'```py\nprint(1)\n```'}</Markdown>)

    expect(screen.getByText('py')).toBeTruthy()
    // 高亮把代码拆成多个 token span，所以按整块文本断言（拼回去等于原文由
    // highlight 的单测保证）
    expect(container.querySelector('pre code')?.textContent).toBe('print(1)')
    expect(screen.getByRole('button', { name: /复制/ })).toBeTruthy()
  })

  it('复制按钮把代码原文写进剪贴板', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<Markdown>{'```\n喂\n```'}</Markdown>)

    fireEvent.click(screen.getByRole('button', { name: /复制/ }))

    expect(writeText).toHaveBeenCalledWith('喂')
  })

  it('svg 围栏渲染成图片（data:image/svg+xml），不是内联 svg，也不当文本显示', () => {
    const source = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'
    const { container } = render(<Markdown>{'```svg\n' + source + '\n```'}</Markdown>)

    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toMatch(/^data:image\/svg\+xml;base64,/)
    // 不内联：DOM 里不能出现 svg 元素，源码也不能以文本形式留在页面上
    expect(container.querySelector('svg')).toBeNull()
    expect(container.textContent).not.toContain('<circle')
  })

  it('html 围栏里其实是 svg 时，也按图渲染（模型常这么写）', () => {
    const { container } = render(
      <Markdown>{'```html\n<div>不是图</div>\n```\n\n```html\n<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>\n```'}</Markdown>,
    )

    // 第一块仍是代码，第二块变图
    expect(container.querySelectorAll('img')).toHaveLength(1)
    expect(container.textContent).toContain('<div>不是图</div>')
  })

  it('原文里的 HTML 与脚本不会变成元素', () => {
    const { container } = render(
      <Markdown>{'<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>'}</Markdown>,
    )

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<script>alert(1)</script>')
  })

  it('链接带 target/rel，危险协议不是链接', () => {
    const { container } = render(<Markdown>{'[好](https://example.com) 与 [坏](javascript:alert(1))'}</Markdown>)

    const a = container.querySelector('a')
    expect(a?.getAttribute('href')).toBe('https://example.com')
    expect(a?.getAttribute('rel')).toBe('noreferrer noopener')
    expect(a?.getAttribute('target')).toBe('_blank')
    expect(container.querySelectorAll('a')).toHaveLength(1)
  })

  it('标题字号按正文拉档差（原来 h2/h3 比正文还小，等于没区分）', () => {
    const { container } = render(<Markdown>{'# 一\n\n## 二\n\n### 三\n\n#### 四\n\n##### 五'}</Markdown>)

    expect(container.querySelector('h1')?.className).toContain('text-chat-h1')
    expect(container.querySelector('h2')?.className).toContain('text-chat-h2')
    expect(container.querySelector('h3')?.className).toContain('text-chat-h3')
    expect(container.querySelector('h4')?.className).toContain('text-chat-h4')
    // 五级及以下不再更大，靠字重与衬线区分
    expect(container.querySelector('h5')?.className).toContain('text-chat')
    expect(container.querySelector('h5')?.className).not.toContain('text-chat-h')
  })

  it('带分割线的小节标题：线在上、标题居中', () => {
    const { container } = render(<Markdown>{'前文\n\n---\n\n## 选型决策线'}</Markdown>)

    const heading = container.querySelector('h2')
    expect(heading?.textContent).toBe('选型决策线')
    expect(heading?.className).toContain('text-center')
    // 线属于这一节，画在标题上方
    expect(container.querySelectorAll('hr')).toHaveLength(1)
  })

  it('没有分割线的标题不居中、也不凭空加线（别把居中写死在层级上）', () => {
    const { container } = render(<Markdown>{'## 普通小节\n\n### 三级\n\n# 大标题'}</Markdown>)

    expect(container.querySelectorAll('hr')).toHaveLength(0)
    for (const tag of ['h1', 'h2', 'h3']) {
      expect(container.querySelector(tag)?.className, tag).not.toContain('text-center')
    }
  })

  it('单独的分割线仍是普通横线（后面没跟标题）', () => {
    const { container } = render(<Markdown>{'甲\n\n---\n\n乙'}</Markdown>)

    expect(container.querySelectorAll('hr')).toHaveLength(1)
  })

  it('表格：表头有底色、单元格是发丝线整格', () => {
    const { container } = render(<Markdown>{'| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |'}</Markdown>)

    expect(container.querySelector('thead tr')?.className).toContain('bg-overlay-light')
    expect(container.querySelector('th')?.className).toContain('border-hairline')
    expect(container.querySelector('td')?.className).toContain('border-hairline')
  })

  it('软换行渲染成换行（不并成一行、也不多个空格）', () => {
    const { container } = render(<Markdown>{'第一行\n第二行'}</Markdown>)

    expect(container.querySelectorAll('p br')).toHaveLength(1)
  })

  it('硬换行（行尾两空格 / 反斜杠）同样断行', () => {
    const { container } = render(<Markdown>{'甲  \n乙\\\n丙'}</Markdown>)

    expect(container.querySelectorAll('p br')).toHaveLength(2)
  })

  it('GFM 任务列表：画成纸面上的小方框，不用原生 checkbox', () => {
    const { container } = render(<Markdown>{'- [x] 做完了\n- [ ] 还没做'}</Markdown>)

    expect(container.querySelectorAll('input')).toHaveLength(0)
    expect(screen.getByLabelText('已完成')).toBeTruthy()
    expect(screen.getByLabelText('未完成')).toBeTruthy()
  })

  it('图片：data: 地址画出来，外链降级成链接（CSP 只允许 self/data，且外链图 = 追踪像素）', () => {
    const { container } = render(
      <Markdown>{'![外图](https://a.example/x.png)\n\n![内嵌](data:image/png;base64,AAAA)'}</Markdown>,
    )

    const images = container.querySelectorAll('img')
    expect(images).toHaveLength(1)
    expect(images[0]?.getAttribute('src')).toMatch(/^data:image\/png/)
    expect(screen.getByRole('link', { name: '外图' }).getAttribute('href')).toBe('https://a.example/x.png')
  })

  it('HTML 块原样成文本（保留自己的换行），不成元素', () => {
    const { container } = render(<Markdown>{'<div class="x">\n块内容\n</div>'}</Markdown>)

    expect(container.querySelector('.x')).toBeNull()
    expect(container.textContent).toContain('<div class="x">')
    expect(container.textContent).toContain('块内容')
  })
})
