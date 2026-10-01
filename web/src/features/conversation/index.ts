/**
 * 对话时间线展示层的统一出口。
 *
 * 组件与纯函数都从这里出：调用方（layouts / App）只 import 这个入口，
 * 将来把 `lib/` 拆包或改分组时，不用去改所有调用点。
 */
export { blockToolCallId, groupTimeline } from './lib/groupTimeline'
export type { TimelineBlock } from './lib/groupTimeline'
export { TODO_TOOL_NAME, latestTodos } from './lib/todos'
export type { TodoItem, TodoStatus } from './lib/todos'
export { relativeTime } from './lib/relativeTime'
export { formatCache, formatUsage } from './lib/usage'

export { ConversationHeader } from './components/ConversationHeader'
export type { ConversationHeaderProps } from './components/ConversationHeader'
export { MessageBubble } from './components/MessageBubble'
export type { MessageBubbleProps } from './components/MessageBubble'
export { NoticeRow } from './components/NoticeRow'
export type { NoticeRowProps } from './components/NoticeRow'
export { StatusBanner } from './components/StatusBanner'
export type { StatusBannerProps } from './components/StatusBanner'
export { Timeline } from './components/Timeline'
export type { TimelineProps } from './components/Timeline'
export { TodoPanel } from './components/TodoPanel'
export type { TodoPanelProps } from './components/TodoPanel'
export { ToolCard } from './components/ToolCard'
export type { ToolCardProps } from './components/ToolCard'
