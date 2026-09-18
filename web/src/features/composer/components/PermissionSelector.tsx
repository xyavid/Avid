import { Select, Tooltip } from '../../../ui/primitives'
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
 * 权限模式选择器：三档，**说明挂在悬停提示上**（当前档那一句）。
 *
 * 值由 L4 route 用 `useState` 持有（照 `branch` 的形态）：它只活在这一个会话视图里，
 * **不写 uiStore**——「上次选了 system，下次打开浏览器仍自动全放行」是安全默认值问题，
 * 不是偏好（`state/uiStore.ts` 开头那条：服务端状态不在本地留副本）。
 *
 * 呈现上只留控件本身：上面那行「权限模式」标签与下面那行常显说明都去掉了——控件里
 * 已经写着当前档的名字（严格 / 工作区 / 系统级），而三档的差别恰恰只在越界与常规动作上，
 * 所以把说明放进悬停提示（内容跟着**当前档**走），既不占版面也随时查得到。
 * 可访问名仍由 `aria-label` 提供（`getByLabel('权限模式')` 照样定位得到）。
 */
export function PermissionSelector({
  value,
  onChange,
  disabled = false,
}: PermissionSelectorProps) {
  const { t } = useTranslation()

  return (
    <Tooltip label={t(`permission.hint.${value}`)}>
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
    </Tooltip>
  )
}
