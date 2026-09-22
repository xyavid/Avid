import { useState } from 'react'

import { Button, Input } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useSessionList, useWorkspaces } from '../../../api/queries'
import {
  defaultExpandedWorkspace,
  filterGroups,
  groupByWorkspace,
  isExpanded,
} from '../lib/navTree'
import { useSessionActions } from '../hooks/useSessionActions'
import { useSessionSearch } from '../hooks/useSessionSearch'
import { DeleteSessionDialog } from './DeleteSessionDialog'
import { RetryNote } from './RetryNote'
import { SessionListHeader } from './SessionListHeader'
import { WorkspaceFolder } from './WorkspaceFolder'

export interface SessionListProps {
  activeId: string | null
  onSelect: (sessionId: string) => void
}

/**
 * 导航列：**工作区像文件夹，会话装在它里面**。
 *
 * 结构上不再有"新建会话 + 工作区下拉"这一对：在哪个文件夹上点 ＋ 就在哪个工作区建会话，
 * 于是"归属"永远是点出来的那一下，不靠一个可能忘了改的下拉框。
 *
 * 这个组件只负责**长什么样**：分组规则在 `lib/navTree.ts`（纯函数，有单测），
 * 对会话与工作区的每一件改动在 `hooks/useSessionActions.ts`，搜索态在
 * `hooks/useSessionSearch.ts`，删除确认在 `components/DeleteSessionDialog.tsx`。
 * 它自己只留折叠覆盖表——那是"每个文件夹各自展开到哪"的纯界面态。
 */
export function SessionList({ activeId, onSelect }: SessionListProps) {
  const { t } = useTranslation()
  const list = useSessionList()
  const workspaces = useWorkspaces()
  // 折叠覆盖表：用户点过的那些记在这里，没点过的按默认规则展开（数据是异步来的，
  // 默认值不能写进 state 初值）。它是纯界面展开态，不进 uiStore（服务端状态不留副本）。
  const [toggled, setToggled] = useState<Record<string, boolean>>({})

  const expandWorkspace = (workspaceId: string) =>
    setToggled((previous) => ({ ...previous, [workspaceId]: true }))

  const actions = useSessionActions({
    onSelectSession: onSelect,
    onExpandWorkspace: expandWorkspace,
  })
  const search = useSessionSearch()

  const allGroups = groupByWorkspace(workspaces.data ?? [], list.data ?? [])
  const searching = search.query.trim() !== ''
  const groups = filterGroups(allGroups, search.query, t('sessions.unnamed'))
  const defaultId = defaultExpandedWorkspace(allGroups, activeId)

  return (
    <section
      className="flex h-full flex-col gap-3 p-3"
      aria-label={t('sessions.workspacesTitle')}
    >
      <SessionListHeader
        searchOpen={search.open}
        canAddWorkspace={actions.canAddWorkspace}
        adding={actions.adding}
        onToggleSearch={search.toggle}
        onAddWorkspace={actions.addWorkspaceByPicker}
      />

      {search.open ? (
        <div className="flex items-center gap-1">
          <Input
            ref={search.input}
            type="search"
            aria-label={t('sessions.search.label')}
            placeholder={t('sessions.search.placeholder')}
            value={search.query}
            onChange={(event) => search.setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') search.close()
            }}
          />
          <Button
            size="icon"
            variant="secondary"
            aria-label={t('sessions.search.clear')}
            title={t('sessions.search.clear')}
            onClick={search.clear}
          >
            <span aria-hidden="true">×</span>
          </Button>
        </div>
      ) : null}

      {actions.notice ? <p className="empty-note">{actions.notice}</p> : null}
      {actions.alert ? <p className="empty-note text-danger">{actions.alert}</p> : null}

      {workspaces.isError ? (
        <RetryNote
          message={t('sessions.workspace.failed')}
          onRetry={() => void workspaces.refetch()}
        />
      ) : null}

      {workspaces.isLoading ? (
        <p className="text-sm text-ink/70">{t('common.loading')}</p>
      ) : null}

      {workspaces.data && workspaces.data.length === 0 ? (
        <p className="empty-note">{t('sessions.workspace.none')}</p>
      ) : null}

      {list.isError ? (
        <RetryNote message={t('errors.title')} onRetry={() => void list.refetch()} />
      ) : null}

      {searching && groups.length === 0 ? (
        <p className="empty-note">{t('sessions.search.empty', { query: search.query.trim() })}</p>
      ) : null}

      <ul className="scroll-area flex-1 space-y-2 pr-1">
        {groups.map((group) => {
          const id = group.workspace?.id
          // 归属查不到的兜底组没有 id：它永远是展开的（否则会话就彻底看不见了）。
          const expanded = id === undefined ? true : isExpanded(id, toggled, defaultId)
          return (
            <li key={id ?? 'orphans'}>
              <WorkspaceFolder
                group={group}
                expanded={expanded}
                onToggle={() => {
                  if (id === undefined) return
                  setToggled((previous) => ({ ...previous, [id]: !expanded }))
                }}
                activeId={activeId}
                creating={actions.creating}
                searching={searching}
                onNewSession={() => {
                  if (id === undefined) return
                  actions.newSession(id)
                }}
                editing={actions.editing}
                name={actions.name}
                renamePending={actions.renaming}
                onSelectSession={onSelect}
                onStartRename={actions.startRename}
                onNameChange={actions.setName}
                onSubmitRename={actions.submitRename}
                onRequestDelete={actions.requestDelete}
              />
            </li>
          )
        })}
      </ul>

      <DeleteSessionDialog
        pendingDelete={actions.pendingDelete}
        alert={actions.alert}
        removing={actions.removing}
        onCancel={actions.cancelDelete}
        onConfirm={actions.confirmDelete}
      />
    </section>
  )
}
