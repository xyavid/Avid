// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ICON_NAMES, Icon } from '../Icon'

describe('Icon 图标集（报告 §9 图标规范）', () => {
  afterEach(cleanup)

  it('每个注册图标都能渲染，且全部遵守线性纪律：fill none / stroke currentColor / 1.5', () => {
    expect(ICON_NAMES.length).toBeGreaterThan(20)

    for (const name of ICON_NAMES) {
      const { container, unmount } = render(<Icon name={name} />)
      const svg = container.querySelector('svg')
      expect(svg, `图标 ${name} 应渲染出 svg`).toBeTruthy()
      expect(svg?.getAttribute('fill')).toBe('none')
      expect(svg?.getAttribute('stroke')).toBe('currentColor')
      expect(svg?.getAttribute('stroke-width')).toBe('1.5')
      expect(svg?.getAttribute('aria-hidden')).toBe('true')
      // a linear icon must have strokes, never an empty shell
      expect(svg?.querySelector('path, rect, circle, line'), `图标 ${name} 缺笔迹`).toBeTruthy()
      unmount()
    }
  })

  it('size 可调，默认 12（徽章场景）', () => {
    const { container: small } = render(<Icon name="check" />)
    const { container: big } = render(<Icon name="check" size={16} />)

    expect(small.querySelector('svg')?.getAttribute('width')).toBe('12')
    expect(big.querySelector('svg')?.getAttribute('width')).toBe('16')
  })
})
