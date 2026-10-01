/*
 * Tooltip：纯 CSS 的说明条（hover + focus-within，不做 JS 定位）。
 *
 * 为什么不做 JS 定位 / Portal：那需要测量、碰撞检测、滚动重算与一套浮层管理器，
 * 是这个 UI 里最贵的一块基础设施，而真实的需要只是"给图标按钮补一个名字"。
 * 代价是：靠近视口边缘时提示条可能被裁掉。这是一个**已知取舍**——
 * 长文案不该进 tooltip（进 aria-label 或就地说明），短标签被裁一半也仍可读。
 *
 * 为什么 focus-within 也要显示：键盘用户看不到 hover。提示条若只响应鼠标，
 * 就会变成"只有鼠标用户知道的说明"。
 *
 * 用命名 group（group/tip）而不是裸 group：Tooltip 常被塞进"整行 hover 才现形"的列表项，
 * 裸 group 会让鼠标划过行的任意位置都弹出提示条；命名 group 把作用域收回到自己的包裹层。
 *
 * 反色底（bg-ink + text-canvas）是这套视觉里唯一允许的高对比组合：
 * 提示条是临时浮层，需要立刻读清；它不参与"层级靠发丝线"那套规则。
 * 阴影用 shadow-2 而不是 scrim——scrim 只压暗背景，拿来做阴影在暗主题下会翻白（报告 §2.7）。
 *
 * 关于三层结构与 `animate-fade-up`（报告 §7.7 要求入场用 fade-up），这里有两个必须绕开的坑：
 *   1. `animate-*` 的关键帧以 `both` 填充，动画结束后的终态会**压过** opacity 类：
 *      把 fade-up 挂在常驻元素上，提示条会一次性播完然后永久可见。所以它只挂在
 *      `group-hover/…` / `group-focus-within/…` 变体下——动画只在"该显示"时存在。
 *   2. 关键帧里带 `translateY(4px) → 0`，而水平居中靠 `-translate-x-1/2`；
 *      同一个元素上动画中的 transform 会整体替换掉类里的 transform，居中会在入场瞬间跳掉。
 *      因此把动画放到**内层**（无静态 transform），居中留在外层定位层。
 */

import type { ReactElement } from 'react'
import { cx } from '../cx'

export interface TooltipProps {
  label: string
  children: ReactElement
  side?: 'top' | 'bottom'
}

export function Tooltip({ label, children, side = 'top' }: TooltipProps): JSX.Element {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      {/* 定位层：只负责"贴在哪"，不持有任何动画，避免与关键帧的 transform 互相覆盖。 */}
      <span
        className={cx(
          'pointer-events-none absolute left-1/2 z-tooltip -translate-x-1/2',
          side === 'top' ? 'bottom-full mb-a4' : 'top-full mt-a4',
        )}
      >
        <span
          role="tooltip"
          className={cx(
            'block whitespace-nowrap rounded-xs bg-ink px-a6 py-a4 text-hint text-canvas shadow-2',
            // 退场走 opacity 过渡（瞬档），入场走 fade-up：开慢关快的同一套节奏。
            'opacity-0 transition-opacity duration-instant ease-out',
            'group-hover/tip:animate-fade-up group-hover/tip:opacity-100',
            'group-focus-within/tip:animate-fade-up group-focus-within/tip:opacity-100',
          )}
        >
          {label}
        </span>
      </span>
    </span>
  )
}
