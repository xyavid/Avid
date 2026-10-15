// @vitest-environment jsdom
/**
 * The backend address single point: an unset base must leave the same-origin shape untouched, and a
 * set one must reach every transport — REST, images and the terminal socket alike.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

async function load() {
  return import('../base')
}

describe('api/base（后端地址单点）', () => {
  it('未设置 base 时保持同源形状', async () => {
    const { API_BASE, backendUrl, socketUrl } = await load()
    expect(API_BASE).toBe('')
    expect(backendUrl('/api/meta')).toBe('/api/meta')
    // A WebSocket URL must be absolute, so the page's own origin resolves the empty base.
    expect(socketUrl('/api/ws/terminal?root=%2Ftmp')).toBe(
      `ws://${window.location.host}/api/ws/terminal?root=%2Ftmp`,
    )
  })

  it('设置 base 后 REST、图片与终端都指向它', async () => {
    vi.stubEnv('VITE_API_BASE', 'http://127.0.0.1:8765/')
    const { API_BASE, backendUrl, socketUrl } = await load()
    // The trailing slash is stripped so `base + '/api/...'` never doubles it.
    expect(API_BASE).toBe('http://127.0.0.1:8765')
    expect(backendUrl('/api/meta')).toBe('http://127.0.0.1:8765/api/meta')
    expect(socketUrl('/api/ws/terminal')).toBe('ws://127.0.0.1:8765/api/ws/terminal')
  })

  it('base 用 https 时终端也跟着走 wss', async () => {
    vi.stubEnv('VITE_API_BASE', 'https://avid.example')
    const { socketUrl } = await load()
    expect(socketUrl('/api/ws/terminal')).toBe('wss://avid.example/api/ws/terminal')
  })

  it('运行期注入压过构建期变量：avid ui 起的后端说了算', async () => {
    vi.stubEnv('VITE_API_BASE', 'http://127.0.0.1:9999')
    vi.stubGlobal('__AVID_API_BASE__', 'http://127.0.0.1:8765')
    const { API_BASE, backendUrl } = await load()
    expect(API_BASE).toBe('http://127.0.0.1:8765')
    expect(backendUrl('/api/meta')).toBe('http://127.0.0.1:8765/api/meta')
  })

  it('图片附件地址也走 base：它会被交给浏览器当 <img src>', async () => {
    vi.stubEnv('VITE_API_BASE', 'http://127.0.0.1:8765')
    const { attachmentUrl } = await import('../client')
    expect(attachmentUrl('s1', 'e1', 0)).toBe(
      'http://127.0.0.1:8765/api/sessions/s1/entries/e1/attachments/0',
    )
  })
})
