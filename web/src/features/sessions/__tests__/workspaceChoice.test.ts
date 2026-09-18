import { describe, expect, it } from 'vitest'

import { pickWorkspace } from '../lib/workspaceChoice'
import type { WorkspaceSummary } from '../../../api/types'

function workspace(id: string, extra: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {
    id,
    root: `/tmp/${id}`,
    name: id,
    created_at: 0,
    last_used_at: 0,
    default_permission: 'strict',
    is_default: false,
    ...extra,
  }
}

describe('新建会话的缺省工作区', () => {
  it('用户选过的那个还在列表里，就用它', () => {
    const list = [workspace('a', { is_default: true }), workspace('b')]
    expect(pickWorkspace(list, 'b')?.id).toBe('b')
  })

  it('没选过（或选的那个已经不在列表里）时用 is_default 的那个', () => {
    const list = [workspace('a'), workspace('b', { is_default: true }), workspace('c')]
    expect(pickWorkspace(list, null)?.id).toBe('b')
    expect(pickWorkspace(list, 'gone')?.id).toBe('b')
  })

  it('没有 is_default（多工作区模式）时取列表第一个，也就是最近用过的那个', () => {
    const list = [workspace('recent'), workspace('older')]
    expect(pickWorkspace(list, null)?.id).toBe('recent')
  })

  it('列表为空返回 null：调用方据此禁用新建，而不是发出必然 400 的请求', () => {
    expect(pickWorkspace([], null)).toBeNull()
    expect(pickWorkspace([], 'gone')).toBeNull()
  })
})
