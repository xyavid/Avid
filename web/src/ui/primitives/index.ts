/*
 * ui 原语统一出口。
 *
 * 为什么只在这一处汇总：调用方（features/*）只 import '../../ui/primitives'，
 * 不直接摸到实现文件。这样"换了 Button 的内部实现"对调用方是不可见的，
 * 也让我们可以在一个文件里读出**设计系统的公开面到底有多大**——
 * 现在只有 6 个名字（cx + 5 个组件）。这个数字是设计系统的真实规模指标，
 * 每加一个出口都应当是一次有意识的决定。
 *
 * cx 从这里转出而不是让调用方 import '../../ui/cx'：
 * 调用方只需要知道"我有哪几件工具"，不需要知道 cx 住在哪个文件里。
 */

export { cx } from '../cx'
export { Button } from './Button'
export type { ButtonProps } from './Button'
export { Badge } from './Badge'
export type { BadgeProps } from './Badge'
export { Card } from './Card'
export type { CardProps } from './Card'
export { Dialog } from './Dialog'
export type { DialogProps } from './Dialog'
export { Tooltip } from './Tooltip'
export type { TooltipProps } from './Tooltip'
