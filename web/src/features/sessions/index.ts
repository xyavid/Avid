/**
 * 会话导航列（task-3）的统一出口。
 *
 * 外壳只该 import 这个文件：组件的内部目录结构（components/ 与 lib/ 怎么分）
 * 是实现细节，导出面的名字才是契约。
 */

export { SessionNav } from './components/SessionNav'
export type { SessionNavProps } from './components/SessionNav'

export { SessionListHeader } from './components/SessionListHeader'
export type { SessionListHeaderProps } from './components/SessionListHeader'

export { NavSearch } from './components/NavSearch'
export type { NavSearchProps } from './components/NavSearch'

export { SessionItem } from './components/SessionItem'
export type { SessionItemProps } from './components/SessionItem'

export { WorkspaceFolder } from './components/WorkspaceFolder'
export type { WorkspaceFolderProps } from './components/WorkspaceFolder'

export { DeleteSessionDialog } from './components/DeleteSessionDialog'
export type { DeleteSessionDialogProps } from './components/DeleteSessionDialog'

export { ORPHAN_GROUP_KEY, filterSessions, groupSessions, sessionLabel } from './lib/navTree'
export type { NavGroup } from './lib/navTree'

export { relativeTime } from './lib/relativeTime'
