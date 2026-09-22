import { Select } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { isPermissionMode, PERMISSION_MODES } from '../lib/permission'
import type { PermissionMode } from '../../../api/types'

/** 说明行的 id：控件用 `aria-describedby` 连上它（也顺便给 e2e 一个稳定锚点）。 */
const HINT_ID = 'permission-mode-hint'

export interface PermissionSelectorProps {
  /** 这次运行实际会用哪一档（已由 route 回落过，不是 null）。 */
  value: PermissionMode
  onChange: (mode: PermissionMode) => void
  disabled?: boolean
}

/**
 * 权限模式选择器：三档 + 一行"悬停/聚焦才显形"的说明。
 *
 * 值由 L4 route 用 `useState` 持有（照 `branch` 的形态）：它只活在这一个会话视图里，
 * **不写 uiStore**——「上次选了 system，下次打开浏览器仍自动全放行」是安全默认值问题，
 * 不是偏好（`state/uiStore.ts` 开头那条：服务端状态不在本地留副本）。
 *
 * 呈现：没有上方标签（控件里就是当前档的名字），说明仍在控件**下方原位**，只是平时
 * `opacity-0`，悬停或聚焦（含键盘 Tab）时显形。
 *
 * 为什么不用 Tooltip 气泡：试过，它要精准停在控件上、还有 300ms 延迟，鼠标移到控件
 * 下方（说明原来的位置）什么都没有——"看不到效果"。规则放在 CSS 里（`group-hover` /
 * `group-focus-within`）没有延迟也没有 JS；占位一直留着，显形时不会把输入条撑高一格。
 * 说明文本始终在 DOM 里，读屏器因此也读得到（不是视觉上藏起来就丢掉信息）。
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
    <div className="group">
      <Select
        aria-label={t('permission.label')}
        // 说明行始终在 DOM 里，所以可以真的把它连到控件上：键盘/读屏聚焦时也读得到。
        aria-describedby={HINT_ID}
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
      <p
        id={HINT_ID}
        className="mt-1 text-xs text-ink-muted opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {t(`permission.hint.${value}`)}
      </p>
    </div>
  )
}
