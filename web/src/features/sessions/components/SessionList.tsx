import { useState } from 'react'

import { Button, Dialog } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useErrorText } from '../../../lib/errors'
import { ApiError } from '../../../api/client'
import {
  useCreateSession,
  useDeleteSession,
  useRenameSession,
  useSessionList,
  useWorkspaces,
} from '../../../api/queries'
import { pickWorkspace } from '../lib/workspaceChoice'
import { SessionItem } from './SessionItem'
import { WorkspaceSelector } from './WorkspaceSelector'

export interface SessionListProps {
  activeId: string | null
  onSelect: (sessionId: string) => void
}

/** 导航列：会话列表 + 新建 + 改名 + 销毁。删除要求无活动 run（服务端 409）。 */
export function SessionList({ activeId, onSelect }: SessionListProps) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const list = useSessionList()
  const workspaces = useWorkspaces()
  const create = useCreateSession()
  const rename = useRenameSession()
  const remove = useDeleteSession()
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [alert, setAlert] = useState<string | null>(null)
  // 用户选过的那个（null = 还没选，按服务端排序回落）。**不进界面域**：它是这次
  // 新建动作的选择，不是用户偏好——服务端状态不在本地留副本（uiStore 开头那条）。
  const [preferredWorkspace, setPreferredWorkspace] = useState<string | null>(null)

  const workspaceList = workspaces.data ?? []
  const chosen = pickWorkspace(workspaceList, preferredWorkspace)

  const failure = (error: unknown) =>
    setAlert(
      errorText(error instanceof ApiError ? error.code : undefined, (error as Error)?.message),
    )

  return (
    <section className="flex h-full flex-col gap-3 p-3" aria-label={t('sessions.title')}>
      <div className="flex items-center justify-between">
        <h2 className="font-sketch text-lg">{t('sessions.title')}</h2>
        <Button
          size="sm"
          variant="primary"
          loading={create.isPending}
          // 没有可选工作区时禁用：服务端一律要求显式指定归属（缺了是 400），
          // 让按钮点出一个已知会失败的请求，不如先说清为什么不能点。
          disabled={chosen === null || workspaces.isLoading}
          onClick={() => {
            if (!chosen) return
            create.mutate(
              { name: null, workspace: chosen.id },
              {
                onSuccess: (session) => {
                  setAlert(null)
                  onSelect(session.id)
                },
                onError: failure,
              },
            )
          }}
        >
          {t('sessions.new')}
        </Button>
      </div>

      <WorkspaceSelector
        workspaces={workspaceList}
        value={chosen?.id ?? null}
        onChange={setPreferredWorkspace}
        loading={workspaces.isLoading}
        failed={workspaces.isError}
        onRetry={() => void workspaces.refetch()}
        disabled={create.isPending}
      />

      {list.isLoading ? <p className="text-sm text-ink/70">{t('common.loading')}</p> : null}
      {list.isError ? (
        <div className="empty-note">
          <p>{t('errors.title')}</p>
          <Button size="sm" className="mt-2" onClick={() => void list.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      ) : null}
      {list.data && list.data.length === 0 ? (
        <p className="empty-note">{t('sessions.empty')}</p>
      ) : null}

      <ul className="scroll-area flex-1 space-y-2 pr-1">
        {(list.data ?? []).map((session) => (
          <li key={session.id}>
            <SessionItem
              session={session}
              active={session.id === activeId}
              editing={editing === session.id}
              name={name}
              busy={rename.isPending}
              onSelect={() => onSelect(session.id)}
              onStartRename={() => {
                setEditing(session.id)
                setName(session.name ?? '')
              }}
              onNameChange={setName}
              onSubmitRename={() =>
                rename.mutate(
                  { id: session.id, name },
                  { onSuccess: () => setEditing(null), onError: failure },
                )
              }
              onRequestDelete={() => setPendingDelete(session.id)}
            />
          </li>
        ))}
      </ul>

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null)
        }}
        title={t('sessions.delete')}
        description={t('sessions.deleteConfirm', { id: pendingDelete ?? '' })}
        alert={alert ?? undefined}
        footer={
          <>
            <Button onClick={() => setPendingDelete(null)}>{t('common.cancel')}</Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={() => {
                if (!pendingDelete) return
                remove.mutate(pendingDelete, {
                  onSuccess: () => {
                    setPendingDelete(null)
                    setAlert(null)
                  },
                  onError: (error) => {
                    failure(error)
                    setPendingDelete(null)
                  },
                })
              }}
            >
              {t('common.confirm')}
            </Button>
          </>
        }
      />
    </section>
  )
}
