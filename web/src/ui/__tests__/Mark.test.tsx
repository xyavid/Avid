// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import assetSource from '../../../src/assets/avid-mark.svg?raw'
import faviconSource from '../../../public/favicon.svg?raw'
import { AvidMark } from '../Mark'

/**
 * 标识的两条纪律，都用会失败的断言钉住：
 *   1. 同一幅画有两份副本（组件用的 asset 与 favicon）——副本漂移必须当场报错；
 *   2. 落位不做圆托、不垫色板（背景透明），尺寸由调用点给。
 */
describe('Avid 标识（阶段 33 · 阶段 7）', () => {
  afterEach(cleanup)

  it('渲染一张自带配色的图，尺寸由调用点决定', () => {
    const { container } = render(<AvidMark size={24} />)
    const img = container.querySelector('img')

    expect(img).toBeTruthy()
    expect(img?.getAttribute('width')).toBe('24')
    expect(img?.getAttribute('height')).toBe('24')
    // 图是资源，不是内联 SVG：形状与配色留在 asset 文件里，不进组件
    expect(container.querySelector('svg')).toBeNull()
    expect(img?.getAttribute('src') ?? '').toContain('avid-mark')
  })

  it('是装饰性图形：空 alt + aria-hidden（名字由旁边的字标给）', () => {
    const { container } = render(<AvidMark />)

    const img = container.querySelector('img')
    expect(img?.getAttribute('alt')).toBe('')
    expect(img?.getAttribute('aria-hidden')).toBe('true')
  })

  it('asset 与 favicon 是同一幅画：两边 path 集合逐字一致（对账门禁）', () => {
    const paths = (text: string) => (text.match(/ d="[^"]+"/g) ?? []).sort()

    const assetPaths = paths(assetSource)
    // 9 条有厚度的色块。上限是防「重新描摹一遍」把 400 多条发丝墨线带回来。
    expect(assetPaths.length).toBeGreaterThanOrEqual(6)
    expect(assetPaths.length).toBeLessThan(40)
    expect(paths(faviconSource)).toEqual(assetPaths)
  })

  it('没有发丝级墨线（它们在 22px 下会变成一圈黑噪点）', () => {
    // 描摹件的边缘残迹都是 #010101 / #020202 这类近黑且极薄的 path；
    // 清洗后一条不该剩——颜色照搬原样，但这一层不是画面的一部分。
    expect(assetSource).not.toMatch(/fill="#0[12]0[12]0[12]"/)
    expect(assetSource).not.toMatch(/fill="#010101"/)
  })

  it('favicon 自带纸底（标签栏里没有页面底色可继承）', () => {
    expect(faviconSource).toContain('fill="#F8F4ED"')
    expect(assetSource).not.toContain('fill="#F8F4ED"')
  })

  it('两边的画布视框一致，落位不会因为视框不同而忽大忽小', () => {
    const viewBox = (text: string) => /viewBox="([^"]+)"/.exec(text)?.[1] ?? null
    const asset = viewBox(assetSource)

    expect(asset).not.toBeNull()
    // favicon 用的是原始 512 画布 + 居中缩放，asset 用的是收窄后的正方形视框：
    // 两边都是正方形，标记在各自框里占的比例也就一致。
    const fav = viewBox(faviconSource)?.split(' ').map(Number) ?? []
    expect(fav[2]).toBe(fav[3])
    expect(Number(asset?.split(' ')[2])).toBeCloseTo(Number(asset?.split(' ')[3]), 5)
  })
})
