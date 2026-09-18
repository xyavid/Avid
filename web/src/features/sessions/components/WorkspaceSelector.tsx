import { Button, Field, Select } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { WorkspaceSummary } from '../../../api/types'

export interface WorkspaceSelectorProps {
  /** 候选工作区，服务端已排序（默认那个在最前，其余按最近使用）。 */
  workspaces: WorkspaceSummary[]
  /** 当前选中的工作区 id；由调用方保证它是列表里真实存在的一项。 */
  value: string | null
  onChange: (workspaceId: string) => void
  loading?: boolean
  /** 列表没取回来：与「一个都没登记」是两回事，提示不能共用。 */
  failed?: boolean
  onRetry?: () => void
  disabled?: boolean
  /** 点「新增工作区…」：调用方负责弹选择器、登记、切过来（见 SessionList）。 */
  onAdd?: () => void
  /** 选择器或登记请求进行中。 */
  adding?: boolean
  /** 是否声明了 workspace_picker 能力；老内核没有这个端点，按钮不出现。 */
  canAdd?: boolean
}

/**
 * 新建会话前的工作区选择器：建会话必须说清归属，这是那个选择的落点。
 *
 * 放在 `features/sessions/` 内部而不是独立 feature：它只服务这一个动作，独立出去会
 * 变成「sessions import 另一个 feature」，layers 门禁当场红（`web/README.md` 规则 2）。
 *
 * **新增工作区**的入口就放在这里——选工作区与加工作区是同一个动作的两半。按钮只负责
 * 说"用户想加"，弹选择器 / 登记 / 切换都由调用方（持有查询的那个组件）做，所以这个
 * 组件仍然"有形状无状态"。
 *
 * 列表为空时也给这个入口：只回一句"去终端敲命令"会让人以为界面不支持加。
 */
export function WorkspaceSelector({
  workspaces,
  value,
  onChange,
  loading = false,
  failed = false,
  onRetry,
  disabled = false,
  onAdd,
  adding = false,
  canAdd = false,
}: WorkspaceSelectorProps) {
  const { t } = useTranslation()

  const addButton =
    canAdd && onAdd ? (
      <Button size="sm" loading={adding} disabled={disabled} onClick={onAdd}>
        {t('sessions.workspace.add')}
      </Button>
    ) : null

  if (loading) {
    return <p className="text-xs text-ink/70">{t('sessions.workspace.loading')}</p>
  }

  if (failed) {
    return (
      <div className="empty-note">
        <p>{t('sessions.workspace.failed')}</p>
        {onRetry ? (
          <Button size="sm" className="mt-2" onClick={onRetry}>
            {t('common.retry')}
          </Button>
        ) : null}
      </div>
    )
  }

  if (workspaces.length === 0) {
    return (
      <div className="empty-note">
        <p>{t('sessions.workspace.none')}</p>
        {addButton ? <div className="mt-2">{addButton}</div> : null}
      </div>
    )
  }

  return (
    <Field label={t('sessions.workspace.label')} hint={t('sessions.workspace.hint')}>
      <div className="flex items-end gap-2">
        <Select
          aria-label={t('sessions.workspace.label')}
          value={value ?? ''}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name ?? workspace.root}
            </option>
          ))}
        </Select>
        {addButton}
      </div>
    </Field>
  )
}
