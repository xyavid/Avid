/**
 * 线性 SVG 图标（报告 §9 图标规范）：stroke=currentColor、stroke-width 1.5、
 * fill none，不用 emoji、不用实心 fill。按需增补：每个新图标都要有真实
 * 使用点，不做"可能用得上"的预置集。
 */

const PATHS = {
  check: <path d="M20 6L9 17l-5-5" />,
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 12 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {PATHS[name]}
    </svg>
  )
}
