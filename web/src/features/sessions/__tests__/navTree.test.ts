import { describe, expect, it } from 'vitest'

import type { SessionSummary, WorkspaceSummary } from '../../../api/types'
import {
  PREVIEW_SESSIONS,
  defaultExpandedWorkspace,
  filterGroups,
  groupByWorkspace,
  isExpanded,
  matchesQuery,
  visibleSessions,
} from '../lib/navTree'

function workspace(id: string, name = id): WorkspaceSummary {
  return {
    id,
    root: `/tmp/${name}`,
    name,
    created_at: 0,
    last_used_at: 0,
    default_permission: 'strict',
    is_default: false,
  }
}

function session(id: string, workspaceId: string | null): SessionSummary {
  return {
    id,
    name: id,
    created_at: 0,
    storage_version: 1,
    parent_session_id: null,
    workspace:
      workspaceId === null
        ? null
        : { id: workspaceId, root: `/tmp/${workspaceId}`, name: workspaceId, default_permission: 'strict' },
    message_count: 0,
    active_run_id: null,
    truncated_tail: false,
  }
}

describe('groupByWorkspace', () => {
  it('按服务端顺序归拢，每组只装自己的会话', () => {
    const groups = groupByWorkspace(
      [workspace('a'), workspace('b')],
      [session('s1', 'a'), session('s2', 'b'), session('s3', 'a')],
    )

    expect(groups.map((group) => group.workspace?.id)).toEqual(['a', 'b'])
    expect(groups[0]?.sessions.map((item) => item.id)).toEqual(['s1', 's3'])
    expect(groups[1]?.sessions.map((item) => item.id)).toEqual(['s2'])
  })

  it('没有会话的工作区也保留（空文件夹要能露出来建会话）', () => {
    const groups = groupByWorkspace([workspace('a'), workspace('b')], [])

    expect(groups).toHaveLength(2)
    expect(groups.every((group) => group.sessions.length === 0)).toBe(true)
  })

  it('归属查不到的会话不会被丢掉', () => {
    const groups = groupByWorkspace([workspace('a')], [session('s1', 'gone'), session('s2', null)])

    expect(groups).toHaveLength(2)
    expect(groups[1]?.workspace).toBeNull()
    expect(groups[1]?.sessions.map((item) => item.id)).toEqual(['s1', 's2'])
  })
})

describe('visibleSessions', () => {
  const many = Array.from({ length: PREVIEW_SESSIONS + 3 }, (_, index) => session(`s${index}`, 'a'))

  it('收起时只露前几个，并给出还剩多少', () => {
    expect(visibleSessions(many, false)).toEqual({
      shown: many.slice(0, PREVIEW_SESSIONS),
      hidden: 3,
    })
  })

  it('展开后全露', () => {
    expect(visibleSessions(many, true)).toEqual({ shown: many, hidden: 0 })
  })

  it('不超过上限时没有"其余"', () => {
    expect(visibleSessions(many.slice(0, 2), false)).toEqual({
      shown: many.slice(0, 2),
      hidden: 0,
    })
  })
})

describe('defaultExpandedWorkspace', () => {
  const groups = [
    { workspace: workspace('a'), sessions: [] },
    { workspace: workspace('b'), sessions: [session('s1', 'b')] },
  ]

  it('优先展开装着当前会话的那个', () => {
    expect(defaultExpandedWorkspace(groups, 's1')).toBe('b')
  })

  it('没有当前会话就展开第一个有会话的', () => {
    expect(defaultExpandedWorkspace(groups, null)).toBe('b')
  })

  it('一个会话都没有时展开第一个：空工作区也要露出新建入口', () => {
    expect(defaultExpandedWorkspace([{ workspace: workspace('a'), sessions: [] }], null)).toBe('a')
  })

  it('一个工作区都没有时返回 null', () => {
    expect(defaultExpandedWorkspace([], null)).toBeNull()
  })
})

describe('isExpanded', () => {
  it('用户操作优先于默认值', () => {
    expect(isExpanded('a', { a: true }, 'b')).toBe(true)
    expect(isExpanded('b', { b: false }, 'b')).toBe(false)
  })

  it('没操作过就按默认值', () => {
    expect(isExpanded('b', {}, 'b')).toBe(true)
    expect(isExpanded('a', {}, 'b')).toBe(false)
  })
})

describe('matchesQuery / filterGroups（按会话搜索）', () => {
  const named = [
    { ...session('s1', 'a'), name: '前端架构方案梳理' },
    { ...session('s2', 'a'), name: null },
    { ...session('s3', 'b'), name: '读文件并计算' },
  ]
  const groupsWithNames = [
    { workspace: workspace('a', 'Avid'), sessions: named.slice(0, 2) },
    { workspace: workspace('b', 'blog'), sessions: named.slice(2) },
  ]

  it('空查询不做过滤（原样返回，调用方据此判断在不在搜索）', () => {
    expect(filterGroups(groupsWithNames, '', '未命名会话')).toBe(groupsWithNames)
    expect(filterGroups(groupsWithNames, '   ', '未命名会话')).toBe(groupsWithNames)
  })

  it('按名字包含匹配，大小写不敏感，前后空白忽略', () => {
    expect(matchesQuery(named[0]!, '架构', '未命名会话')).toBe(true)
    expect(matchesQuery(named[0]!, ' 架构 ', '未命名会话')).toBe(true)
    expect(matchesQuery(named[2]!, '读文件', '未命名会话')).toBe(true)
    expect(matchesQuery(named[2]!, '架构', '未命名会话')).toBe(false)
  })

  it('没有名字的会话按界面显示的名字（未命名会话）匹配', () => {
    expect(matchesQuery(named[1]!, '未命名', '未命名会话')).toBe(true)
    expect(matchesQuery(named[1]!, '架构', '未命名会话')).toBe(false)
  })

  it('过滤后只留命中的会话，没有命中的工作区整组消失', () => {
    const filtered = filterGroups(groupsWithNames, '架构', '未命名会话')

    expect(filtered.map((group) => group.workspace?.id)).toEqual(['a'])
    expect(filtered[0]?.sessions.map((item) => item.id)).toEqual(['s1'])
  })

  it('全部不命中时返回空表（界面据此给"没有匹配"）', () => {
    expect(filterGroups(groupsWithNames, '不存在的会话名', '未命名会话')).toEqual([])
  })

  it('不按工作区名匹配：搜工作区名不该把整组捞出来', () => {
    expect(filterGroups(groupsWithNames, 'blog', '未命名会话')).toEqual([])
  })
})
