/**
 * 删除会话的确认弹窗。
 *
 * 文案必须说清两件事实，否则用户只能靠试：
 * 1) **不可撤销**——删的是磁盘上的会话文件，没有回收站；
 * 2) 服务端在会话有活动 run 时会拒绝（409 `session_busy`）——这不是界面能拦的，
 *    是运行注册表的约束，先说出来比让用户撞一次 409 更省事。
 *
 * busy 期间两个按钮都禁用：重复投递只会拿到服务端的二次拒绝，界面不该给出
 * 一份错的反馈。
 */

import type { ReactElement } from 'react'

import type { SessionSummary } from '../../../api/types'
import { Button, Dialog } from '../../../ui/primitives'
import { sessionLabel } from '../lib/navTree'

export interface DeleteSessionDialogProps {
  session: SessionSummary | null
  onCancel: () => void
  onConfirm: (id: string) => void
  busy?: boolean
}

export function DeleteSessionDialog({
  session,
  onCancel,
  onConfirm,
  busy = false,
}: DeleteSessionDialogProps): ReactElement {
  return (
    <Dialog
      open={session !== null}
      title="删除会话"
      description="会话文件会从磁盘移除，此操作不可撤销。"
      onClose={onCancel}
      width="sm"
      footer={
        <>
          <Button variant="secondary" type="button" disabled={busy} onClick={onCancel}>
            取消
          </Button>
          {/* 删除是破坏性动作，用 danger 而不是 primary：颜色不该把"危险"说成"主路径" */}
          <Button
            variant="danger"
            type="button"
            loading={busy}
            onClick={() => {
              if (session) onConfirm(session.id)
            }}
          >
            删除
          </Button>
        </>
      }
    >
      <p className="text-ui text-ink-light">
        {session ? `将删除「${sessionLabel(session)}」及其全部条目。` : null}
      </p>
      <p className="mt-a8 text-hint text-ink-muted">
        该会话存在正在运行的 run 时，服务端会拒绝删除（409 session_busy）；请先停止运行再重试。
      </p>
    </Dialog>
  )
}
