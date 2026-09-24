/**
 * 导航列的动作面：对会话与工作区能做的每一件**改动**，外加承载它们的提示与错误两条消息。
 *
 * 为什么从 `SessionList` 里提出来：那个组件里混着五条 mutation、四五份 pending 标志、
 * 两条消息、改名中状态与删除确认框，再加六条空态/错误态分支——真正的渲染反而成了少数。
 * 提出来之后组件只负责"长什么样"，这个 hook 只负责"做了什么、失败怎么讲"。
 *
 * 这里守着一条容易被写散的不变量：**每个动作开始时先清掉两条消息**。原先它在三处各写
 * 一遍（新建、新增工作区、以及新增工作区成功之后），漏改一处就会出现"上一次的提示还挂在
 * 这一次的结果旁边"。
 */
import { useState } from 'react'

import { ApiError } from '../../../api/client'
import {
  useAddWorkspace,
  useCreateSession,
  useDeleteSession,
  useDeleteWorkspace,
  useMeta,
  usePickFolder,
  useRenameSession,
} from '../../../api/queries'
import type { SessionSummary, WorkspaceSummary } from '../../../api/types'
import { useErrorText } from '../../../lib/errors'
import { useTranslation } from '../../../lib/i18n'

export interface SessionActionsOptions {
  /** 建会话成功后切过去。 */
  onSelectSession: (sessionId: string) => void
  /** 展开某个工作区文件夹（新建之后用户显然想看到结果）。 */
  onExpandWorkspace: (workspaceId: string) => void
}

export function useSessionActions({ onSelectSession, onExpandWorkspace }: SessionActionsOptions) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const meta = useMeta()
  const create = useCreateSession()
  const rename = useRenameSession()
  const remove = useDeleteSession()
  const pick = usePickFolder()
  const addWorkspace = useAddWorkspace()
  const removeWorkspace = useDeleteWorkspace()

  // 成功/提示类消息（错误走 alert）：用一句人话说明"刚刚发生了什么"。
  const [notice, setNotice] = useState<string | null>(null)
  const [alert, setAlert] = useState<string | null>(null)
  // 改名中：`editing` 是会话 id，`name` 是输入框里的当前值。
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  // 待移除的工作区（null = 确认框没开着）：存整个记录而不是 id，因为确认文案要说名字，
  // 而"开着但不知道删谁"这个状态不存在。
  const [pendingWorkspaceDelete, setPendingWorkspaceDelete] = useState<WorkspaceSummary | null>(
    null,
  )

  const failure = (error: unknown) =>
    setAlert(
      errorText(error instanceof ApiError ? error.code : undefined, (error as Error)?.message),
    )

  /** 每个动作的开头都走这里：上一次的提示不该留在这一次的结果旁边。 */
  const beginAction = () => {
    setAlert(null)
    setNotice(null)
  }

  /** 在某个工作区建会话：展开它（用户显然想看到结果）并选中新会话。 */
  const newSession = (workspaceId: string) => {
    beginAction()
    create.mutate(
      { name: null, workspace: workspaceId },
      {
        onSuccess: (session) => {
          onExpandWorkspace(workspaceId)
          onSelectSession(session.id)
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
  const addWorkspaceByPicker = () => {
    beginAction()
    pick.mutate(undefined, {
      onError: failure,
      onSuccess: (result) => {
        if (!result.path) return // 取消：不做任何变更
        addWorkspace.mutate(
          { path: result.path },
          {
            onSuccess: (workspace) => {
              onExpandWorkspace(workspace.id)
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
                onExpandWorkspace(existingId)
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

  const startRename = (session: SessionSummary) => {
    setEditing(session.id)
    setName(session.name ?? '')
  }

  /**
   * 移除工作区：**只摘索引**。服务端不删任何会话文件，所以成功后要重取会话列表——
   * 原来装在那个文件夹里的会话还在，只是归到了「未归属的会话」组（提示里说清这一点，
   * 否则用户会以为会话跟着没了）。
   */
  const confirmWorkspaceDelete = () => {
    if (!pendingWorkspaceDelete) return
    const target = pendingWorkspaceDelete
    removeWorkspace.mutate(target.id, {
      onSuccess: () => {
        setPendingWorkspaceDelete(null)
        setNotice(t('sessions.workspace.deleted', { name: target.name ?? target.root }))
      },
      onError: (error) => {
        failure(error)
        setPendingWorkspaceDelete(null)
      },
    })
  }

  const submitRename = (sessionId: string) =>
    rename.mutate({ id: sessionId, name }, { onSuccess: () => setEditing(null), onError: failure })

  const confirmDelete = () => {
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
  }

  // 老内核没有这个端点：能力表里没声明就不显示按钮，而不是点出一个 404。
  const canAddWorkspace = meta.data?.features.workspace_picker === 1
  const canDeleteWorkspace = meta.data?.features.workspace_delete === 1
  const cancelDelete = () => setPendingDelete(null)

  return {
    notice,
    alert,
    editing,
    name,
    pendingDelete,
    pendingWorkspaceDelete,
    setName,
    startRename,
    submitRename,
    requestDelete: setPendingDelete,
    cancelDelete,
    confirmDelete,
    requestWorkspaceDelete: setPendingWorkspaceDelete,
    cancelWorkspaceDelete: () => setPendingWorkspaceDelete(null),
    confirmWorkspaceDelete,
    newSession,
    addWorkspaceByPicker,
    canAddWorkspace,
    canDeleteWorkspace,
    creating: create.isPending,
    renaming: rename.isPending,
    removing: remove.isPending,
    deletingWorkspace: removeWorkspace.isPending,
    adding: pick.isPending || addWorkspace.isPending,
  }
}
