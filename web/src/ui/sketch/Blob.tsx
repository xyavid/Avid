import type { ReactNode } from 'react'

export interface BlobProps {
  children?: ReactNode
  /** 一级入口用；一层装饰轮廓即够，不做逐元素手绘路径。 */
  className?: string
}

/**
 * 手绘轮廓：内联 SVG + `vector-effect: non-scaling-stroke`（几百字节、零依赖）。
 * 只给一级入口（≤6 个）用——`filter: drop-shadow` 比 box-shadow 贵，列表内禁用。
 * 装饰层对读屏器是噪音，所以 `aria-hidden`。
 */
export function Blob({ children, className }: BlobProps) {
  return (
    <span className={`relative inline-flex items-center justify-center ${className ?? ''}`}>
      <svg
        aria-hidden="true"
        viewBox="0 0 200 120"
        preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full"
      >
        <path
          d="M12 26 C40 6 150 4 186 22 C198 46 194 92 178 104 C120 118 34 116 16 98 C4 76 4 44 12 26 Z"
          fill="none"
          stroke="rgb(var(--avid-ink-rgb))"
          strokeWidth={4.5}
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <span className="relative px-6 py-3">{children}</span>
    </span>
  )
}
