// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import assetSource from '../../../src/assets/avid-mark.svg?raw'
import logoDarkSource from '../../assets/avid-logo-dark.svg?raw'
import logoLightSource from '../../assets/avid-logo-light.svg?raw'
import faviconSource from '../../assets/favicon.svg?raw'
import { AvidMark } from '../Mark'

/** Sorted `d` attributes of every path — one yardstick for comparing all copies. */
const paths = (text: string) => (text.match(/ d="[^"]+"/g) ?? []).sort()

/** Lotus layer inside the README logo: nested in `<svg x="14">`, with the wordmark outside. */
const lotusPaths = (text: string) =>
  paths(/<svg x="\d+"[\s\S]*?<\/svg>/.exec(text)?.[0] ?? '')

/**
 * Three disciplines, each pinned by an assertion that fails on drift: every copy of the drawing
 * must stay identical; the mark sits transparent with no disc or plate; and the README wordmarks
 * are outlined so they render without the viewer's fonts.
 */
describe('Avid 标识（阶段 33 · 阶段 7）', () => {
  afterEach(cleanup)

  it('渲染一张自带配色的图，尺寸由调用点决定', () => {
    const { container } = render(<AvidMark size={24} />)
    const img = container.querySelector('img')

    expect(img).toBeTruthy()
    expect(img?.getAttribute('width')).toBe('24')
    expect(img?.getAttribute('height')).toBe('24')
    // the drawing is an asset, not inline SVG: shapes and colors stay out of the component
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
    const assetPaths = paths(assetSource)
    // a handful of filled shapes; the upper bound blocks re-tracing the 400+ hairline paths
    expect(assetPaths.length).toBeGreaterThanOrEqual(6)
    expect(assetPaths.length).toBeLessThan(40)
    expect(paths(faviconSource)).toEqual(assetPaths)
  })

  it('README 标识的两版也内联同一幅荷花，且背景透明（对账门禁）', () => {
    const assetPaths = paths(assetSource)

    expect(lotusPaths(logoLightSource)).toEqual(assetPaths)
    expect(lotusPaths(logoDarkSource)).toEqual(assetPaths)
    // each: 9 lotus paths + 1 wordmark, on a transparent background, same rule as the asset
    for (const source of [logoLightSource, logoDarkSource]) {
      expect(source.match(/<path/g)?.length).toBe(10)
      expect(source).not.toContain('<rect')
    }
  })

  it('字标是轮廓，不依赖查看端字体', () => {
    // outlining is the reason these files exist: <text> reflows with the viewer's fonts
    expect(logoLightSource).not.toContain('<text')
    expect(logoDarkSource).not.toContain('<text')
  })

  it('浅色与深色两版只差字标颜色', () => {
    const shape = (text: string) =>
      text.replace(/<!--[\s\S]*?-->/, '').replace(/fill="#[0-9A-Fa-f]{6}"/g, 'fill="X"')

    expect(shape(logoLightSource)).toBe(shape(logoDarkSource))
  })

  it('没有发丝级墨线（它们在 22px 下会变成一圈黑噪点）', () => {
    // hairline traces left by tracing (#010101 etc.) are cleaned out; none may survive
    expect(assetSource).not.toMatch(/fill="#0[12]0[12]0[12]"/)
    expect(assetSource).not.toMatch(/fill="#010101"/)
  })

  it('favicon 自带底板，asset 没有（标签栏里没有页面底色可继承）', () => {
    // the plate is dark: pale petals on paper measure a 1.15 contrast ratio, invisible at 16px
    expect(faviconSource).toContain('fill="#3B4A54"')
    expect(faviconSource).not.toContain('fill="#F8F4ED"')
    // the asset stays untouched: transparent background, not a pixel changed
    expect(assetSource).not.toContain('<rect')
  })

  it('两边的画布视框一致，落位不会因为视框不同而忽大忽小', () => {
    const viewBox = (text: string) => /viewBox="([^"]+)"/.exec(text)?.[1] ?? null
    const asset = viewBox(assetSource)

    expect(asset).not.toBeNull()
    // the favicon keeps the original 512 canvas, the asset a narrowed square viewBox:
    // both square, so the mark occupies the same share of either
    const fav = viewBox(faviconSource)?.split(' ').map(Number) ?? []
    expect(fav[2]).toBe(fav[3])
    expect(Number(asset?.split(' ')[2])).toBeCloseTo(Number(asset?.split(' ')[3]), 5)
  })
})
