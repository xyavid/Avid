import { Field, Select } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { isPermissionMode, PERMISSION_MODES } from '../lib/permission'
import type { PermissionMode } from '../../../api/types'

export interface PermissionSelectorProps {
  /** 这次运行实际会用哪一档（已由 route 回落过，不是 null）。 */
  value: PermissionMode
  onChange: (mode: PermissionMode) => void
  disabled?: boolean
}

/**
 * 权限模式选择器：三档 + 当前档的一句话说明。
 *
 * 值由 L4 route 用 `useState` 持有（照 `branch` 的形态）：它只活在这一个会话视图里，
 * **不写 uiStore**——「上次选了 system，下次打开浏览器仍自动全放行」是安全默认值问题，
 * 不是偏好（`state/uiStore.ts` 开头那条：服务端状态不在本地留副本）。
 *
 * 三档的差别写在选项标签与下方 hint 里：只给「严格 / 工作区 / 系统级」三个词，用户
 * 没法从名字推出「越界要不要问」，而这三档的区别恰恰只在越界与常规动作上。
 */
export function PermissionSelector({
  value,
  onChange,
  disabled = false,
}: PermissionSelectorProps) {
  const { t } = useTranslation()

  return (
    <Field label={t('permission.label')} hint={t(`permission.hint.${value}`)}>
      <Select
        aria-label={t('permission.label')}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          if (isPermissionMode(event.target.value)) onChange(event.target.value)
        }}
      >
        {PERMISSION_MODES.map((mode) => (
          <option key={mode} value={mode}>
            {t(`permission.mode.${mode}`)}
          </option>
        ))}
      </Select>
    </Field>
  )
}
