/**
 * 管理工作区：一个入口，三种状态，**同一时刻只有一个 Dialog 打开**。
 *
 * 为什么用"状态机 + 单弹窗"而不是"管理弹窗里再套一个添加弹窗"：
 * `ui/primitives/Dialog` 明确把"同时只有一个模态"当作不实现焦点陷阱的前提
 * （见该文件顶部的取舍说明）。两个 Dialog 同时挂载时，Esc 挂在 document 上，
 * 一次按键会把两个都关掉——返回列表的那一步就被无声吃掉了。
 * 所以这里把 `list / add / confirm` 做成互斥的三态，任一时刻只渲染一个 Dialog。
 * 代价是"返回列表"要走一次状态切换而不是天然叠层；收益是键盘行为始终可预测。
 *
 * 为什么自己拉 `fetchMeta`：本组件的 props 契约只有 `{open, onClose}`，
 * 而"这台机器有没有文件夹选择器"是 `meta.capabilities.workspace_picker` 的事实。
 * 打开时顺带拉一次 meta（与工作区列表并发），好过让调用方多传一个 prop——
 * 契约一旦定下，扩展它比多一个并发请求贵（要改 Lead 的装配层）。
 *
 * 移除的文案必须说准服务端语义：**只从候选列表摘掉，不删会话数据**
 * （`routes/workspaces.py:32` 的 docstring）。说成"删除工作区"会让人以为磁盘上的
 * 会话没了，从而不敢用这个功能。
 */

import { useCallback, useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import { ApiError } from '../../../api/client'
import { fetchMeta, fetchWorkspaces } from '../../../api/sessions'
import type { WorkspaceSummary } from '../../../api/types'
import { deleteWorkspace } from '../../../api/workspaces'
import { Button, Dialog } from '../../../ui/primitives'
import { workspaceLabel } from '../lib/labels'
import { describeWorkspaceError } from '../lib/workspaceError'
import { AddWorkspaceDialog } from './AddWorkspaceDialog'
import { WorkspaceList } from './WorkspaceList'

export interface WorkspaceManagerProps {
  open: boolean
  onClose: () => void
}

export function WorkspaceManager({ open, onClose }: WorkspaceManagerProps): ReactElement {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [picker, setPicker] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** 三态里的两态：正在添加 / 正在确认移除。都为 false 时渲染列表。 */
  const [adding, setAdding] = useState(false)
  const [pending, setPending] = useState<WorkspaceSummary | null>(null)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const [items, meta] = await Promise.all([fetchWorkspaces(signal), fetchMeta(signal)])
      setWorkspaces(items)
      setPicker(meta.capabilities.workspace_picker)
    } catch (cause) {
      // 关掉弹窗会 abort 掉在飞的请求：那不是错误，静默退出即可。
      if (cause instanceof ApiError && cause.code === 'aborted') return
      setError(describeWorkspaceError(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    // 每次打开都回到列表态：上一次可能停在"添加"或"确认移除"上，直接复现会很怪。
    setAdding(false)
    setPending(null)
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [open, load])

  const handleCreated = async () => {
    setAdding(false)
    // 以服务端返回为准重新拉一次，而不是把返回值就地插进列表：
    // 服务端排序（进程绑定的那个在最前）与前端插入顺序不必一致。
    await load()
  }

  const handleConfirmRemove = async () => {
    if (pending === null) return
    setBusy(true)
    setError(null)
    try {
      await deleteWorkspace(pending.id)
      setPending(null)
      await load()
    } catch (cause) {
      // 竞态：进程绑定的那个工作区被别的路径改成 default，我们没来得及禁用按钮。
      setError(describeWorkspaceError(cause))
    } finally {
      setBusy(false)
    }
  }

  if (adding) {
    return (
      <AddWorkspaceDialog
        open={open}
        picker={picker}
        onClose={() => setAdding(false)}
        onCreated={() => {
          void handleCreated()
        }}
      />
    )
  }

  if (pending !== null) {
    return (
      <Dialog
        open={open}
        width="sm"
        title="移除工作区"
        description="只从候选列表摘掉，不删会话数据。"
        onClose={() => {
          if (!busy) setPending(null)
        }}
        footer={
          <>
            <Button
              variant="secondary"
              type="button"
              disabled={busy}
              onClick={() => setPending(null)}
            >
              取消
            </Button>
            <Button
              variant="danger"
              type="button"
              loading={busy}
              onClick={() => {
                void handleConfirmRemove()
              }}
            >
              移除
            </Button>
          </>
        }
      >
        <p className="text-ui text-ink-light">
          将「{workspaceLabel(pending)}」从候选列表里移除。
        </p>
        <p className="mt-a8 text-hint text-ink-muted">
          它下面已经登记的会话与磁盘上的数据都不动，只是它不再出现在新建会话的工作目录候选里；
          之后想再用，重新添加即可。
        </p>
        {error === null ? null : (
          <p role="alert" className="mt-a8 rounded-sm bg-danger-bg px-a8 py-a6 text-hint text-danger">
            {error}
          </p>
        )}
      </Dialog>
    )
  }

  return (
    <Dialog
      open={open}
      width="md"
      title="管理工作区"
      description="登记过的文件夹会出现在新建会话的工作目录候选里。移除只影响这份候选列表。"
      onClose={onClose}
      footer={
        <Button variant="secondary" type="button" onClick={onClose}>
          关闭
        </Button>
      }
    >
      {error === null ? null : (
        <p role="alert" className="mb-a8 rounded-sm bg-danger-bg px-a8 py-a6 text-hint text-danger">
          {error}
        </p>
      )}
      {loading ? (
        <p className="text-ui text-ink-muted">正在读取工作区列表…</p>
      ) : (
        <WorkspaceList
          workspaces={workspaces}
          busy={busy}
          onAdd={() => setAdding(true)}
          onRemove={(id) => {
            // 只切状态：确认弹窗会接管 fired 的那一项。这里不调接口，
            // 所以"点了移除但没确认"不会产生任何副作用。
            const target = workspaces.find((item) => item.id === id) ?? null
            setError(null)
            setPending(target)
          }}
        />
      )}
    </Dialog>
  )
}
