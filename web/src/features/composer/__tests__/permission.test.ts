import { describe, expect, it } from 'vitest'

import {
  buildStartRunInput,
  DEFAULT_PERMISSION,
  isPermissionMode,
  PERMISSION_MODES,
  resolvePermissionMode,
} from '../lib/permission'

describe('权限模式的取值集合', () => {
  it('恰好三档，顺序与服务端 Literal 一致', () => {
    expect(PERMISSION_MODES).toEqual(['strict', 'workspace', 'system'])
  })

  it('兜底档是 strict', () => {
    expect(DEFAULT_PERMISSION).toBe('strict')
  })

  it('只认这三档，别的一律不是', () => {
    expect(isPermissionMode('strict')).toBe(true)
    expect(isPermissionMode('workspace')).toBe(true)
    expect(isPermissionMode('system')).toBe(true)
    expect(isPermissionMode('STRICT')).toBe(false)
    expect(isPermissionMode('')).toBe(false)
    expect(isPermissionMode(null)).toBe(false)
    expect(isPermissionMode(undefined)).toBe(false)
    expect(isPermissionMode(1)).toBe(false)
  })
})

describe('缺省回落', () => {
  it('显式选过的档优先于工作区默认', () => {
    expect(resolvePermissionMode('system', 'strict')).toBe('system')
    expect(resolvePermissionMode('strict', 'system')).toBe('strict')
  })

  it('没选过就用当前会话所属工作区的默认权限', () => {
    expect(resolvePermissionMode(null, 'workspace')).toBe('workspace')
    expect(resolvePermissionMode(undefined, 'system')).toBe('system')
  })

  it('两侧都没有（或服务端给了不认识的值）都回落到 strict', () => {
    expect(resolvePermissionMode(null, null)).toBe('strict')
    expect(resolvePermissionMode(null, undefined)).toBe('strict')
    expect(resolvePermissionMode(undefined, '')).toBe('strict')
    // 工作区里存着一个前端还不认识的模式：不能把它当成用户的选择发出去（服务端 422）。
    expect(resolvePermissionMode(null, 'yolo')).toBe('strict')
    expect(resolvePermissionMode(null, 1)).toBe('strict')
  })
})

describe('提交体组装', () => {
  it('四个字段都按契约命名（extra=forbid，少发或多发都是 422）', () => {
    const body = buildStartRunInput({
      prompt: '读 pyproject.toml',
      branch: 'b2',
      autoApprove: false,
      permission: 'workspace',
    })

    expect(body).toEqual({
      prompt: '读 pyproject.toml',
      auto_approve: false,
      branch: 'b2',
      permission: 'workspace',
    })
  })

  it('permission 一定是回落后的具体档，不会是 undefined', () => {
    const body = buildStartRunInput({
      prompt: 'x',
      branch: 'main',
      autoApprove: true,
      permission: resolvePermissionMode(null, null),
    })

    expect(body.permission).toBe('strict')
    expect(Object.values(body)).not.toContain(undefined)
  })
})
