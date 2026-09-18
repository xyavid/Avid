export interface UserMarkProps {
  /** 边长（px）。默认 20，与 `AvidMark` 同尺寸，所以同一行里两者基线一致。 */
  size?: number
  className?: string
}

/**
 * 用户消息的手绘小标记：一枚**静态**内联 SVG（歪头 + 一道肩弧）。
 *
 * 为什么不是复用 `AvidMark`：那枚标记是两条相交的角形笔画（Avid 的「A」），
 * 它的语义是「这份内容出自 Avid」。把它放到用户卡上，视觉上会被读成「用户说的话
 * 也是 Avid 说的」——角色标记的全部作用就是区分作者，复用会把这件事抹掉。
 *
 * 契约与 `AvidMark` 逐条对齐（同一套笔触语言，不是两套风格）：
 * `stroke-width 4.5` + `vector-effect: non-scaling-stroke` + `currentColor`
 * （父级给 `text-ink`，换主题跟着走）+ `tilt-mark` 的 −2deg（只给 ≤48px 的装饰元素）。
 * 形状是同一枚路径在列表里复用，没有逐元素生成、滤镜或位图，成本与 §8.9 一致。
 * 装饰不进无障碍树，所以整枚 `aria-hidden`，角色名由旁边的文字承担。
 */
export function UserMark({ size = 20, className }: UserMarkProps) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={`tilt-mark shrink-0 ${className ?? ''}`}
    >
      {/* 头：故意画得不闭合、不对称，避免成标准的正圆 */}
      <path
        d="M8.5 7.3C8.2 5 10.1 3.4 12.2 3.5c2.2 0.1 3.8 1.9 3.6 4-0.2 2.1-1.9 3.6-4 3.5-2.1-0.1-3.1-1.7-3.3-3.7"
        fill="none"
        stroke="currentColor"
        strokeWidth={4.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* 肩：一道上凸的弧，两端不等高，保持手绘的不整齐 */}
      <path
        d="M4.7 20.4c0.5-4 3.6-6.4 7.3-6.4s6.8 2.4 7.3 6.4"
        fill="none"
        stroke="currentColor"
        strokeWidth={4.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
