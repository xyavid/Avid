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
} from '../../../api/queries'
import { SessionItem } from './SessionItem'

export interface SessionListProps {
  activeId: string | null
  onSelect: (sessionId: string) => void
}

/** 导航列：会话列表 + 新建 + 改名 + 销毁。删除要求无活动 run（服务端 409）。 */
export function SessionList({ activeId, onSelect }: SessionListProps) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const list = useSessionList()
  const create = useCreateSession()
  const rename = useRenameSession()
  const remove = useDeleteSession()
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [alert, setAlert] = useState<string | null>(null)

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
          onClick={() =>
            create.mutate(
              { name: null },
              {
                onSuccess: (session) => {
                  setAlert(null)
                  onSelect(session.id)
                },
                onError: failure,
              },
            )
          }
        >
          {t('sessions.new')}
        </Button>
      </div>

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
