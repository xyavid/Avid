/**
 * navTree 的用例：分组、排序、兜底组、过滤、显示名。
 *
 * 默认 node 环境（不写 jsdom 文件头），因为这三个函数都不碰 DOM——
 * 于是"原语还没落地"也不会挡住这层验证。
 */

import { describe, expect, it } from 'vitest'

import type { SessionSummary, WorkspaceSummary } from '../../../../api/types'
import {
  BUCKET_WINDOW_MS,
  ORPHAN_GROUP_KEY,
  ORPHAN_LABEL,
  bucketOf,
  filterSessions,
  groupByBucket,
  groupSessions,
  sessionLabel,
  sessionWorkspaceLabel,
} from '../navTree'

function ws(
  id: string,
  name: string | null,
  lastUsedAt = 0,
  root = `/w/${id}`,
): WorkspaceSummary {
  return {
    id,
    root,
    name,
    created_at: 0,
    last_used_at: lastUsedAt,
    default_permission: null,
    is_default: false,
  }
}

function sess(
  id: string,
  name: string | null,
  workspaceId: string | null,
  createdAt: number,
  messageCount = 3,
): SessionSummary {
  return {
    id,
    name,
    created_at: createdAt,
    storage_version: 1,
    parent_session_id: null,
    workspace:
      workspaceId === null
        ? null
        : { id: workspaceId, root: `/w/${workspaceId}`, name: null, default_permission: null },
    message_count: messageCount,
    active_run_id: null,
    truncated_tail: false,
  }
}

describe('groupSessions', () => {
  it('按工作区分组，空工作区不出组', () => {
    const groups = groupSessions(
      [sess('a1', '甲一', 'ws-a', 10), sess('b1', '乙一', 'ws-b', 10)],
      [ws('ws-a', '甲项目'), ws('ws-b', '乙项目'), ws('ws-empty', '空项目')],
    )

    expect(groups.map((g) => g.key)).toEqual(['ws-a', 'ws-b'])
    expect(groups.map((g) => g.label)).toEqual(['甲项目', '乙项目'])
    expect(groups.map((g) => g.sessions.map((s) => s.id))).toEqual([['a1'], ['b1']])
  })

  it('组内按 created_at 降序（本 DTO 没有 last_used 字段）', () => {
    const groups = groupSessions(
      [sess('old', '旧', 'ws-a', 100), sess('new', '新', 'ws-a', 300), sess('mid', '中', 'ws-a', 200)],
      [ws('ws-a', '甲项目')],
    )

    expect(groups[0]?.sessions.map((s) => s.id)).toEqual(['new', 'mid', 'old'])
  })

  it('组顺序按工作区 last_used_at 降序', () => {
    const groups = groupSessions(
      [sess('a1', '甲一', 'ws-a', 10), sess('b1', '乙一', 'ws-b', 10)],
      [ws('ws-a', '甲项目', 100), ws('ws-b', '乙项目', 500)],
    )

    expect(groups.map((g) => g.key)).toEqual(['ws-b', 'ws-a'])
  })

  it('归属缺失或归属的工作区已不在注册表时落到兜底组，不丢会话', () => {
    const input = [
      sess('known', '在册', 'ws-a', 10),
      sess('gone', '注册表已摘', 'ws-gone', 20),
      sess('none', '没有归属', null, 30),
    ]
    const groups = groupSessions(input, [ws('ws-a', '甲项目')])

    const orphan = groups.find((g) => g.key === ORPHAN_GROUP_KEY)
    expect(orphan?.key).toBe('unknown')
    expect(orphan?.label).toBe('未归属工作区')
    expect(orphan?.sessions.map((s) => s.id)).toEqual(['none', 'gone'])
    // 条目守恒：分组后的会话总数与输入总数相等（单测钉住这条不变量）
    expect(groups.reduce((sum, g) => sum + g.sessions.length, 0)).toBe(input.length)
    expect(groups.flatMap((g) => g.sessions.map((s) => s.id)).sort()).toEqual([
      'gone',
      'known',
      'none',
    ])
  })

  it('一个会话都没有时返回空数组', () => {
    expect(groupSessions([], [ws('ws-a', '甲项目')])).toEqual([])
  })

  it('工作区没有名字时用 root 兜底', () => {
    const [group] = groupSessions([sess('a1', '甲一', 'ws-a', 10)], [ws('ws-a', null, 0, '/srv/code')])

    expect(group?.label).toBe('/srv/code')
    expect(group?.root).toBe('/srv/code')
  })
})

describe('filterSessions', () => {
  const all = [sess('1', 'Refactor Auth', 'ws-a', 1), sess('2', '修登录', 'ws-a', 2), sess('3', null, 'ws-a', 3)]

  it('不区分大小写地匹配名字子串', () => {
    expect(filterSessions(all, 'AUTH').map((s) => s.id)).toEqual(['1'])
    expect(filterSessions(all, '登录').map((s) => s.id)).toEqual(['2'])
  })

  it('空查询与全空白查询都返回全部', () => {
    expect(filterSessions(all, '')).toBe(all)
    expect(filterSessions(all, '   ')).toBe(all)
  })

  it('查不到时返回空数组，而不是抛错', () => {
    expect(filterSessions(all, '不存在')).toEqual([])
  })
})

describe('sessionLabel', () => {
  it('有名字时用名字（并去掉首尾空白）', () => {
    expect(sessionLabel(sess('abcdef123', '  修登录  ', 'ws-a', 1))).toBe('修登录')
  })

  it('名字为空或全空白时回落到「未命名会话 · 短 id」', () => {
    expect(sessionLabel(sess('abcdef123', null, 'ws-a', 1))).toBe('未命名会话 · abcdef')
    expect(sessionLabel(sess('abcdef123', '   ', 'ws-a', 1))).toBe('未命名会话 · abcdef')
  })

  it('短 id 短于 6 位时原样给出', () => {
    expect(sessionLabel(sess('ab', null, 'ws-a', 1))).toBe('未命名会话 · ab')
  })
})

/*
 * 时间分桶的边界要**逐毫秒**钉住：这条规则的全部内容就是那一条比较，
 * 差一毫秒就把"今天刚建的会话"推进"更早"，而那种错误很难从界面上看出来。
 */
describe('bucketOf', () => {
  const NOW = Date.UTC(2024, 0, 10, 12, 0, 0)

  it('24 小时内（含正好 24h）算今天', () => {
    expect(bucketOf(NOW, NOW)).toBe('today')
    expect(bucketOf(NOW - 60_000, NOW)).toBe('today')
    expect(bucketOf(NOW - (BUCKET_WINDOW_MS - 1), NOW)).toBe('today')
    // 描述写的是"超过 24h 归更早"，所以临界值本身属于今天。
    expect(bucketOf(NOW - BUCKET_WINDOW_MS, NOW)).toBe('today')
  })

  it('超过 24h 归更早', () => {
    expect(bucketOf(NOW - BUCKET_WINDOW_MS - 1, NOW)).toBe('earlier')
    expect(bucketOf(NOW - 10 * BUCKET_WINDOW_MS, NOW)).toBe('earlier')
  })

  it('未来时间归今天；非法时间归更早（宁可放到旧的桶里，也不假装它新鲜）', () => {
    expect(bucketOf(NOW + 5_000, NOW)).toBe('today')
    expect(bucketOf(Number.NaN, NOW)).toBe('earlier')
  })
})

describe('groupByBucket', () => {
  const NOW = Date.UTC(2024, 0, 10, 12, 0, 0)

  it('分成今天 / 更早两组，组内按创建时间降序', () => {
    const groups = groupByBucket(
      [
        sess('old', '旧', 'ws-a', NOW - 3 * BUCKET_WINDOW_MS),
        sess('new', '新', 'ws-a', NOW - 1_000),
        sess('mid', '中', 'ws-a', NOW - 2 * 60 * 60_000),
      ],
      NOW,
    )

    expect(groups.map((g) => g.bucket)).toEqual(['today', 'earlier'])
    expect(groups.map((g) => g.label)).toEqual(['今天', '更早'])
    expect(groups[0]?.sessions.map((s) => s.id)).toEqual(['new', 'mid'])
    expect(groups[1]?.sessions.map((s) => s.id)).toEqual(['old'])
  })

  it('空桶不出，且条目守恒：分桶后总数 = 输入总数', () => {
    const input = [sess('a', '甲', 'ws-a', NOW - 1_000), sess('b', '乙', null, NOW - 5 * BUCKET_WINDOW_MS)]
    const groups = groupByBucket(input, NOW)

    expect(groups.map((g) => g.bucket)).toEqual(['today', 'earlier'])
    expect(groups.reduce((sum, g) => sum + g.sessions.length, 0)).toBe(input.length)

    // 只有"更早"里有会话时，今天那一组不该留一个空标题。
    const onlyOld = groupByBucket([sess('c', '丙', 'ws-a', NOW - 5 * BUCKET_WINDOW_MS)], NOW)
    expect(onlyOld.map((g) => g.bucket)).toEqual(['earlier'])
    expect(onlyOld[0]?.label).toBe('更早')
  })

  it('不修改输入数组的顺序', () => {
    const input = [sess('a', '甲', 'ws-a', NOW - 5 * BUCKET_WINDOW_MS), sess('b', '乙', 'ws-a', NOW - 1_000)]
    const snapshot = input.map((s) => s.id)

    groupByBucket(input, NOW)

    expect(input.map((s) => s.id)).toEqual(snapshot)
  })
})

describe('sessionWorkspaceLabel', () => {
  it('在册工作区用它的名字；名字为空时回落 root', () => {
    expect(sessionWorkspaceLabel(sess('a', '甲', 'ws-a', 1), [ws('ws-a', '甲项目')])).toBe('甲项目')
    expect(sessionWorkspaceLabel(sess('a', '甲', 'ws-a', 1), [ws('ws-a', null, 0, '/srv/code')])).toBe(
      '/srv/code',
    )
  })

  it('归属缺失或已从注册表摘掉时回落「未归属工作区」，绝不返回空串', () => {
    expect(sessionWorkspaceLabel(sess('a', '甲', null, 1), [ws('ws-a', '甲项目')])).toBe(ORPHAN_LABEL)
    expect(sessionWorkspaceLabel(sess('a', '甲', 'ws-gone', 1), [ws('ws-a', '甲项目')])).toBe(
      ORPHAN_LABEL,
    )
  })
})
