import { useState } from 'react'

import { Badge, Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { SessionSummary } from '../../../api/types'
import { visibleSessions, type WorkspaceGroup } from '../lib/navTree'
import { SessionItem } from './SessionItem'

export interface WorkspaceFolderProps {
  group: WorkspaceGroup
  expanded: boolean
  onToggle: () => void
  activeId: string | null
  /** 这个工作区里正在新建会话（只有它自己的按钮转圈）。 */
  creating: boolean
  onNewSession: () => void
  editing: string | null
  name: string
  renamePending: boolean
  onSelectSession: (sessionId: string) => void
  onStartRename: (session: SessionSummary) => void
  onNameChange: (value: string) => void
  onSubmitRename: (sessionId: string) => void
  onRequestDelete: (sessionId: string) => void
}

/**
 * 一个工作区 = 一个文件夹：标题行可折叠，展开后是它的会话。
 *
 * 服务端已经排好序（会话从新到旧），这里不重新排序；收起时只露前几个，其余走
 * 「展开其余 N 个会话」——这是每个文件夹自己的状态（`useState`），因为它是纯界面
 * 展开态，不是服务端事实。
 *
 * 可访问名：折叠按钮的名字 = 工作区名（+条数），"新建"按钮 = 在「<名字>」新建会话。
 * 两者必须能分辨——它们在同一行，功能完全不同。
 */
export function WorkspaceFolder({
  group,
  expanded,
  onToggle,
  activeId,
  creating,
  onNewSession,
  editing,
  name,
  renamePending,
  onSelectSession,
  onStartRename,
  onNameChange,
  onSubmitRename,
  onRequestDelete,
}: WorkspaceFolderProps) {
  const { t } = useTranslation()
  const [allShown, setAllShown] = useState(false)
  const sessions = group.sessions
  const { shown, hidden } = visibleSessions(sessions, allShown)
  const label = group.workspace?.name ?? group.workspace?.root ?? t('sessions.orphans')

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1">
        <Button
          size="sm"
          variant="secondary"
          className="flex-1 justify-start gap-1"
          aria-expanded={expanded}
          onClick={onToggle}
        >
          <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
          <span aria-hidden="true">📁</span>
          <span className="truncate font-sketch text-sm">{label}</span>
          <span className="ml-auto shrink-0 font-mono text-[10px] text-ink/70">
            {sessions.length}
          </span>
        </Button>
        <Button
          size="icon"
          variant="secondary"
          aria-label={t('sessions.newIn', { name: label })}
          title={t('sessions.newIn', { name: label })}
          loading={creating}
          onClick={onNewSession}
        >
          <span aria-hidden="true">＋</span>
        </Button>
      </div>

      {expanded ? (
        <div className="flex flex-col gap-1 pl-3">
          {sessions.length === 0 ? (
            <p className="empty-note">{t('sessions.emptyIn')}</p>
          ) : (
            <ul className="space-y-1">
              {shown.map((session) => (
                <li key={session.id}>
                  <SessionItem
                    session={session}
                    active={session.id === activeId}
                    editing={editing === session.id}
                    name={name}
                    busy={renamePending}
                    onSelect={() => onSelectSession(session.id)}
                    onStartRename={() => onStartRename(session)}
                    onNameChange={onNameChange}
                    onSubmitRename={() => onSubmitRename(session.id)}
                    onRequestDelete={() => onRequestDelete(session.id)}
                  />
                </li>
              ))}
            </ul>
          )}
          {hidden > 0 ? (
            <div className="flex items-center gap-2">
              <Button size="sm" variant="secondary" onClick={() => setAllShown(true)}>
                {t('sessions.showMore', { count: hidden })}
              </Button>
              <Badge tone="neutral" count={sessions.length}>
                {t('sessions.total', { count: sessions.length })}
              </Badge>
            </div>
          ) : null}
          {allShown && sessions.length > 0 ? (
            <Button size="sm" variant="secondary" onClick={() => setAllShown(false)}>
              {t('sessions.collapse')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
