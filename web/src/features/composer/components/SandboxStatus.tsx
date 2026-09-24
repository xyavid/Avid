import { Badge, Tooltip } from '../../../ui/primitives'
import type { BadgeTone } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { TranslateVars } from '../../../lib/i18n'
import type { PermissionMode, SandboxState } from '../../../api/types'

export interface SandboxStatusProps {
  /** 这次运行会用的模式（route 回落过）。 */
  mode: PermissionMode
  /** 服务端上报的沙箱后端探测结果；meta 还没回来时为 null。 */
  sandbox: SandboxState | null
}

/**
 * 常驻的沙箱状态标记：**这次运行的物理边界是什么**。
 *
 * 为什么必须常驻可见（产品规则"full ≠ default，full 必须是明确、可见、可审计的显式授权"）：
 * 关掉沙箱这件事如果只体现在选择器的一个选项名上，用户很容易以为"那只是个更宽松的权限档"。
 * 一个独立的格子把它说出来——「沙箱：工作区」/「沙箱：已禁用」/「沙箱：不可用」。
 *
 * 数据来源刻意分成两半：
 *
 * * `mode` 来自**这次运行的选择**（策略）；
 * * `sandbox` 来自服务端的**实测探测**（事实）。
 *
 * `mode=manual/auto` 而探测说后端不可用时，如实显示"不可用"并说明后果（受管命令
 * 逐个问人、auto 直接拒）——这正是"不静默降级"在界面上的落点：降级可以被看见。
 */
export function SandboxStatus({ mode, sandbox }: SandboxStatusProps) {
  const { t } = useTranslation()

  const { text, tone, hint } = resolveSandboxLabel(mode, sandbox, t)

  return (
    // Tooltip 用 `asChild` 把 ref 交给子元素：`Badge` 是普通函数组件、不转 ref，
    // 直接包它会得到 "Function components cannot be given refs" 警告，所以中间垫一个
    // 真实 DOM 元素（span 不参与布局，只承接 ref / aria-describedby）。
    <Tooltip side="top" label={hint}>
      <span className="inline-flex">
        <Badge tone={tone} aria-label={t('permission.sandbox.label')}>
          {t('permission.sandbox.label')}：{text}
        </Badge>
      </span>
    </Tooltip>
  )
}

function resolveSandboxLabel(
  mode: PermissionMode,
  sandbox: SandboxState | null,
  t: (key: string, vars?: TranslateVars) => string,
): { text: string; tone: BadgeTone; hint: string } {
  if (mode === 'full') {
    // full 的沙箱状态由模式本身决定，不必等 meta：它就是要关掉。
    const text = t('permission.sandbox.disabled')
    return { text, tone: 'danger', hint: t('permission.sandbox.hint', { state: text }) }
  }
  if (sandbox === null) {
    const text = t('permission.sandbox.workspace')
    return { text, tone: 'neutral', hint: t('permission.sandbox.hint', { state: text }) }
  }
  if (sandbox.available) {
    const text = t('permission.sandbox.workspace')
    return { text, tone: 'ok', hint: t('permission.sandbox.hint', { state: text }) }
  }
  const text = t('permission.sandbox.unavailable')
  return {
    text,
    tone: 'warn',
    hint: t('permission.sandbox.degraded', { reason: sandbox.reason ?? text }),
  }
}
