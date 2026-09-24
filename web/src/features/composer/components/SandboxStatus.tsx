/**
 * 沙箱状态标记：**只做气泡**——常驻的是一枚图标，话说在悬停/聚焦时才出现的气泡里。
 *
 * 它要说的事实没变：**这次运行的物理边界是什么**（`沙箱：工作区（无出网）` /
 * `沙箱：已禁用` / `沙箱：不可用（原因）`）。变的是呈现方式：以前那格常驻文字
 * （`沙箱：工作区（无出网）`）在窄输入条里会被挤成三行，占掉输入条一整行的高度；
 * 现在常驻的只有一枚图标，形态与时间线上的复制、分叉按钮一致——**图标看得见，
 * 文字悬停才出现**。
 *
 * 为什么图标仍然常驻（而不是整格都收进权限选择器的气泡）：颜色是这台机器上
 * "边界还在不在"的第一眼信号，`full` 与"沙箱后端探测失败"两种状态不该只在
 * 用户主动去问的时候才知道。四档用**四种不同形状的图标**区分，不靠颜色单独承担
 * 语义（色觉障碍下也分得开）。
 *
 * 数据来源刻意分成两半：
 *
 * * `mode` 来自**这次运行的选择**（策略）；
 * * `sandbox` 来自服务端的**实测探测**（事实）。
 *
 * `mode=manual/auto` 而探测说后端不可用时，如实显示"不可用"并说明后果（受管命令
 * 逐个问人、auto 直接拒）——这正是"不静默降级"在界面上的落点：降级可以被看见。
 */
import { Shield, ShieldAlert, ShieldCheck, ShieldOff } from 'lucide-react'

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

/** 一档沙箱状态怎么画：事实文本、色调、图标、气泡里的那句话。 */
export interface SandboxStatusView {
  text: string
  tone: BadgeTone
  hint: string
  icon: typeof Shield
}

export function SandboxStatus({ mode, sandbox }: SandboxStatusProps) {
  const { t } = useTranslation()

  const { tone, hint, icon: Icon } = resolveSandboxLabel(mode, sandbox, t)

  return (
    // Tooltip 用 `asChild` 把 ref 交给子元素：`Badge` 是普通函数组件、不转 ref，
    // 直接包它会得到 "Function components cannot be given refs" 警告，所以中间垫一个
    // 真实 DOM 元素（span 不参与布局，只承接 ref / aria-describedby）。
    <Tooltip side="top" label={hint}>
      <span className="inline-flex">
        <Badge tone={tone} aria-label={t('permission.sandbox.label')}>
          <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
        </Badge>
      </span>
    </Tooltip>
  )
}

/**
 * 把「这次选的模式」与「服务端实测的后端」合成一格状态。
 *
 * 抽成导出函数是为了让**映射规则**能脱离浮层单独验（jsdom 里 Radix 的浮层要真事件
 * 才开，规则与渲染分开测更直接）。
 */
export function resolveSandboxLabel(
  mode: PermissionMode,
  sandbox: SandboxState | null,
  t: (key: string, vars?: TranslateVars) => string,
): SandboxStatusView {
  if (mode === 'full') {
    // full 的沙箱状态由模式本身决定，不必等 meta：它就是要关掉。
    const text = t('permission.sandbox.disabled')
    return {
      text,
      tone: 'danger',
      icon: ShieldOff,
      hint: t('permission.sandbox.hint', { state: text }),
    }
  }
  if (sandbox === null) {
    const text = t('permission.sandbox.workspace')
    return {
      text,
      tone: 'neutral',
      icon: Shield,
      hint: t('permission.sandbox.hint', { state: text }),
    }
  }
  if (sandbox.available) {
    const text = t('permission.sandbox.workspace')
    return {
      text,
      tone: 'ok',
      icon: ShieldCheck,
      hint: t('permission.sandbox.hint', { state: text }),
    }
  }
  const text = t('permission.sandbox.unavailable')
  return {
    text,
    tone: 'warn',
    icon: ShieldAlert,
    hint: t('permission.sandbox.degraded', { reason: sandbox.reason ?? text }),
  }
}
