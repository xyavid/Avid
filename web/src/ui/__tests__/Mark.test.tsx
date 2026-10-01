// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import faviconSource from '../../../public/favicon.svg?raw'
import { AVID_MARK_PATH, AvidMark } from '../Mark'

/**
 * 标识的两条纪律，都用会失败的断言钉住：
 *   1. 形状只有一份（Mark.tsx 的常量），favicon 是它的副本——副本漂移必须当场报错；
 *   2. 标记本身不带颜色与尺寸（fill=currentColor、尺寸由调用点给），
 *      因为换肤机制要求「颜色只从 token 来」，写死色值的标记会让青夜主题漏一块。
 */
describe('Avid 标记（阶段 33 · 阶段 7）', () => {
  afterEach(cleanup)

  it('渲染 48 视框的单条轮廓，颜色随 currentColor', () => {
    const { container } = render(<AvidMark size={24} />)
    const svg = container.querySelector('svg')

    expect(svg?.getAttribute('viewBox')).toBe('0 0 48 48')
    expect(svg?.getAttribute('width')).toBe('24')
    expect(svg?.getAttribute('height')).toBe('24')

    // 单条 path：定稿形状靠外轮廓自身的凹槽读作火，不靠挖空（fill-rule 缺席即是证据）
    const paths = svg?.querySelectorAll('path') ?? []
    expect(paths).toHaveLength(1)
    expect(paths[0]?.getAttribute('d')).toBe(AVID_MARK_PATH)
    expect(paths[0]?.getAttribute('fill')).toBe('currentColor')
    expect(paths[0]?.getAttribute('fill-rule')).toBeNull()
  })

  it('装饰性图形不进无障碍树（名字由旁边的字标给）', () => {
    const { container } = render(<AvidMark />)

    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })

  it('favicon.svg 里的 path 与常量逐字一致（对账门禁）', () => {
    expect(faviconSource.match(/d="[^"]+"/g)).toHaveLength(1)
    expect(faviconSource).toContain(AVID_MARK_PATH)
  })

  it('favicon 自带纸底与墨色（浏览器标签栏里没有 currentColor 可继承）', () => {
    expect(faviconSource).toContain('fill="#F8F4ED"')
    expect(faviconSource).toContain('fill="#3B3D3F"')
  })
})
