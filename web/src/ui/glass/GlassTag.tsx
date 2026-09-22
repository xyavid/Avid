export interface GlassTagProps {
  /** 常驻可见的身份：会话 ID 或运行 ID。空值时空贴一条标签。 */
  label: string
  value?: string | null
  className?: string
  emptyText: string
}

/**
 * 身份标签：让「我在看哪个 run / 会话」始终可见（多标签页与长会话里的实际需求）。
 *
 * 取代旧的胶带（`.tape`）：**功能不是装饰**——旧语言里它是一张斜贴的半透明便签，
 * 新语言里它是一枚玻璃标签（无倾斜）。文本用 `title` 而不是纯装饰：值要能被读出来。
 */
export function GlassTag({ label, value, className, emptyText }: GlassTagProps) {
  if (!value) {
    return (
      <span
        className={`id-tag-empty font-mono text-[10px] px-2 py-0.5 ${className ?? ''}`}
        title={`${label}: ${emptyText}`}
      >
        {emptyText}
      </span>
    )
  }
  return (
    <span
      className={`id-tag font-mono text-[10px] px-2 py-0.5 ${className ?? ''}`}
      title={`${label}: ${value}`}
    >
      <span className="font-sans opacity-70">{label}</span> <span>{value}</span>
    </span>
  )
}
