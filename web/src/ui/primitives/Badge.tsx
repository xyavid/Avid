/*
 * Badge：状态标注，不是按钮、不是标签页。
 *
 * 为什么底一律用"极低饱和的状态底 + 同色文字"（bg-ok-bg / text-ok）：
 * 全站只有一个彩色在说话（报告 §12.1）。状态色若用实色块，页面上会立刻出现
 * 第四、第五种"说话的颜色"，accent 的 5% 占比守不住。
 * 浅底 + 深字让状态在扫读时能被看见，又不抢正文的注意力。
 *
 * 形状用 `rounded-xs` 并带 0.5px 发丝边：Badge 是"贴在纸上的小印章"，
 * 与卡片（rounded-sm）拉开半档，视觉上不会和按钮混为一谈。
 */

import type { ReactNode } from 'react'
import { cx } from '../cx'

export interface BadgeProps {
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' | 'info'
  children: ReactNode
  className?: string
}

const TONE: Record<NonNullable<BadgeProps['tone']>, string> = {
  // neutral 不用状态色：它是"没有状态"，只靠凹陷底与三级墨色退到背景里。
  neutral: 'bg-inset text-ink-muted',
  accent: 'bg-accent-soft text-accent',
  ok: 'bg-ok-bg text-ok',
  warn: 'bg-warn-bg text-warn',
  danger: 'bg-danger-bg text-danger',
  info: 'bg-info-bg text-info',
}

/*
 * `border-hair` 在这里写两次不是笔误：因为 `borderWidth.hair` 与 `colors.hair` 同名不同轴，
 * Tailwind 会为同一个类名生成两条规则——第一条给 0.5px 线宽，第二条给 `--avid-border-rgb` 线色。
 * 连写两次把"线宽 + 线色"两个意图都显式摆出来，读代码的人不必去查这个巧合。
 *
 * 为什么 Badge 不按 tone 给彩色描边：
 *   一是语义上不需要——发丝线在这套视觉里是"结构"而非"语义"，状态由浅底 + 同色文字承担；
 *   二是就算加也不能叠 `border-<语义色>/<alpha>`：Tailwind 按类名字母序输出，
 *   `.border-hair` 的线色规则恒定排在这些颜色类之后，写了会被静默覆盖（不报错、也不生效）。
 *   真需要状态描边时用专设的 `border-state-danger` / `border-state-warn`——
 *   `state-*` 的字典序排在 `hair` 之后，覆盖方向正确（理由与实测见 tokens.css）。
 */
const BASE =
  'inline-flex max-w-full items-center gap-a4 whitespace-nowrap rounded-xs ' +
  'border-hair border-hair px-a6 py-a2 text-hint'

export function Badge({ tone = 'neutral', children, className }: BadgeProps): JSX.Element {
  return <span className={cx(BASE, TONE[tone], className)}>{children}</span>
}
