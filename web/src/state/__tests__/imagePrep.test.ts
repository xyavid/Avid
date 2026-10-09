/**
 * 图片草稿的准备（阶段 59）：只测纯计算——缩放系数、体积门限、base64 编码。
 * canvas 那条路在 jsdom 里没有实现（也就没有可测的东西），它由证据脚本在真浏览器里跑。
 */

import { beforeEach, describe, expect, it } from 'vitest'

import {
  DOWNSCALE_SIDE,
  MAX_IMAGE_BYTES,
  MAX_IMAGES,
  base64Of,
  draftFromBytes,
  isImageFile,
  scaleFor,
} from '../imagePrep'

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0])

beforeEach(() => {
  // jsdom 不实现 object URL：造一个可控替身，断言只看它有没有被用上。
  Object.assign(URL, {
    createObjectURL: () => 'blob:test',
    revokeObjectURL: () => undefined,
  })
})

describe('scaleFor：只在长边超限时才缩', () => {
  it('不超限就不动', () => {
    expect(scaleFor(800, 600, DOWNSCALE_SIDE)).toBe(1)
    expect(scaleFor(1568, 1568, DOWNSCALE_SIDE)).toBe(1)
  })

  it('超限时按长边等比缩', () => {
    expect(scaleFor(3136, 1568, DOWNSCALE_SIDE)).toBeCloseTo(0.5)
    expect(scaleFor(1000, 4000, 2000)).toBeCloseTo(0.5)
  })

  it('零尺寸不算超限（解码失败的图不该把比例算成 NaN）', () => {
    expect(scaleFor(0, 0, DOWNSCALE_SIDE)).toBe(1)
  })
})

describe('isImageFile：四种允许的类型', () => {
  it('认 png/jpeg/webp/gif', () => {
    for (const type of ['image/png', 'image/jpeg', 'image/webp', 'image/gif']) {
      expect(isImageFile(type)).toBe(true)
    }
  })

  it('svg 与别的类型都不认（svg 是脚本载体，不是图片）', () => {
    expect(isImageFile('image/svg+xml')).toBe(false)
    expect(isImageFile('application/pdf')).toBe(false)
    expect(isImageFile('')).toBe(false)
  })
})

describe('draftFromBytes：草稿带字节、base64 与本地预览地址', () => {
  it('base64 与字节一一对应', () => {
    const draft = draftFromBytes(PNG, 'a.png', 'image/png')
    expect(draft.bytes).toBe(PNG.byteLength)
    expect(draft.data).toBe(base64Of(PNG))
    expect(atob(draft.data).length).toBe(PNG.byteLength)
  })

  it('本地预览走 object URL，且默认不标已压缩', () => {
    const draft = draftFromBytes(PNG, 'a.png', 'image/png')
    expect(draft.url).toBe('blob:test')
    expect(draft.compressed).toBe(false)
    expect(draft.name).toBe('a.png')
  })

  it('每条草稿有独立 id（同一张图选两次是两条）', () => {
    const first = draftFromBytes(PNG, 'a.png', 'image/png')
    const second = draftFromBytes(PNG, 'a.png', 'image/png')
    expect(first.id).not.toBe(second.id)
  })
})

describe('上限与内核同口径', () => {
  it('单图 8MB、单条 8 张（与 avid/attachments.py 一致）', () => {
    expect(MAX_IMAGE_BYTES).toBe(8 * 1024 * 1024)
    expect(MAX_IMAGES).toBe(8)
  })
})
