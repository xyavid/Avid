// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'

import { applyMode, initAppearance, readMode, resolveTheme, THEMES, writeMode } from '../appearance'

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('外观配置（模式 / 主题解析 / 持久化）', () => {
  it('显式模式映射主题：light→暖纸，dark→青夜', () => {
    expect(resolveTheme('light', false)).toBe('warm-paper')
    expect(resolveTheme('light', true)).toBe('warm-paper')
    expect(resolveTheme('dark', false)).toBe('midnight')
    expect(resolveTheme('dark', true)).toBe('midnight')
  })

  it('auto 跟随系统偏好', () => {
    expect(resolveTheme('auto', false)).toBe('warm-paper')
    expect(resolveTheme('auto', true)).toBe('midnight')
  })

  it('默认模式是 auto（未选择时跟随系统，不硬编码浅色）', () => {
    expect(readMode()).toBe('auto')
  })

  it('writeMode 落 localStorage；坏数据回退 auto', () => {
    writeMode('dark')
    expect(readMode()).toBe('dark')
    window.localStorage.setItem('avid.appearance', '{oops')
    expect(readMode()).toBe('auto')
  })

  it('applyMode 写 data-theme（initAppearance 走同一条路）', () => {
    window.localStorage.setItem('avid.appearance', JSON.stringify('dark'))
    initAppearance()
    expect(document.documentElement.dataset.theme).toBe('midnight')

    applyMode('light')
    expect(document.documentElement.dataset.theme).toBe('warm-paper')
  })

  it('主题注册表：两套，各带预览色与明暗归属（报告 §3.2/§3.3/§4）', () => {
    expect(THEMES.map((t) => t.id)).toEqual(['warm-paper', 'midnight'])
    expect(THEMES[0]?.kind).toBe('light')
    expect(THEMES[1]?.kind).toBe('dark')
    for (const t of THEMES) {
      expect(t.bg).toMatch(/^#[0-9A-Fa-f]{6}$/)
      expect(t.accent).toMatch(/^#[0-9A-Fa-f]{6}$/)
    }
  })
})
