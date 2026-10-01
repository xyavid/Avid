/**
 * 根组件（装配层）。
 *
 * 这一层只做三件事：装 Provider、把导航量接到左栏、把一次运行的量接到页面。
 * 它不含任何视觉细节，也不含任何状态逻辑——所有取舍都在各自的模块里。
 *
 * **`key={sessionId}` 是本次结构设计的关键一招**：换会话时整棵对话页卸载重建，
 * 于是"上一轮的流式累积、待决审批、错误横幅"在类型上就不可能残留。若改成在
 * effect 里逐项 reset，漏一项就会出现"新会话里挂着旧会话的审批条"。
 * 代价是切回旧会话不保留滚动位置——可接受，历史本来就要重拉。
 */

import { useMemo } from 'react'

import { SessionNav } from './features/sessions'
import { ConversationPage } from './layouts/ConversationPage'
import { AppProvider, useApp } from './state/appStore'
import type { SessionData } from './state/useSessionData'
import { useBranches } from './state/useBranches'

export interface AppProps {
  /**
   * 数据层注入点，**只给测试用**。
   *
   * 为什么开在 `App` 上，而不是让测试自己包一层 `<AppProvider data={...}>`：
   * `App` 内部已经装了 Provider（`main.tsx` 就是这么用它的），外面再包一层
   * **完全不起作用**——`<App/>` 里那份 Provider 会用真实数据层（测试环境里是空的）
   * 压掉外层注入的数据。这个坑真实发生过：上下文探针读到了注入的会话，
   * 而页面主体渲染的却是空态，排查方向被带偏了很久。
   *
   * 把注入点放在公开入口上，测试走的就与生产是同一条装配路径，差的只有数据来源。
   */
  data?: SessionData
}

export function App({ data }: AppProps = {}) {
  return (
    <AppProvider {...(data === undefined ? {} : { data })}>
      <AppView />
    </AppProvider>
  )
}

function AppView() {
  const app = useApp()
  const sessionId = app.activeSession?.id ?? null

  const runningSessionIds = useMemo(
    () =>
      new Set(
        app.sessions.filter((item) => item.active_run_id !== null).map((item) => item.id),
      ),
    [app.sessions],
  )

  const nav = (
    <SessionNav
      sessions={app.sessions}
      workspaces={app.workspaces}
      activeSessionId={sessionId}
      runningSessionIds={runningSessionIds}
      collapsed={app.nav.navCollapsed}
      error={app.error}
      onToggleCollapsed={app.actions.toggleNav}
      onSelect={app.actions.selectSession}
      onNewSession={() => {
        void app.actions.createSession()
      }}
      onRename={(id, name) => {
        void app.actions.renameSession(id, name)
      }}
      onDelete={(id) => {
        void app.actions.deleteSession(id)
      }}
    />
  )

  /*
   * 分支状态按会话重建（`key`），与对话页同一手法。
   * `BranchesHost` 是必需的中间层：hook 不能条件调用，而 `sessionId` 为 null 时
   * 也不该发生分支请求。用 render prop 把结果交给页面，避免 `branches` 进全局 store
   * （它只服务头部的一个选择器，进全局就要多写一处"换会话清分支"的联动）。
   */
  return (
    <BranchesHost key={sessionId ?? 'none'} sessionId={sessionId}>
      {(branches, branchError) => (
        <ConversationPage
          session={app.activeSession}
          branch={app.nav.branch}
          branches={branches}
          branchError={branchError}
          permission={app.permission}
          onPermissionChange={app.setPermission}
          sandbox={app.sandbox}
          model={app.meta?.capabilities.model ?? null}
          workspaces={app.workspaces}
          navCollapsed={app.nav.navCollapsed}
          inspectorToolCallId={app.nav.inspectorToolCallId}
          inspectorTab={app.nav.inspectorTab}
          onOpenInspector={app.actions.openInspector}
          onCloseInspector={app.actions.closeInspector}
          onInspectorTab={app.actions.setInspectorTab}
          onSelectBranch={app.actions.selectBranch}
          canCreateSession={app.defaultWorkspace !== null}
          onCreateSession={() => {
            void app.actions.createSession()
          }}
          onRefresh={() => {
            void app.actions.refresh()
          }}
          nav={nav}
        />
      )}
    </BranchesHost>
  )
}

function BranchesHost({
  sessionId,
  children,
}: {
  sessionId: string | null
  children: (
    branches: ReturnType<typeof useBranches>['branches'],
    error: string | null,
  ) => React.ReactNode
}) {
  const { branches, error } = useBranches(sessionId)
  return <>{children(branches, error)}</>
}
