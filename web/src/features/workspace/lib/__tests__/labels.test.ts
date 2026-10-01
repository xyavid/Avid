/**
 * 显示口径的边界用例。
 *
 * 这里钉的是全仓最容易被写坏的一条口径：**null 表示"没有这个数"，不是 0**。
 * 渲染层各自写 `value ?? '—'` 不难，难的是"0 必须显示成 0、空串要显示 —"这两条
 * 同时成立；把它收在纯函数里，渲染层就不必重复做这个判断。
 */

import { describe, expect, it } from 'vitest'

import { leafOf, orDash, workspaceLabel } from '../labels'

describe('orDash', () => {
  it('null / undefined 显示「—」', () => {
    expect(orDash(null)).toBe('—')
    expect(orDash(undefined)).toBe('—')
  })

  it('空串与纯空白也显示「—」：空白不是值', () => {
    expect(orDash('')).toBe('—')
    expect(orDash('   ')).toBe('—')
  })

  it('0 必须显示成 0，不能被当成"缺数"', () => {
    expect(orDash(0)).toBe('0')
    expect(orDash('0')).toBe('0')
  })
})

describe('leafOf', () => {
  it('取路径末段，尾斜杠不算一段', () => {
    expect(leafOf('/home/fishy/Avid')).toBe('Avid')
    expect(leafOf('/home/fishy/Avid/')).toBe('Avid')
  })

  it('根路径回落到它自己，不返回空串（空串会被误当成"没有名字"）', () => {
    expect(leafOf('/')).toBe('/')
  })
})

describe('workspaceLabel', () => {
  it('有名字用名字', () => {
    expect(workspaceLabel({ name: 'Avid', root: '/home/fishy/Avid' })).toBe('Avid')
  })

  it('名字为空 / 纯空白 / null 时回落到 root 末段', () => {
    expect(workspaceLabel({ name: null, root: '/home/fishy/Avid' })).toBe('Avid')
    expect(workspaceLabel({ name: '', root: '/home/fishy/Avid' })).toBe('Avid')
    expect(workspaceLabel({ name: '  ', root: '/home/fishy/Avid' })).toBe('Avid')
  })
})
