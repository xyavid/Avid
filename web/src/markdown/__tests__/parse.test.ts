import { describe, expect, it } from 'vitest'

import { parseBlocks } from '../parse'

/**
 * 块级解析：模型输出里真实会出现的那些块，以及**流式过程中**的半截输入。
 * 纯函数，不需要 DOM。
 */
describe('markdown 块级解析', () => {
  it('围栏代码块：取语言标，内容原样保留（含空行与缩进）', () => {
    const blocks = parseBlocks('前文\n\n```ts\nconst a = 1\n\n  const b = 2\n```\n\n后文')

    expect(blocks.map((b) => b.kind)).toEqual(['paragraph', 'code', 'paragraph'])
    const code = blocks[1]
    if (code?.kind !== 'code') throw new Error('第二块应为代码')
    expect(code.lang).toBe('ts')
    expect(code.text).toBe('const a = 1\n\n  const b = 2')
  })

  it('流式中的半截围栏：还没等到收尾的 ``` 也要成块，而不是当成正文', () => {
    const blocks = parseBlocks('这是说明\n\n```py\nfor i in range(3):')

    expect(blocks.map((b) => b.kind)).toEqual(['paragraph', 'code'])
    const code = blocks[1]
    if (code?.kind !== 'code') throw new Error('第二块应为代码')
    expect(code.lang).toBe('py')
    expect(code.text).toBe('for i in range(3):')
  })

  it('波浪号围栏同样认，且不把围栏里的 ``` 当收尾', () => {
    const blocks = parseBlocks('~~~\n```\n还在代码里\n~~~')

    const [fence] = blocks
    if (fence?.kind !== 'code') throw new Error('应为代码块')
    expect(fence.text).toBe('```\n还在代码里')
  })

  it('标题 1–6 档；# 后面没有空格不算标题', () => {
    const blocks = parseBlocks('# 一\n\n### 三\n\n###### 六\n\n#不是标题')

    expect(blocks.map((b) => (b.kind === 'heading' ? b.level : b.kind))).toEqual([1, 3, 6, 'paragraph'])
  })

  it('分隔线：--- / *** / ___，三条以上', () => {
    expect(parseBlocks('a\n\n---\n\nb').map((b) => b.kind)).toEqual(['paragraph', 'hr', 'paragraph'])
    expect(parseBlocks('***').map((b) => b.kind)).toEqual(['hr'])
    expect(parseBlocks('___').map((b) => b.kind)).toEqual(['hr'])
    expect(parseBlocks('--').map((b) => b.kind)).toEqual(['paragraph'])
  })

  it('无序列表：聚成一个列表，项之间不粘行', () => {
    const [list] = parseBlocks('- 甲\n- 乙\n* 丙')

    if (list?.kind !== 'list') throw new Error('应为无序列表')
    expect(list.ordered).toBe(false)
    expect(list.items.map((i) => i.text)).toEqual(['甲', '乙', '丙'])
  })

  it('有序列表：记住起始序号（模型常用 3. 起头接前文）', () => {
    const [list] = parseBlocks('3. 甲\n4. 乙')

    if (list?.kind !== 'list') throw new Error('应为有序列表')
    expect(list.ordered).toBe(true)
    expect(list.start).toBe(3)
    expect(list.items.map((i) => i.text)).toEqual(['甲', '乙'])
  })

  it('列表项可以嵌套：缩进两格起一层，父项的续行算父项', () => {
    const [list] = parseBlocks('- 甲\n  续行\n  - 甲一\n- 乙')

    if (list?.kind !== 'list') throw new Error('应为列表')
    expect(list.items[0]?.text).toBe('甲\n续行')
    expect(list.items[0]?.children).toHaveLength(1)
    const nested = list.items[0]?.children[0]
    if (nested?.kind !== 'list') throw new Error('项内应为嵌套列表')
    expect(nested.items[0]?.text).toBe('甲一')
    expect(list.items[1]?.text).toBe('乙')
  })

  it('表格：识别表头、对齐与数据行', () => {
    const [table] = parseBlocks('| 名称 | 数量 |\n| :--- | ---: |\n| 甲 | 1 |\n| 乙 | 22 |')

    if (table?.kind !== 'table') throw new Error('应为表格')
    expect(table.head).toEqual(['名称', '数量'])
    expect(table.align).toEqual(['left', 'right'])
    expect(table.rows).toEqual([['甲', '1'], ['乙', '22']])
  })

  it('引用：> 剥一层后按块递归解析', () => {
    const [quote] = parseBlocks('> 甲\n>\n> - 乙')

    if (quote?.kind !== 'quote') throw new Error('应为引用')
    expect(quote.blocks.map((b) => b.kind)).toEqual(['paragraph', 'list'])
  })

  it('段落里的软换行保留（模型排版的一部分；CJK 补空格更糟）', () => {
    const [para] = parseBlocks('第一行\n第二行')

    if (para?.kind !== 'paragraph') throw new Error('应为段落')
    expect(para.text).toBe('第一行\n第二行')
  })

  it('空输入与纯空白不产出块', () => {
    expect(parseBlocks('')).toEqual([])
    expect(parseBlocks('\n\n   \n')).toEqual([])
  })
})
