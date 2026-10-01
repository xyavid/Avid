/**
 * 工作区域（右栏信息面板 + 工作区管理）的统一出口。
 *
 * 与 `ui/primitives` 同一个理由：调用方（Lead 的装配层）只 import 这一处，
 * 不摸实现文件。这样"面板内部拆成几个组件"对调用方不可见，也让我们能一眼读出
 * 这个 feature 对外承诺了多少个名字。
 *
 * 网络层（`api/workspaces.ts`）**不从转出**：它是端点封装，有自己该在的目录
 * （`src/api/`），从这里转一手只会让人以为"工作区域的网络出口是 feature 私有的"。
 */

export { AddWorkspaceDialog } from './components/AddWorkspaceDialog'
export type { AddWorkspaceDialogProps } from './components/AddWorkspaceDialog'
export { InfoRail } from './components/InfoRail'
export type { InfoRailProps } from './components/InfoRail'
export { WorkspaceList } from './components/WorkspaceList'
export type { WorkspaceListProps } from './components/WorkspaceList'
export { WorkspaceManager } from './components/WorkspaceManager'
export type { WorkspaceManagerProps } from './components/WorkspaceManager'

/* 显示口径与错误文案是这组组件的对外语义的一部分（Lead 的状态层可能也要用同一套），
   所以一并转出，而不是逼调用方去 import 内部的 lib 路径。 */
export { leafOf, orDash, permissionLabel, workspaceLabel } from './lib/labels'
export { describeWorkspaceError } from './lib/workspaceError'
