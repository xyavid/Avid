import { Select, Tooltip } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { isPermissionMode, PERMISSION_MODES } from '../lib/permission'
import type { PermissionMode } from '../../../api/types'

/** 气泡的 id：Radix 会把它同时挂到气泡与触发元素的 `aria-describedby` 上（也是 e2e 的稳定锚点）。 */
const HINT_ID = 'permission-mode-hint'

export interface PermissionSelectorProps {
  /** 这次运行实际会用哪一档（已由 route 回落过，不是 null）。 */
  value: PermissionMode
  onChange: (mode: PermissionMode) => void
  disabled?: boolean
}

/**
 * 权限模式选择器：三档 + 悬停/聚焦时浮现在控件**外面**的说明气泡。
 *
 * 值由 L4 route 用 `useState` 持有（照 `branch` 的形态）：它只活在这一个会话视图里，
 * **不写 uiStore**——「上次选了 system，下次打开浏览器仍自动全放行」是安全默认值问题，
 * 不是偏好（`state/uiStore.ts` 开头那条：服务端状态不在本地留副本）。
 *
 * 呈现（阶段 24 改动）：说明从"控件方框**内部**原位的占位行"挪到了 `Tooltip` 气泡，
 * 方向朝上（控件在输入条最下一行，往上弹不会被底部裁掉）。
 *
 * 为什么改：原位那版要一直留着一行高度给说明占位（不占位就会在显形时把输入条撑高一格），
 * 于是"悬停才出现"的文字反而常驻吃掉了纵向空间，而它描述的东西本来就不属于控件方框；
 * 气泡是浮层，不进布局，未悬停时**内容根本不挂载**（不是 opacity 藏起来），
 * 所以既没有占位也没有额外的方框高度。可访问性不减：Radix 会在打开时把
 * `aria-describedby` 连到气泡上，键盘 Tab 聚焦同样读得到。
 *
 * 可访问名仍由 `aria-label` 提供（`getByLabel('权限模式')` 照样定位得到）。
 */
export function PermissionSelector({
  value,
  onChange,
  disabled = false,
}: PermissionSelectorProps) {
  const { t } = useTranslation()

  return (
    <Tooltip id={HINT_ID} side="top" label={t(`permission.hint.${value}`)}>
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
