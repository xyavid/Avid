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
}

/**
 * 新建会话前的工作区选择器：多工作区模式下建会话必须说清归属，这是那个选择的落点。
 *
 * 放在 `features/sessions/` 内部而不是独立 feature：它只服务这一个动作，独立出去会
 * 变成「sessions import 另一个 feature」，layers 门禁当场红（`web/README.md` 规则 2）。
 *
 * 列表为空时给**可执行**的提示：这里不提供登记入口（登记是注册表的写入口，走
 * `avid workspace add <路径>`），但必须让人知道下一步敲什么，而不是点了新建才报错。
 */
export function WorkspaceSelector({
  workspaces,
  value,
  onChange,
  loading = false,
  failed = false,
  onRetry,
  disabled = false,
}: WorkspaceSelectorProps) {
  const { t } = useTranslation()

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
    return <p className="empty-note">{t('sessions.workspace.none')}</p>
  }

  return (
    <Field label={t('sessions.workspace.label')} hint={t('sessions.workspace.hint')}>
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
    </Field>
  )
}
