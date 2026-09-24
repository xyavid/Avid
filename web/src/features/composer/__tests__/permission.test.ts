import { describe, expect, it } from 'vitest'

import {
  buildStartRunInput,
  DEFAULT_PERMISSION,
  isPermissionMode,
  needsFullAck,
  PERMISSION_MODES,
  resolvePermissionMode,
} from '../lib/permission'

describe('权限模式的取值集合', () => {
  it('恰好三档，顺序与服务端 Literal 一致', () => {
    expect(PERMISSION_MODES).toEqual(['manual', 'auto', 'full'])
  })

  it('兜底档是 manual（最严的一档，与服务端 DEFAULT_MODE 同值）', () => {
    expect(DEFAULT_PERMISSION).toBe('manual')
  })

  it('只认这三档，旧名与别的一律不是', () => {
    expect(isPermissionMode('manual')).toBe(true)
    expect(isPermissionMode('auto')).toBe(true)
    expect(isPermissionMode('full')).toBe(true)
    // 旧模式名不能被当成"用户选了一档"发出去：服务端 422。
    expect(isPermissionMode('strict')).toBe(false)
    expect(isPermissionMode('workspace')).toBe(false)
    expect(isPermissionMode('system')).toBe(false)
    expect(isPermissionMode('MANUAL')).toBe(false)
    expect(isPermissionMode('')).toBe(false)
    expect(isPermissionMode(null)).toBe(false)
    expect(isPermissionMode(undefined)).toBe(false)
    expect(isPermissionMode(1)).toBe(false)
  })
})

describe('缺省回落', () => {
  it('显式选过的档优先于工作区默认', () => {
    expect(resolvePermissionMode('auto', 'manual')).toBe('auto')
    expect(resolvePermissionMode('manual', 'auto')).toBe('manual')
  })

  it('没选过就用当前会话所属工作区的默认权限', () => {
    expect(resolvePermissionMode(null, 'auto')).toBe('auto')
    expect(resolvePermissionMode(undefined, 'manual')).toBe('manual')
  })

  it('两侧都没有（或服务端给了不认识的值）都回落到 manual', () => {
    expect(resolvePermissionMode(null, null)).toBe('manual')
    expect(resolvePermissionMode(null, undefined)).toBe('manual')
    expect(resolvePermissionMode(undefined, '')).toBe('manual')
    // 工作区里存着一个前端还不认识的模式：不能把它当成用户的选择发出去（服务端 422）。
    expect(resolvePermissionMode(null, 'yolo')).toBe('manual')
    // 旧名也走同一条回落：它不再是合法值。
    expect(resolvePermissionMode(null, 'strict')).toBe('manual')
    expect(resolvePermissionMode(null, 1)).toBe('manual')
  })

  it('工作区默认值即使自称 full 也不被采用（full ≠ default）', () => {
    // 服务端不接受 full 作默认值；万一注册表被手改成它，前端按"不认识的值"回落，
    // 而不是把"关掉沙箱"变成一个每次打开会话都自动生效的缺省。
    expect(resolvePermissionMode(null, 'full')).toBe('manual')
    // 但用户**显式**选了 full 仍然有效：那是这一次运行的授权。
    expect(resolvePermissionMode('full', 'manual')).toBe('full')
  })
})

describe('提交体组装', () => {
  it('字段按契约命名（extra=forbid，少发或多发都是 422）', () => {
    const body = buildStartRunInput({
      prompt: '读 pyproject.toml',
      branch: 'b2',
      autoApprove: false,
      permission: 'manual',
    })

    expect(body).toEqual({
      prompt: '读 pyproject.toml',
      auto_approve: false,
      branch: 'b2',
      permission: 'manual',
      full_access_ack: false,
    })
  })

  it('permission 一定是回落后的具体档，不会是 undefined', () => {
    const body = buildStartRunInput({
      prompt: 'x',
      branch: 'main',
      autoApprove: true,
      permission: resolvePermissionMode(null, null),
    })

    expect(body.permission).toBe('manual')
    expect(Object.values(body)).not.toContain(undefined)
  })

  it('只有 full 带显式授权，别的模式一律 false', () => {
    expect(needsFullAck('full')).toBe(true)
    expect(needsFullAck('manual')).toBe(false)
    expect(needsFullAck('auto')).toBe(false)

    const full = buildStartRunInput({
      prompt: 'x',
      branch: 'main',
      autoApprove: false,
      permission: 'full',
    })
    expect(full.full_access_ack).toBe(true)
  })

  it('授权凭据必须由组装点统一填：漏了服务端就 422', () => {
    for (const mode of PERMISSION_MODES) {
      const body = buildStartRunInput({
        prompt: 'x',
        branch: 'main',
        autoApprove: false,
        permission: mode,
      })
      expect(body.full_access_ack).toBe(needsFullAck(mode))
    }
  })
})
