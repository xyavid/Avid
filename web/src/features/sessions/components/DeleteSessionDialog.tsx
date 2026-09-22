/**
 * 删除会话的确认框：销毁会话是**不可逆**的动作，所以走一次显式确认，且确认按钮在请求
 * 在飞时转为 loading（`removing`）——否则用户会连点两下，第二下必然报"会话不存在"。
 *
 * 弹窗自己的开合由 `pendingDelete` 决定：它既是"开着"，也是"删哪一个"（一个 state 顶两个，
 * 因为不存在"开着但不知道删谁"的状态）。
 */
import { Button, Dialog } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'

export interface DeleteSessionDialogProps {
  /** 待删会话的 id；null = 没开着。 */
  pendingDelete: string | null
  /** 上一次失败的说明，挂在弹窗里（关掉弹窗才清）。 */
  alert: string | null
  removing: boolean
  onCancel: () => void
  onConfirm: () => void
}

export function DeleteSessionDialog({
  pendingDelete,
  alert,
  removing,
  onCancel,
  onConfirm,
}: DeleteSessionDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog
      open={pendingDelete !== null}
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
      title={t('sessions.delete')}
      description={t('sessions.deleteConfirm', { id: pendingDelete ?? '' })}
      alert={alert ?? undefined}
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
