export interface TapeProps {
  /** 常驻可见的身份：会话 ID 或运行 ID。空值时空贴一条胶带。 */
  label: string
  value?: string | null
  className?: string
  emptyText: string
}

/**
 * 斜贴标签：让「我在看哪个 run / 会话」始终可见（多标签页与长会话里的实际需求）。
 * 值用等宽字体，标签本身是装饰性的 —— 整体 `aria-hidden` 但保留可读文本，
 * 所以用 `title` 而不是纯装饰。
 */
export function Tape({ label, value, className, emptyText }: TapeProps) {
  if (!value) {
    return (
      <span className={`tape-empty font-mono ${className ?? ''}`} title={`${label}: ${emptyText}`}>
        {emptyText}
      </span>
    )
  }
  return (
    <span className={`tape ${className ?? ''}`} title={`${label}: ${value}`}>
      <span className="font-sans opacity-70">{label}</span> <span>{value}</span>
    </span>
  )
}
