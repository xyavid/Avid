/**
 * 工具调用卡片：折叠态是一枚 chip（只留「CALL + 名字 + 状态」，好让时间线里连续几十个
 * 调用被一眼扫过），展开态按工具把输出翻译成最合适的读法——bash 看颜色、改动看红绿笔、
 * 待办看勾、任务看字段、子代理看分段。所有读法都收敛成「带标记与色调的行」，由同一个
 * 渲染器输出，于是加一种工具只需要加一段纯函数。正文/代码/工具输出一律不倾斜。
 */
import { memo } from 'react'

import { clsx } from 'clsx'
import { useMemo, useState } from 'react'

import { renderAnsi, type AnsiTone } from '../../lib/ansi'
import { diffFromEdit, looksLikeDiff, parseUnifiedDiff, summarizeDiff } from '../../lib/diff'
import type { DiffLine, DiffLineKind } from '../../lib/diff'
import { useTranslation } from '../../lib/i18n'
import { sanitizeToolContent } from '../../lib/markdown'
import type { Density } from '../../lib/density'
import type { ToolRun, ToolStatus } from '../../lib/timeline'
import { Badge, Button } from '../../ui/primitives'

export interface ToolCallCardProps {
  /** 工具卡自己也能把「全文 / diff / 原始 JSON」送进检查器（§8.4 的消息动作行）。 */
  onInspect?: (run: ToolRun) => void
  run: ToolRun
  /** 工具输出原文；组件内部会 sanitize，调用方直接透传即可。 */
  content?: string
  density?: Density
  defaultExpanded?: boolean
}

const STATUS_TONE = {
  running: 'info', ok: 'ok', failed: 'danger', denied: 'warn', truncated: 'warn',
} as const satisfies Record<ToolStatus, 'info' | 'ok' | 'warn' | 'danger'>
const STATUS_KEY = {
  running: 'tools.status.running', ok: 'tools.status.ok', failed: 'tools.status.failed',
  denied: 'tools.status.denied', truncated: 'tools.status.truncated',
} as const satisfies Record<ToolStatus, string>
const TONE_CLASS: Record<AnsiTone, string> = {
  fg: '', red: 'text-danger', green: 'text-ok', yellow: 'text-warn', blue: 'text-info',
  purple: 'text-accent',
}
const TONE_ROW: Record<Tone, string> = {
  ok: 'bg-ok-bg/30 text-ok', danger: 'bg-danger-bg/30 text-danger', dim: 'opacity-60',
  head: 'font-sketch border-t-hair border-ink pt-1 opacity-80',
}
const SIGN: Record<DiffLineKind, string> = { add: '+', del: '-', ctx: ' ', meta: '' }
const DIFF_TONE: Record<DiffLineKind, Tone | undefined> = {
  add: 'ok', del: 'danger', ctx: undefined, meta: 'dim',
}
const TODO_MARK: Record<string, string> = { completed: '[x]', in_progress: '[~]' }
const TASK_TOOLS = ['create_task', 'update_task', 'claim_task', 'complete_task', 'get_task', 'can_start']
const TASK_FIELDS: Array<[string, string]> = [
  ['id', 'tools.task.id'], ['subject', 'tools.task.subject'], ['status', 'tools.task.status'],
  ['owner', 'tasks.owner'], ['blocked_by', 'tasks.blockedBy'],
]
const TASK_STATUS_KEY: Record<string, string> = {
  pending: 'tasks.status.pending', in_progress: 'tasks.status.in_progress',
  completed: 'tasks.status.completed',
}
const PART = /^===\s*(.+?)\s*===$/

type Tone = 'ok' | 'danger' | 'dim' | 'head'
interface Line {
  text: string; tone?: Tone; sign?: string; ansi?: boolean; labelKey?: string; textKey?: string
}

function AnsiText({ text }: { text: string }) {
  const spans = useMemo(() => renderAnsi(text), [text])
  return (
    <>
      {spans.map((span, index) => (
        <span key={index} className={clsx(span.bold && 'font-bold', span.tone && TONE_CLASS[span.tone])}>
          {span.text}
        </span>
      ))}
    </>
  )
}

/** 唯一输出渲染器：标记 + 色调 + 可选行首标签；高度只由密度决定。 */
function Lines({ lines, density, summary, truncated, head }: {
  lines: Line[]; density: Density; summary?: { added: number; removed: number }
  truncated?: boolean; head?: string
}) {
  const { t } = useTranslation()
  if (lines.length === 0) return <p className="text-xs text-ink/70">{t('tools.empty')}</p>
  const rows = lines.map((line, index) => (
    <li key={index} className={clsx('whitespace-pre-wrap break-anywhere', line.tone && TONE_ROW[line.tone])}>
      {line.sign ? <span className="select-none opacity-60">{line.sign}</span> : null}
      {line.labelKey ? <span className="font-sketch opacity-70">{t(line.labelKey)} </span> : null}
      {line.ansi ? <AnsiText text={line.text} /> : line.textKey ? t(line.textKey) : line.text}
      {truncated && index === lines.length - 1 ? <span className="text-warn">{`\n── ${t('tools.status.truncated')} ──`}</span> : null}
    </li>
  ))
  return (
    <div className="rounded-sketch-2 border-hair border-ink">
      {summary ? (
        <p className="flex gap-2 border-b-hair border-ink px-2 py-1 text-xs">
          <span className="text-ok">{t('tools.diff.added', { count: summary.added })}</span>
          <span className="text-danger">{t('tools.diff.removed', { count: summary.removed })}</span>
        </p>
      ) : null}
      {head ? <p className="px-2 pt-2 font-sketch text-xs">{head}</p> : null}
      <ol aria-label={summary ? t('tools.inspector.diff') : undefined}
        className={clsx('term scroll-area list-none overflow-x-auto rounded-sketch-2 p-3 text-xs',
          density === 'compact' ? 'max-h-48' : 'max-h-96')}>
        {rows}
      </ol>
    </div>
  )
}
function splitParts(text: string): Line[] {
  return text.split('\n').map((line) => {
    const match = PART.exec(line.trim())
    return match ? { text: match[1] ?? '', tone: 'head' as const } : { text: line }
  })
}

function taskLines(args: Record<string, unknown>): Line[] {
  const out: Line[] = []
  for (const [field, key] of TASK_FIELDS) {
    const value = args[field]
    const shown = typeof value === 'string' ? value
      : typeof value === 'number' || typeof value === 'boolean' ? String(value)
      : Array.isArray(value) ? value.join(', ') : ''
    if (!shown) continue
    const textKey = key === 'tools.task.status' ? TASK_STATUS_KEY[shown] : undefined
    out.push({ text: shown, labelKey: key, textKey })
  }
  return out
}

/** 工具 → 行。纯函数，不认识 JSX，于是每种工具的特殊读法都能单独想清楚。 */
function toolLines(run: ToolRun, text: string): Line[] {
  const rows = text.split('\n')
  if (run.tool === 'bash') return rows.map((line) => ({ text: line, ansi: true }))
  if (run.tool === 'glob') return rows.filter((line) => line.trim() !== '').map((path) => ({ text: path }))
  if (run.tool === 'todo_write') {
    const todos = Array.isArray(run.arguments.todos) ? run.arguments.todos : []
    return todos.map((item) => {
      const row = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
      const status = typeof row.status === 'string' ? row.status : ''
      const content = typeof row.content === 'string' ? row.content : ''
      return { text: content, sign: `${TODO_MARK[status] ?? '[ ]'} ` }
    })
  }
  if (run.tool === 'subagent') return splitParts(text)
  if (TASK_TOOLS.includes(run.tool)) return taskLines(run.arguments)
  return rows.map((line) => ({ text: line }))
}
function diffRows(lines: DiffLine[]): Line[] {
  return lines.map((line) => ({ text: line.text, sign: SIGN[line.kind], tone: DIFF_TONE[line.kind] }))
}
function Body({ run, text, density }: { run: ToolRun; text: string; density: Density }) {
  const { t } = useTranslation()
  const tool = run.tool
  if (tool === 'write_file' || tool === 'edit_file' || (tool === 'read_file' && looksLikeDiff(text))) {
    const lines = looksLikeDiff(text) ? parseUnifiedDiff(text) : diffFromEdit(run.arguments)
    return <Lines lines={diffRows(lines)} summary={summarizeDiff(lines)} density={density} />
  }
  const name = typeof run.arguments.name === 'string' && run.arguments.name ? run.arguments.name : ''
  const head = tool === 'load_skill' ? t('tools.skill.summary', { name: name || t('common.unknown'), chars: text.length }) : undefined
  const cut = run.truncated || run.status === 'truncated'
  return <Lines lines={toolLines(run, text)} head={head} truncated={tool === 'bash' && cut} density={density} />
}

export const ToolCallCard = memo(function ToolCallCard({
  run,
  content,
  density = 'comfy',
  defaultExpanded = false,
  onInspect,
}: ToolCallCardProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(defaultExpanded)
  const text = useMemo(() => sanitizeToolContent(content), [content])
  const failed = run.status === 'failed' || run.status === 'denied'
  const status = (
    <Badge tone={STATUS_TONE[run.status]} role={failed ? 'alert' : undefined} pulse={run.status === 'running'}>
      {t(STATUS_KEY[run.status])}
    </Badge>
  )

  if (!expanded) {
    return (
      <div className="flex w-fit max-w-[250px] items-center gap-2">
        <Button size="sm" variant="secondary" className="max-w-[250px] gap-2 px-4 py-2" aria-expanded={false} onClick={() => setExpanded(true)}>
          <span className="shrink-0 rounded-sketch-1 bg-mark/40 px-1 font-sketch text-xs">{t('tools.call')}</span>
          <span className="min-w-0 truncate font-mono text-xs">{run.tool}</span>
        </Button>
        {status}
      </div>
    )
  }
  return (
    <section className={clsx('sketch-card flex w-full flex-col gap-2 border-bold', density === 'compact' ? 'p-2' : 'p-4')}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-sketch-1 bg-mark/40 px-2 py-0.5 font-mono text-xs">{t('tools.call')} {run.tool}</span>
        {status}
        <span className="font-mono text-xs text-ink/70">{t('tools.duration', { ms: run.durationMs })}</span>
        <span className="font-mono text-xs text-ink/70">{t('tools.chars', { chars: run.contentChars })}</span>
        {onInspect ? (
          <Button size="sm" variant="secondary" className="ml-auto" onClick={() => onInspect(run)}>
            {t('common.inspect')}
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="secondary"
          aria-expanded
          className={onInspect ? '' : 'ml-auto'}
          onClick={() => setExpanded(false)}
        >
          {t('common.collapse')}
        </Button>
      </div>
      {run.status === 'denied' && run.reason ? <p role="alert" className="text-xs text-danger">{t('tools.deniedReason', { reason: run.reason })}</p> : null}
      <Body run={run} text={text} density={density} />
    </section>
  )
})
