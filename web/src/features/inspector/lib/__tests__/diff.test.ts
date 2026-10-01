/**
 * 行级 diff 的用例：五种基本形态 + 行号，外加"统一格式可复制"这一条。
 *
 * 行号是这里最容易写错的一处（offset by one / 把 add 也推 oldNo），
 * 所以每个 case 都把 oldNo / newNo 一起断言，而不只看 kind。
 */

import { describe, expect, it } from 'vitest'

import { diffLines, toUnified } from '../diff'

describe('diffLines', () => {
  it('纯新增', () => {
    expect(diffLines('a\nb', 'a\nb\nc')).toEqual([
      { kind: 'context', text: 'a', oldNo: 1, newNo: 1 },
      { kind: 'context', text: 'b', oldNo: 2, newNo: 2 },
      { kind: 'add', text: 'c', oldNo: null, newNo: 3 },
    ])
  })

  it('纯删除', () => {
    expect(diffLines('a\nb\nc', 'a\nb')).toEqual([
      { kind: 'context', text: 'a', oldNo: 1, newNo: 1 },
      { kind: 'context', text: 'b', oldNo: 2, newNo: 2 },
      { kind: 'del', text: 'c', oldNo: 3, newNo: null },
    ])
  })

  it('中间替换：先删后加，两侧行号各自推进', () => {
    expect(diffLines('a\nb\nc', 'a\nB\nc')).toEqual([
      { kind: 'context', text: 'a', oldNo: 1, newNo: 1 },
      { kind: 'del', text: 'b', oldNo: 2, newNo: null },
      { kind: 'add', text: 'B', oldNo: null, newNo: 2 },
      { kind: 'context', text: 'c', oldNo: 3, newNo: 3 },
    ])
  })

  it('完全相同：没有 add / del', () => {
    const lines = diffLines('a\nb\nc', 'a\nb\nc')

    expect(lines.map((line) => line.kind)).toEqual(['context', 'context', 'context'])
    expect(lines.map((line) => line.oldNo)).toEqual([1, 2, 3])
    expect(lines.map((line) => line.newNo)).toEqual([1, 2, 3])
  })

  it('两侧都空 → 空数组', () => {
    expect(diffLines('', '')).toEqual([])
  })

  it('空 → 有内容：全部是新增，编号从 1 起', () => {
    expect(diffLines('', 'x\ny')).toEqual([
      { kind: 'add', text: 'x', oldNo: null, newNo: 1 },
      { kind: 'add', text: 'y', oldNo: null, newNo: 2 },
    ])
  })

  it('有内容 → 空：全部是删除', () => {
    expect(diffLines('x\ny', '')).toEqual([
      { kind: 'del', text: 'x', oldNo: 1, newNo: null },
      { kind: 'del', text: 'y', oldNo: 2, newNo: null },
    ])
  })

  it('末尾换行不算额外空行', () => {
    expect(diffLines('a\n', 'a\n')).toEqual([{ kind: 'context', text: 'a', oldNo: 1, newNo: 1 }])
  })

  it('多处替换：每处各自成对，中间相同行回落成 context', () => {
    const kinds = diffLines('one\ntwo\nthree\nfour', 'one\nTWO\nthree\nFOUR').map((l) => l.kind)

    expect(kinds).toEqual(['context', 'del', 'add', 'context', 'del', 'add'])
  })

  it('超大输入走整段替换的兜底路径（宁可难读，不能卡住界面）', () => {
    const big = (prefix: string): string =>
      Array.from({ length: 1_600 }, (_, index) => `${prefix} ${index}`).join('\n')

    const lines = diffLines(big('old'), big('new'))

    expect(lines).toHaveLength(3_200)
    expect(lines.filter((line) => line.kind === 'del')).toHaveLength(1_600)
    expect(lines.filter((line) => line.kind === 'add')).toHaveLength(1_600)
    expect(lines.some((line) => line.kind === 'context')).toBe(false)
  })
})

describe('toUnified', () => {
  it('前缀是 + / - / 空格，可直接复制', () => {
    expect(toUnified('a\nb\nc', 'a\nB\nc')).toBe([' a', '-b', '+B', ' c'].join('\n'))
  })

  it('空 diff 输出空串', () => {
    expect(toUnified('', '')).toBe('')
  })

  it('前缀与行数一一对应', () => {
    const lines = toUnified('x\ny', 'y\nz').split('\n')

    expect(lines.length).toBe(diffLines('x\ny', 'y\nz').length)
    expect(lines.every((line) => ['+', '-', ' '].includes(line.charAt(0)))).toBe(true)
  })
})
