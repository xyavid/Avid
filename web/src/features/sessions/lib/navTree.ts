/**
 * 会话导航列的纯逻辑：分组、过滤、显示名。
 *
 * 为什么把这三件事从组件里拿出来：它们是**可独立验证的规则**（工作区归属、
 * 排序、空组不出），塞进 TSX 就只能靠渲染结果反推；这里三个函数都不碰 DOM，
 * 能在默认 node 环境一次性把边界跑完。
 *
 * 另一个刻意点：本文件不 import 任何 React / ui 原语，于是原语还没落地时
 * 这层也能单独跑绿（并行的重构里这一点很值钱）。
 */

import type { SessionSummary, WorkspaceSummary } from '../../../api/types'

/**
 * 未归属组的 key 与标题。
 *
 * 会话的归属是创建时写进 header 的静态事实，而工作区注册表是用户可改的
 * （`DELETE /api/workspaces/{id}` 只摘登记、不删会话数据）。所以"会话所属的
 * 工作区不在注册表里"是**正常状态**，不是错误——这种情况不能把会话丢掉，
 * 得单开一组。key 用固定字面量 `'unknown'` 而不是路径：它要当 React key 与
 * 测试里的定位锚点，稳定比可读更重要。
 */
export const ORPHAN_GROUP_KEY = 'unknown'

/** 兜底组的标题。会话与「未归属工作区」这个说法在各处必须一致。 */
export const ORPHAN_LABEL = '未归属工作区'

export interface NavGroup {
  key: string
  label: string
  root: string | null
  sessions: SessionSummary[]
}

/** name 可空（注册表允许只登记路径），此时用 root 兜底，绝不给空标题。 */
function workspaceLabel(workspace: WorkspaceSummary): string {
  return workspace.name?.trim() || workspace.root
}

/**
 * 按工作区分组。
 *
 * 组顺序取工作区的 `last_used_at` 降序（最近干活的项目在最上面）；并列时依赖
 * `Array.prototype.sort` 的稳定性保持注册表原序，避免刷新后组位置跳动。
 *
 * 组内排序只用 `created_at` 降序：`SessionSummary` 这个 DTO **没有** last_used
 * 字段，猜一个不存在的字段名只会得到一条永远不生效的排序分支（而且看起来
 * 像在工作）。线格式真加上那天再改这里，改一处即可。
 *
 * **空工作区不出组**：候选列表里登记了但一个会话都没有的目录不该占位置。
 */
export function groupSessions(
  sessions: SessionSummary[],
  workspaces: WorkspaceSummary[],
): NavGroup[] {
  const byKey = new Map<string, NavGroup>()

  const ordered = [...workspaces].sort((a, b) => b.last_used_at - a.last_used_at)
  for (const workspace of ordered) {
    byKey.set(workspace.id, {
      key: workspace.id,
      label: workspaceLabel(workspace),
      root: workspace.root,
      sessions: [],
    })
  }

  const orphan: NavGroup = {
    key: ORPHAN_GROUP_KEY,
    label: ORPHAN_LABEL,
    root: null,
    sessions: [],
  }

  // 条目守恒由结构保证：每个输入会话要么进它自己的工作区组，要么进兜底组，
  // 不存在第三条分支（下面的 filter 只丢掉"组里没会话"的空组）
  for (const session of sessions) {
    const workspaceId = session.workspace?.id ?? null
    const group = (workspaceId === null ? undefined : byKey.get(workspaceId)) ?? orphan
    group.sessions.push(session)
  }

  const groups = [...byKey.values(), orphan].filter((group) => group.sessions.length > 0)
  for (const group of groups) {
    group.sessions.sort((a, b) => b.created_at - a.created_at)
  }
  return groups
}

/**
 * 不区分大小写的名称子串匹配；空查询（含全空白）返回全部。
 *
 * 只匹配 `name`：列表上能看到的标题就是它，拿 id 或时间一起匹配会让"看得见的
 * 字"与"搜得到的字"对不上。未命名会话因此搜不到——它的标题是回落的展示文案，
 * 不是数据里的名字。
 */
export function filterSessions(sessions: SessionSummary[], query: string): SessionSummary[] {
  const needle = query.trim().toLowerCase()
  if (needle === '') return sessions
  return sessions.filter((session) => (session.name ?? '').toLowerCase().includes(needle))
}

/** 短 id：只取前 6 位——它是"两个未命名会话怎么区分"的线索，不是标识符本身。 */
function shortId(id: string): string {
  return id.length > 6 ? id.slice(0, 6) : id
}

/** 会话显示名：name 为空时回落到「未命名会话 · 短 id」。 */
export function sessionLabel(session: SessionSummary): string {
  const name = session.name?.trim()
  if (name) return name
  return `未命名会话 · ${shortId(session.id)}`
}

/*
 * ---------------------------------------------------------------------------
 * 时间分桶（截图里的「今天 / 更早」）
 * ---------------------------------------------------------------------------
 */

export type TimeBucket = 'today' | 'earlier'

export interface BucketGroup {
  bucket: TimeBucket
  label: string
  sessions: SessionSummary[]
}

/** 分桶窗口：24 小时。 */
export const BUCKET_WINDOW_MS = 24 * 60 * 60 * 1000

const BUCKET_LABELS: Record<TimeBucket, string> = { today: '今天', earlier: '更早' }

/**
 * 一条会话落在哪个时间桶里。
 *
 * 边界按"**超过** 24h 才归更早"取：正好 24h 仍算今天。未来时间（负 elapsed）也归
 * 今天——客户端与服务端时钟差几毫秒是常态，把刚创建的会话判进"更早"会很刺眼。
 *
 * `now` 必须是参数：在函数里读 `Date.now()` 会让这条规则不可测，也会让同一份数据
 * 在两次调用间得到不同分桶。
 */
export function bucketOf(createdAt: number, now: number): TimeBucket {
  if (!Number.isFinite(createdAt)) return 'earlier'
  return now - createdAt > BUCKET_WINDOW_MS ? 'earlier' : 'today'
}

/**
 * 按时间分桶，供左栏列表渲染。
 *
 * 两处刻意决定：
 *   · **空桶不出**（与 `groupSessions` 同一条规矩）：没有"更早"的会话时不该留一个空标题；
 *   · 组内**按 created_at 降序**，与 `groupSessions` 的组内序一致——时间桶是外层结构，
 *     下沉的那层排序规则不该因为换了外层就变。
 * 输入数组不被改动（先 spread 再 sort），调用方拿到的还是同一份会话列表。
 */
export function groupByBucket(sessions: SessionSummary[], now: number): BucketGroup[] {
  const today: SessionSummary[] = []
  const earlier: SessionSummary[] = []
  for (const session of sessions) {
    if (bucketOf(session.created_at, now) === 'today') today.push(session)
    else earlier.push(session)
  }

  const newestFirst = (a: SessionSummary, b: SessionSummary): number => b.created_at - a.created_at
  const groups: BucketGroup[] = [
    { bucket: 'today', label: BUCKET_LABELS.today, sessions: today.sort(newestFirst) },
    { bucket: 'earlier', label: BUCKET_LABELS.earlier, sessions: earlier.sort(newestFirst) },
  ]
  // 条目守恒由结构保证：每个输入会话必进 today 或 earlier 之一，这里只丢掉空桶。
  return groups.filter((group) => group.sessions.length > 0)
}

/**
 * 会话所属工作区的**显示标签**，用于列表项上的小标记。
 *
 * 为什么要有这个函数：本轮分组从"按工作区"改成"按时间"，工作区信息下沉成组内标记，
 * 于是"这条会话属于哪个目录"变成逐条计算的规则——把它留在 TSX 里就只能靠渲染结果反推。
 *
 * 归属对不上（header 里没有 workspace，或工作区已从注册表摘掉）时给「未归属工作区」，
 * 与会话列表的空档情况同一套说法；**绝不返回空串**，否则标记会变成一块看不见的凹槽。
 */
export function sessionWorkspaceLabel(
  session: SessionSummary,
  workspaces: WorkspaceSummary[],
): string {
  const id = session.workspace?.id
  if (id === undefined) return ORPHAN_LABEL
  const workspace = workspaces.find((item) => item.id === id)
  return workspace === undefined ? ORPHAN_LABEL : workspaceLabel(workspace)
}

