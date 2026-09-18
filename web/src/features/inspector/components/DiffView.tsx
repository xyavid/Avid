import { diffFromEdit, looksLikeDiff, parseUnifiedDiff, summarizeDiff } from '../../../lib/diff'

export interface DiffViewProps {
  text: string
  arguments?: Record<string, unknown>
}

/** 红绿笔 diff：优先用工具参数里的 old_string/new_string 现算，其次解析已有 diff 文本。 */
export function DiffView({ text, arguments: args }: DiffViewProps) {
  const lines = args && (args.old_string !== undefined || args.new_string !== undefined)
    ? diffFromEdit(args)
    : looksLikeDiff(text)
      ? parseUnifiedDiff(text)
      : []
  const summary = summarizeDiff(lines)

  if (lines.length === 0) {
    return (
      <pre className="scroll-area term max-h-[45vh] overflow-x-auto whitespace-pre-wrap p-3">
        {text}
      </pre>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-2 font-mono text-xs">
        <span className="text-ok">+{summary.added}</span>
        <span className="text-danger">-{summary.removed}</span>
      </div>
      <pre className="scroll-area term max-h-[45vh] overflow-x-auto p-3">
        {lines.map((line, index) => (
          <span
            key={`${index}-${line.kind}`}
            className={
              line.kind === 'add'
                ? 'block bg-ok-bg/30 text-ok'
                : line.kind === 'del'
                  ? 'block bg-danger-bg/30 text-danger'
                  : line.kind === 'meta'
                    ? 'block text-info'
                    : 'block'
            }
          >
            {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : '  '}
            {line.text}
          </span>
        ))}
      </pre>
    </div>
  )
}
