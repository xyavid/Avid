import { useState } from 'react'

import { Button, Dialog, Select, Tooltip } from '../../../ui/primitives'
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
 * **不写 uiStore**——「上次选了 full，下次打开浏览器仍自动关沙箱」是安全默认值问题，
 * 不是偏好（`state/uiStore.ts` 开头那条：服务端状态不在本地留副本）。
 *
 * **`full` 走二次确认**（阶段 26）：选中它不是"换了个档"，而是把最后一道物理边界
 * 关掉，所以这里不直接 `onChange('full')`，而是先弹一次 Dialog，用户按下
 * 「我明白，关闭沙箱」才生效。关掉弹窗什么都不发生（选择器回到原值）。
 *
 * 为什么确认放在 UI 而不是只靠服务端 422：服务端的 `full_access_ack` 是**准入**条件
 * （少带就拒），它挡不住"误触第三项"这一层；两者是不同的一道。
 *
 * 呈现：说明用 `Tooltip` 气泡（不占布局高度，未悬停时内容不挂载）。
 */
export function PermissionSelector({
  value,
  onChange,
  disabled = false,
}: PermissionSelectorProps) {
  const { t } = useTranslation()
  const [confirming, setConfirming] = useState(false)

  return (
    <>
      <Tooltip id={HINT_ID} side="top" label={t(`permission.hint.${value}`)}>
        <Select
          aria-label={t('permission.label')}
          value={value}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value
            if (!isPermissionMode(next)) return
            if (next === 'full' && value !== 'full') {
              setConfirming(true)
              return
            }
            onChange(next)
          }}
        >
          {PERMISSION_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {t(`permission.mode.${mode}`)}
            </option>
          ))}
        </Select>
      </Tooltip>
      <Dialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('permission.full.title')}
        description={t('permission.full.body')}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setConfirming(false)
                onChange('full')
              }}
            >
              {t('permission.full.confirm')}
            </Button>
          </>
        }
      />
    </>
  )
}
