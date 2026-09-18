export interface AvidMarkProps {
  /** 边长（px）。默认 20，只用于消息卡片的角色标记。 */
  size?: number
  className?: string
}

/**
 * Avid 的手绘小标记：一枚**静态**内联 SVG（两条歪腿 + 一道歪横梁），
 * `stroke-width 4.5` + `vector-effect: non-scaling-stroke`，颜色取 `currentColor`
 * （父级给 `text-ink`，所以换主题跟着走）。
 *
 * 关于「手绘轮廓只给一级入口」：那条约束针对的是**入口级**装饰外壳（每个入口一条
 * 独立路径）。这里是同一枚路径在列表里复用，没有逐元素生成、没有滤镜、没有位图，
 * 成本与 §8.9 的「这套风格为什么便宜」一致；倾斜 −2deg 只作用在 ≤48px 的装饰元素上。
 * 装饰不进无障碍树，所以整枚 `aria-hidden`，角色名由旁边的文字承担。
 */
export function AvidMark({ size = 20, className }: AvidMarkProps) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={`tilt-mark shrink-0 ${className ?? ''}`}
    >
      <path
        d="M4.4 20.2C6.6 13.4 8.8 7.8 11.7 3.6c2.3 3.9 4.3 9.4 6.4 16.4"
        fill="none"
        stroke="currentColor"
        strokeWidth={4.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d="M7.6 14.4c2.7-1 5.9-0.9 8.6 0.2"
        fill="none"
        stroke="currentColor"
        strokeWidth={4.5}
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
