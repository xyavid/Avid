/**
 * 解析用例：语法按 CommonMark/GFM 标准实现（`@lezer/markdown`），这里钉的是
 * **我们的翻译层**——哪些节点落到哪个 kind、以及四条显式约定的行为：
 *   1. 未识别节点按原文当纯文本（HTML 块/标签进不了 DOM）；
 *   2. 引用链接自己查定义（Lezer 不校验引用定义，查不到按字面文本）；
 *   3. 软换行渲染成断行（有意偏离，对话场景的选择）；
 *   4. 实体只解数值与常用名，表外按原文。
 * 末尾单独一组是**协议闸门**：模型输出里进 DOM 属性的只有链接与图片两处。
 */

import { describe, expect, it } from 'vitest'

import type { Block, Inline } from '../parse'
import { parseBlocks } from '../parse'

function textOf(inline: Inline[]): string {
  return inline
    .map((node) => {
      if (node.kind === 'text' || node.kind === 'code') return node.text
      if ('children' in node) return textOf(node.children)
      return ''
    })
    .join('')
}

function kinds(blocks: Block[]): string[] {
  return blocks.map((block) => block.kind)
}

function paragraphIn(text: string): Inline[] {
  const [block] = parseBlocks(text)
  if (block?.kind !== 'paragraph') throw new Error(`期望段落：${text}`)
  return block.inline
}

describe('块级：CommonMark/GFM 结构', () => {
  it('段落里的软换行落成 softbreak（渲染成断行）', () => {
    expect(paragraphIn('第一行\n第二行')).toEqual([
      { kind: 'text', text: '第一行' },
      { kind: 'softbreak' },
      { kind: 'text', text: '第二行' },
    ])
  })

  it('ATX 与 setext 标题同级；# 后无空格不是标题', () => {
    const blocks = parseBlocks('# 一\n\n二\n===\n\n#不是标题')
    expect(blocks[0]).toMatchObject({ kind: 'heading', level: 1 })
    expect(blocks[1]).toMatchObject({ kind: 'heading', level: 1 })
    expect(blocks[2]).toMatchObject({ kind: 'paragraph' })
  })

  it('围栏代码：语言取信息串第一个词；没写完的围栏一路到底（流式半截）', () => {
    expect(parseBlocks('```ts meta\nconst a = 1\n```')[0]).toMatchObject({
      kind: 'code',
      lang: 'ts',
      text: 'const a = 1',
    })
    expect(parseBlocks('说明\n\n```py\nfor i in range(3):')[1]).toMatchObject({
      kind: 'code',
      lang: 'py',
      text: 'for i in range(3):',
    })
  })

  it('缩进代码块也是代码块', () => {
    expect(parseBlocks('段落\n\n    x = 1')[1]).toMatchObject({ kind: 'code', lang: null, text: 'x = 1' })
  })

  it('嵌套列表：项里能装块，有序表带起始号', () => {
    const list = parseBlocks('3. 甲\n   - 甲一\n   - 甲二\n4. 乙')[0]
    expect(list).toMatchObject({ kind: 'list', ordered: true, start: 3 })
    if (list?.kind !== 'list') throw new Error('期望列表')
    expect(list.items).toHaveLength(2)
    expect(kinds(list.items[0]!.blocks)).toEqual(['paragraph', 'list'])
  })

  it('引用里可以再装块（列表、代码）', () => {
    const quote = parseBlocks('> 甲\n>\n> - 乙')[0]
    if (quote?.kind !== 'quote') throw new Error('期望引用')
    expect(kinds(quote.blocks)).toEqual(['paragraph', 'list'])
  })

  it('GFM 表格：对齐从分隔行读，单元格走行内解析', () => {
    const table = parseBlocks('| 名称 | 数量 |\n| :--- | ---: |\n| **甲** | 1 |\n| 乙 | 22 |')[0]
    expect(table).toMatchObject({ kind: 'table', align: ['left', 'right'] })
    if (table?.kind !== 'table') throw new Error('期望表格')
    expect(table.head.map(textOf)).toEqual(['名称', '数量'])
    expect(table.rows.map((row) => row.map(textOf))).toEqual([
      ['甲', '1'],
      ['乙', '22'],
    ])
    expect(table.rows[0]![0]![0]).toMatchObject({ kind: 'strong' })
  })

  it('表格单元格里的竖线可以转义', () => {
    const table = parseBlocks('| 甲 |\n| --- |\n| a \\| b |')[0]
    if (table?.kind !== 'table') throw new Error('期望表格')
    expect(textOf(table.rows[0]![0]!)).toBe('a | b')
  })

  it('HTML 块按原文当纯文本（不解析、不执行）', () => {
    expect(parseBlocks('<div>块</div>')[0]).toMatchObject({ kind: 'literal', text: '<div>块</div>' })
  })

  it('引用定义本身不产出内容，正文照常', () => {
    expect(kinds(parseBlocks('[ref]: https://example.com "标题"\n\n看 [这里][ref]'))).toEqual(['paragraph'])
  })

  it('分隔线紧跟标题合成带线小节（阶段 33 的视觉规则）', () => {
    expect(kinds(parseBlocks('前文\n\n---\n\n## 选型决策线'))).toEqual(['paragraph', 'section'])
    // 后面没跟标题就还是普通横线
    expect(kinds(parseBlocks('甲\n\n---\n\n乙'))).toEqual(['paragraph', 'hr', 'paragraph'])
  })

  it('任务项：勾选状态进模型', () => {
    const list = parseBlocks('- [ ] 待办 **粗**\n- [x] 完了')[0]
    if (list?.kind !== 'list') throw new Error('期望列表')
    expect(list.items.map((item) => item.checked)).toEqual([false, true])
    expect(
      list.items.map((item) => {
        const first = item.blocks[0]
        return textOf(first?.kind === 'paragraph' ? first.inline : [])
      }),
    ).toEqual(['待办 粗', '完了'])
  })

  it('空文档与纯空白没有块', () => {
    expect(parseBlocks('')).toEqual([])
    expect(parseBlocks('\n\n   \n')).toEqual([])
  })
})

describe('行内：标准写法与四条约定', () => {
  it('代码段：多反引号、内部换行折成空格、两端空格去掉一个', () => {
    expect(paragraphIn('`` a`b `` 与 `x\ny`')[0]).toEqual({ kind: 'code', text: 'a`b' })
    expect(paragraphIn('`` a`b `` 与 `x\ny`')[2]).toEqual({ kind: 'code', text: 'x y' })
  })

  it('强调可以嵌套，删除线是 GFM', () => {
    const inline = paragraphIn('**粗 *斜* 粗** 与 ~~删~~')
    expect(inline[0]).toMatchObject({ kind: 'strong' })
    const inner = inline[0]?.kind === 'strong' ? inline[0].children : []
    expect(inner.some((node) => node.kind === 'em')).toBe(true)
    expect(inline.some((node) => node.kind === 'del')).toBe(true)
  })

  it('反斜杠转义：标记变字面', () => {
    expect(textOf(paragraphIn('\\*不是斜体\\*'))).toBe('*不是斜体*')
  })

  it('实体：数值与常用名解，表外按原文', () => {
    expect(textOf(paragraphIn('&#65; &amp; &copy; &madeup;'))).toBe('A & © &madeup;')
  })

  it('硬换行：行尾两空格与反斜杠各成一断', () => {
    expect(paragraphIn('甲  \n乙\\\n丙').filter((node) => node.kind === 'break')).toHaveLength(2)
  })

  it('链接四种形态：行内、引用、折叠、简写', () => {
    const href = (text: string) => {
      const link = paragraphIn(text).find((node) => node.kind === 'link')
      return link?.kind === 'link' ? link.href : null
    }
    expect(href('[甲](https://a.example "题")')).toBe('https://a.example')
    expect(href('[ref]: https://b.example\n\n[甲][ref]')).toBe('https://b.example')
    expect(href('[ref]: https://c.example\n\n[ref][]')).toBe('https://c.example')
    expect(href('[ref]: https://d.example\n\n[ref]')).toBe('https://d.example')
  })

  it('引用没定义时按字面文本（CommonMark 行为，Lezer 不校验这条）', () => {
    const inline = paragraphIn('[甲][没定义的]')
    expect(inline.every((node) => node.kind === 'text')).toBe(true)
    expect(textOf(inline)).toBe('[甲][没定义的]')
  })

  it('自动链接：裸 URL、www、邮箱、尖括号形式', () => {
    const hrefs = (text: string) =>
      paragraphIn(text)
        .filter((node) => node.kind === 'link')
        .map((node) => (node.kind === 'link' ? node.href : ''))
    expect(hrefs('看 https://a.example 这里')).toEqual(['https://a.example'])
    expect(hrefs('看 www.a.example 这里')).toEqual(['http://www.a.example'])
    expect(hrefs('写 me@a.example')).toEqual(['mailto:me@a.example'])
    expect(hrefs('<https://b.example>')).toEqual(['https://b.example'])
  })

  it('图片：alt 取纯文本；行内标签按原文', () => {
    const inline = paragraphIn('![图 **注**](https://a.example/x.png) 与 <b>粗</b>')
    expect(inline[0]).toMatchObject({ kind: 'image', src: 'https://a.example/x.png', alt: '图 注' })
    // HTML 标签不在 DOM 里成元素：原样当文本
    expect(textOf(inline)).toContain('<b>粗</b>')
  })
})

describe('协议闸门（模型输出进 DOM 属性的唯一两处）', () => {
  it('链接只放行 http(s) / mailto / 锚点 / 相对路径', () => {
    const href = (text: string) => {
      const link = paragraphIn(text).find((node) => node.kind === 'link')
      return link?.kind === 'link' ? link.href : null
    }
    expect(href('[甲](https://a.example)')).toBe('https://a.example')
    expect(href('[甲](mailto:a@b.c)')).toBe('mailto:a@b.c')
    expect(href('[甲](#小节)')).toBe('#小节')
    expect(href('[甲](./docs/a.md)')).toBe('./docs/a.md')
    expect(href('[甲](/api/meta)')).toBe('/api/meta')
  })

  it('危险协议按字面文本留着，不成链接', () => {
    for (const text of [
      '[点我](javascript:alert(1))',
      '[点我](data:text/html;base64,PHNjcmlwdD4=)',
      '[点我](vbscript:x)',
      '[点我](//evil.example/x)',
    ]) {
      const inline = paragraphIn(text)
      expect(inline.every((node) => node.kind === 'text'), text).toBe(true)
      expect(textOf(inline), text).toBe(text)
    }
  })

  it('尖括号自动链接里的危险协议同样拦住', () => {
    expect(paragraphIn('<javascript:alert(1)>').every((node) => node.kind === 'text')).toBe(true)
  })

  it('图片地址：data:image 与 http(s) 放行，其余按文本', () => {
    const image = (text: string) => paragraphIn(text).find((node) => node.kind === 'image')
    expect(image('![x](data:image/png;base64,AAAA)')).toMatchObject({ kind: 'image' })
    expect(image('![x](https://a.example/x.png)')).toMatchObject({ kind: 'image' })
    expect(image('![x](javascript:alert(1))')).toBeUndefined()
    expect(image('![x](data:text/html;base64,PHNjcmlwdD4=)')).toBeUndefined()
  })
})
