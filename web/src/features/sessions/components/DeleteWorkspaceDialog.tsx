/**
 * 移除工作区的确认框。
 *
 * 为什么这个动作也要确认一次：按钮在**文件夹标题行**上，与"折叠/展开"（同一行、
 * 同样是方框）只差一个图标的位置——误触的代价是把工作区从导航列里拿走。它不是
 * 破坏性的（会话数据一行不动），但用户会以为"整个工作区没了"，所以确认文案必须把
 * "只摘列表、会话归到未归属、可以再加回来"这三件事说全，而不是只问一句"确定？"。
 *
 * 弹窗的开合由 `pending` 决定：它既是"开着"，也是"删哪个"（一个 state 顶两个）。
 */
import { Button, Dialog } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { WorkspaceSummary } from '../../../api/types'

export interface DeleteWorkspaceDialogProps {
  /** 待移除的工作区；null = 没开着。 */
  pending: WorkspaceSummary | null
  removing: boolean
  onCancel: () => void
  onConfirm: () => void
}

export function DeleteWorkspaceDialog({
  pending,
  removing,
  onCancel,
  onConfirm,
}: DeleteWorkspaceDialogProps) {
  const { t } = useTranslation()
  const name = pending?.name ?? pending?.root ?? ''

  return (
    <Dialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
      title={t('sessions.workspace.deleteTitle')}
      description={t('sessions.workspace.deleteBody', { name })}
      footer={
        <>
          <Button onClick={onCancel}>{t('common.cancel')}</Button>
          <Button variant="danger" loading={removing} onClick={onConfirm}>
            {t('common.confirm')}
          </Button>
        </>
      }
    />
  )
}
