/*
 * Card：抬升面。全站唯一的"框"。
 *
 * 为什么阴影只到 `shadow-1`（0 1px 2px / 6%）：层级的第一手段是发丝线 + 留白 + 明度差，
 * 阴影只作为"纸的厚度暗示"。自检标准是"去掉全部阴影后层级依然清楚"，
 * 所以任何依赖 shadow-2/3 才能和背景分开的卡片都算设计失败——那两档留给浮层与模态。
 *
 * 为什么开放 `as`：语义标签是内容的责任，不是样式的责任。一组卡片列表应该是
 * `<section>` 或 `<article>`，而不是被迫全是 div。样式与标签解耦后，
 * 无障碍结构就只剩调用方一个决定要做。
 */

import type { ElementType, HTMLAttributes } from 'react'
import { cx } from '../cx'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: 'paper' | 'inset'
  as?: 'div' | 'section' | 'article'
}

export function Card({
  tone = 'paper',
  as = 'div',
  className,
  children,
  ...rest
}: CardProps): JSX.Element {
  const Tag = as as ElementType
  return (
    <Tag
      {...rest}
      className={cx(
        // `border-hair` 两次 = 线宽 + 线色两个轴（同名 token 分属 borderWidth 与 colors 两张表）。
        'rounded-sm border-hair border-hair shadow-1',
        // paper 比纸面更亮一档（"浮起来的纸"），inset 更暗一档（"凹进去的槽"）。
        // 两个方向都不能再走远：再亮就接近纯白，再暗就变成另一种颜色了。
        tone === 'inset' ? 'bg-inset' : 'bg-card',
        className,
      )}
    >
      {children}
    </Tag>
  )
}
