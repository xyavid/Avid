import { describe, expect, it } from 'vitest'

import type { DiffRow } from '../diff'
import { diffLines } from '../diff'

/** Diff rows → readable shape: `+added` / `-removed` / ` unchanged` / `…skipped`. */
function shape(rows: DiffRow[]): string[] {
  return rows.map((row) =>
    row.kind === 'skip'
      ? `…${row.count}`
      : `${row.kind === 'add' ? '+' : row.kind === 'del' ? '-' : ' '}${row.text}`,
  )
}

describe('diffLines：两段文本 → 差异行', () => {
  it('追加一行：前面都是未改动行，末尾一条新增', () => {
    const diff = diffLines('a\nb', 'a\nb\nc')

    expect(shape(diff.rows)).toEqual([' a', ' b', '+c'])
    expect([diff.added, diff.removed]).toEqual([1, 0])
    expect(diff.text).toBe(' a\n b\n+c')
  })

  it('替换中间一行：旧的删一行、新的加一行', () => {
    const diff = diffLines('a\nb\nc', 'a\nB\nc')

    expect(shape(diff.rows)).toEqual([' a', '-b', '+B', ' c'])
    expect([diff.added, diff.removed]).toEqual([1, 1])
  })

  it('一字未改：不给行也不给全文（没改动就不铺原文）', () => {
    const diff = diffLines('a\nb', 'a\nb')

    expect(diff.rows).toEqual([])
    expect([diff.added, diff.removed, diff.text]).toEqual([0, 0, ''])
  })

  it('尾随换行不算多一行', () => {
    const diff = diffLines('a\n', 'a\nb\n')

    expect(shape(diff.rows)).toEqual([' a', '+b'])
  })

  it('新建（旧文本为空）：整篇都是新增', () => {
    const diff = diffLines('', 'x\ny')

    expect(shape(diff.rows)).toEqual(['+x', '+y'])
    expect([diff.added, diff.removed]).toEqual([2, 0])
  })

  it('开头一长段未改动：只留贴着改动的 3 行，其余折成一行', () => {
    const diff = diffLines('1\n2\n3\n4\n5\n6\n7', '1\n2\n3\n4\n5\n6\nX')

    expect(shape(diff.rows)).toEqual(['…3', ' 4', ' 5', ' 6', '-7', '+X'])
  })

  it('中间一长段未改动：两头各留 3 行，中间折成一行', () => {
    const same = ['1', '2', '3', '4', '5', '6', '7', '8', '9']
    const diff = diffLines(['a', ...same, 'b'].join('\n'), ['A', ...same, 'B'].join('\n'))

    expect(shape(diff.rows)).toEqual(['-a', '+A', ' 1', ' 2', ' 3', '…3', ' 7', ' 8', ' 9', '-b', '+B'])
  })

  it('未改动行不多于 7 行就不折（折了反而更碎）', () => {
    const diff = diffLines('1\n2\n3\n4', '1\n2\n3\nX')

    expect(shape(diff.rows)).toEqual([' 1', ' 2', ' 3', '-4', '+X'])
  })

  it('整段改写的两行以上：先给旧的、再给新的（与参考界面同序）', () => {
    const diff = diffLines('a\nb\nc', 'X\nY')

    expect(shape(diff.rows)).toEqual(['-a', '-b', '-c', '+X', '+Y'])
  })

  it('大块改写：算不动就整段删整段加（不猜中间哪几行没动）', () => {
    const before = Array.from({ length: 600 }, (_, i) => `old ${i}`).join('\n')
    const after = Array.from({ length: 600 }, (_, i) => `new ${i}`).join('\n')
    const diff = diffLines(before, after)

    expect([diff.added, diff.removed]).toEqual([600, 600])
    expect(diff.rows.some((row) => row.kind === 'context')).toBe(false)
    expect(diff.text.startsWith('-old 0\n')).toBe(true)
  })

  it('显示有上限：超了就删中间留两头，被删掉的行数并进省略行', () => {
    const before = Array.from({ length: 600 }, (_, i) => `old ${i}`).join('\n')
    const after = Array.from({ length: 600 }, (_, i) => `new ${i}`).join('\n')
    const diff = diffLines(before, after)

    expect(diff.rows).toHaveLength(400)
    expect(diff.rows.filter((row) => row.kind === 'skip')).toHaveLength(1)
    expect(diff.rows[199]).toMatchObject({ kind: 'skip', count: 1200 - 199 - 200 })
    // Both ends must stay visible: deleted old lines first, added new lines last.
    expect(diff.rows[0]).toMatchObject({ kind: 'del', text: 'old 0' })
    expect(diff.rows.at(-1)).toMatchObject({ kind: 'add', text: 'new 599' })
    // The full text is never truncated: display is for the eye, copy takes everything.
    expect(diff.text.split('\n')).toHaveLength(1200)
  })

  it('省略行只进显示，不进全文', () => {
    const diff = diffLines('1\n2\n3\n4\n5\n6\n7', '1\n2\n3\n4\n5\n6\nX')

    expect(shape(diff.rows)).toEqual(['…3', ' 4', ' 5', ' 6', '-7', '+X'])
    expect(diff.text).not.toContain('其余')
    expect(diff.text.split('\n')).toHaveLength(8)
  })
})
