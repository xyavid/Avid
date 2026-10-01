/*
 * Button：全站唯一按钮。
 *
 * 为什么把 `type` 从原生属性里摘掉再自己声明：
 * `<button>` 在表单内的默认 type 是 `submit`，一个"取消"按钮会顺手提交表单。
 * 这里把默认值钉成 `'button'`，并**从 Omit 的键里去掉 type**，
 * 让"忘记写 type"这件事在类型层面就不可能发生（只能显式选 button / submit）。
 *
 * 为什么没有 `size="lg"`：这套视觉里按钮是"控件"，不是"行动号召"。
 * 32px 已经到顶，再加一档只会鼓励把按钮做大来抢注意力，而层级靠字号与明度建立。
 *
 * 颜色全部来自 token 类名；hover 只改色 / 底 / 透明度，不动位移与缩放——
 * 按压反馈靠 `:focus-visible` 环与色变，不做"弹一下"（报告 §8.2 禁令）。
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { LoaderIcon } from '../icons'
import { cx } from '../cx'

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'icon'
  icon?: ReactNode
  loading?: boolean
  type?: 'button' | 'submit'
}

/*
 * 变体表：颜色决策集中在这里，改动按钮的视觉只需要读一张表。
 *
 * 关于 `border-hair border-hair` 写两次：`borderWidth.hair` 与 `colors.hair` 同名不同轴，
 * Tailwind 会为这一个类名生成**两条**规则（先线宽 0.5px，后线色 --avid-border-rgb）。
 * 连写两次是把两个意图都显式说出来，避免后人以为"少写一个就没颜色"而漏掉线宽。
 *
 * danger 的描边为什么用 `border-state-danger` 而不是叠一个半透明色：
 * Tailwind 把同轴的类**按类名字母序**输出（不是配置里的书写顺序），
 * `.border-hair` 的**线色**规则恒定排在各语义色类之后；
 * 例如 danger 的 40% 透明度版就排在它前面，同权重下被静默覆盖——
 * 把两者写在一起，实际得到的是普通发丝线色，红色既不生效也不报错。
 * `state-danger` 是为此专设的具名 token：字典序排在 `hair` 之后，
 * 覆盖方向天然正确，值本身就是"40% 朱红预混到纸面"的实色（见 tokens.css）。
 * 这里不用 `!`：强制优先级会把工具类变成拖尾的坑（调用方想改描边也得带 `!`），
 * 而"状态描边"本来就该是一个有名字的 token，而不是靠优先级硬压出来的效果。
 */
const VARIANT: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-accent text-card hover:bg-accent-hover',
  secondary: 'border-hair border-hair bg-card text-ink hover:bg-accent-soft',
  ghost: 'text-ink-light hover:bg-accent-soft hover:text-ink',
  // danger 的描边是低饱和的朱红实色：整条高饱和红边会把"删除"喊得比标题还响。
  danger: 'text-danger border-hair border-state-danger hover:bg-danger-bg',
}

/*
 * 尺寸走 `min-h-` + 令牌变量而非固定 h-：
 * 按钮常被放进 flex 行里，固定高度会被 `flex-shrink` 压扁；min-h 只兜底不锁死。
 * icon 档例外——它必须是正方形，用固定的 icon-btn 尺寸 + shrink-0，否则会变成长方形。
 */
const SIZE: Record<NonNullable<ButtonProps['size']>, string> = {
  sm: 'min-h-[--avid-icon-btn] px-a8 text-caption',
  md: 'min-h-[--avid-control-h] px-a12 text-ui',
  icon: 'h-[--avid-icon-btn] w-[--avid-icon-btn] shrink-0',
}

const BASE =
  'inline-flex items-center justify-center gap-a6 whitespace-nowrap rounded-sm ' +
  'transition-colors duration-fast ease-standard select-none ' +
  'disabled:opacity-45 disabled:cursor-not-allowed'

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  loading = false,
  type = 'button',
  disabled,
  className,
  children,
  ...rest
}: ButtonProps): JSX.Element {
  // loading 与 disabled 合成一个出口：调用方不必自己记得"加载中也得禁用"，
  // 否则就会出现"点了两次、跑了两遍"的真实事故。
  const isDisabled = disabled === true || loading

  return (
    <button
      {...rest}
      type={type}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className={cx(BASE, VARIANT[variant], SIZE[size], className)}
    >
      {loading ? <LoaderIcon className="animate-spin" /> : icon}
      {children}
    </button>
  )
}
