import { useRef, useState } from 'react'

import { Button, Dialog, Input } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useErrorText } from '../../../lib/errors'
import { ApiError } from '../../../api/client'
import {
  useAddWorkspace,
  useCreateSession,
  useDeleteSession,
  useMeta,
  usePickFolder,
  useRenameSession,
  useSessionList,
  useWorkspaces,
} from '../../../api/queries'
import {
  defaultExpandedWorkspace,
  filterGroups,
  groupByWorkspace,
  isExpanded,
} from '../lib/navTree'
import type { SessionSummary } from '../../../api/types'
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
 * 分组的规则、可见条数、默认展开谁都在 `lib/navTree.ts`（纯函数，有单测）；这里只管
 * 状态与副作用：折叠覆盖表、新建/改名/删除、以及"新增工作区"那条宿主机选择器链路。
 * 后端接口一行没改：`GET /api/workspaces` 给文件夹，`GET /api/sessions` 给里面的会话。
 */
export function SessionList({ activeId, onSelect }: SessionListProps) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const list = useSessionList()
  const workspaces = useWorkspaces()
  const create = useCreateSession()
  const rename = useRenameSession()
  const remove = useDeleteSession()
  const meta = useMeta()
  const pick = usePickFolder()
  const addWorkspace = useAddWorkspace()
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [alert, setAlert] = useState<string | null>(null)
  // 成功/提示类消息（错误走 alert）：用一句人话说明"刚刚发生了什么"。
  const [notice, setNotice] = useState<string | null>(null)
  // 折叠覆盖表：用户点过的那些记在这里，没点过的按默认规则展开（数据是异步来的，
  // 默认值不能写进 state 初值）。它是纯界面展开态，不进 uiStore（服务端状态不留副本）。
  const [toggled, setToggled] = useState<Record<string, boolean>>({})
  // 搜索：只在客户端按会话名过滤（服务端没有全文检索，也不该为一个过滤条件加接口）。
  // 不持久化——"刷新后还留着一个把列表藏掉一半的过滤条件"是坑不是贴心。
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const searchInput = useRef<HTMLInputElement>(null)

  const allGroups = groupByWorkspace(workspaces.data ?? [], list.data ?? [])
  const searching = query.trim() !== ''
  const groups = filterGroups(allGroups, query, t('sessions.unnamed'))
  const defaultId = defaultExpandedWorkspace(allGroups, activeId)

  const failure = (error: unknown) =>
    setAlert(
      errorText(error instanceof ApiError ? error.code : undefined, (error as Error)?.message),
    )

  // 老内核没有这个端点：能力表里没声明就不显示按钮，而不是点出一个 404。
  const canAddWorkspace = meta.data?.features.workspace_picker === 1

  /** 在某个工作区建会话：展开它（用户显然想看到结果）并选中新会话。 */
  const handleNewSession = (workspaceId: string) => {
    setAlert(null)
    setNotice(null)
    create.mutate(
      { name: null, workspace: workspaceId },
      {
        onSuccess: (session) => {
          setToggled((previous) => ({ ...previous, [workspaceId]: true }))
          onSelect(session.id)
        },
        onError: failure,
      },
    )
  }

  /**
   * 新增工作区：弹**宿主机**的文件选择器 → 登记 → 展开它。
   *
   * 三条路径都要有明确结果，且都不该悄悄发生：
   *   · 取消（`path === null`）→ 什么都不做，也不报错（取消不是故障）；
   *   · 已在列表里（409 `workspace_exists`）→ 展开那个已有的，提示已经在了，**不重复添加**；
   *   · 成功 → 展开新的，提示已添加。路径不存在/没有可用后端等错误照常走 `failure`。
   */
  const handleAddWorkspace = () => {
    setAlert(null)
    setNotice(null)
    pick.mutate(undefined, {
      onError: failure,
      onSuccess: (result) => {
        if (!result.path) return // 取消：不做任何变更
        addWorkspace.mutate(
          { path: result.path },
          {
            onSuccess: (workspace) => {
              setToggled((previous) => ({ ...previous, [workspace.id]: true }))
              setNotice(
                t('sessions.workspace.added', {
                  name: workspace.name ?? workspace.root,
                }),
              )
            },
            onError: (error) => {
              const existingId =
                error instanceof ApiError && error.code === 'workspace_exists'
                  ? error.detail.id
                  : undefined
              if (typeof existingId === 'string') {
                setToggled((previous) => ({ ...previous, [existingId]: true }))
                setNotice(error.message)
                return
              }
              failure(error)
            },
          },
        )
      },
    })
  }

  return (
    <section
      className="flex h-full flex-col gap-3 p-3"
      aria-label={t('sessions.workspacesTitle')}
    >
      <div className="flex items-center justify-between">
        <h2 className="font-sketch text-lg">{t('sessions.workspacesTitle')}</h2>
        <Button
          size="icon"
          variant="secondary"
          aria-label={t('sessions.search.open')}
          title={t('sessions.search.open')}
          aria-expanded={searchOpen}
          onClick={() => {
            const next = !searchOpen
            setSearchOpen(next)
            if (next) {
              // 打开就聚焦：这一个动作的目的就是打字，让人再点一次输入框是多余的。
              window.setTimeout(() => searchInput.current?.focus(), 0)
            } else {
              setQuery('')
            }
          }}
        >
          <span aria-hidden="true">🔍</span>
        </Button>
        {canAddWorkspace ? (
          <Button
            size="icon"
            variant="secondary"
            aria-label={t('sessions.workspace.add')}
            title={t('sessions.workspace.add')}
            loading={pick.isPending || addWorkspace.isPending}
            onClick={handleAddWorkspace}
          >
            <span aria-hidden="true">＋</span>
          </Button>
        ) : null}
      </div>

      {searchOpen ? (
        <div className="flex items-center gap-1">
          <Input
            ref={searchInput}
            type="search"
            aria-label={t('sessions.search.label')}
            placeholder={t('sessions.search.placeholder')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setQuery('')
                setSearchOpen(false)
              }
            }}
          />
          <Button
            size="icon"
            variant="secondary"
            aria-label={t('sessions.search.clear')}
            title={t('sessions.search.clear')}
            onClick={() => {
              setQuery('')
              searchInput.current?.focus()
            }}
          >
            <span aria-hidden="true">×</span>
          </Button>
        </div>
      ) : null}

      {notice ? <p className="empty-note">{notice}</p> : null}
      {alert ? <p className="empty-note text-danger">{alert}</p> : null}

      {workspaces.isError ? (
        <div className="empty-note">
          <p>{t('sessions.workspace.failed')}</p>
          <Button size="sm" className="mt-2" onClick={() => void workspaces.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      ) : null}

      {workspaces.isLoading ? (
        <p className="text-sm text-ink/70">{t('common.loading')}</p>
      ) : null}

      {workspaces.data && workspaces.data.length === 0 ? (
        <p className="empty-note">{t('sessions.workspace.none')}</p>
      ) : null}

      {list.isError ? (
        <div className="empty-note">
          <p>{t('errors.title')}</p>
          <Button size="sm" className="mt-2" onClick={() => void list.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      ) : null}

      {searching && groups.length === 0 ? (
        <p className="empty-note">{t('sessions.search.empty', { query: query.trim() })}</p>
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
                creating={create.isPending}
                searching={searching}
                onNewSession={() => {
                  if (id === undefined) return
                  handleNewSession(id)
                }}
                editing={editing}
                name={name}
                renamePending={rename.isPending}
                onSelectSession={onSelect}
                onStartRename={(session: SessionSummary) => {
                  setEditing(session.id)
                  setName(session.name ?? '')
                }}
                onNameChange={setName}
                onSubmitRename={(sessionId: string) =>
                  rename.mutate(
                    { id: sessionId, name },
                    { onSuccess: () => setEditing(null), onError: failure },
                  )
                }
                onRequestDelete={setPendingDelete}
              />
            </li>
          )
        })}
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
