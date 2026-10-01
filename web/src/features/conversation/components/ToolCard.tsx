/**
 * 一次工具调用的卡片：头部一行状态 + 工具名 + 参数摘要 + 耗时，展开体是结果原文。
 *
 * 折叠的默认值不是常量：**运行中默认展开、结束后默认折叠**。理由是两种时刻读者要的东西不同——
 * 正在跑的时候他在等结果，跑完之后时间线上留的是"做过什么"的索引。
 * 用户一旦自己点过，就尊重他的选择（`userChoice`），不再被状态变化抢走控制权。
 */
import { useState } from 'react'
import type { ReactElement } from 'react'

import type { ToolRun } from '../../../events/reducer'
import { Badge, Button, Card, cx } from '../../../ui/primitives'
import {
  CheckCircleIcon,
  ChevronRightIcon,
  LoaderIcon,
  ShieldIcon,
  XCircleIcon,
} from '../../../ui/icons'

export interface ToolCardProps {
  toolRun: ToolRun
  /** 默认是否展开；缺省时按状态推（running 展开、其余折叠） */
  defaultOpen?: boolean
  /** 打开检查器；只把 id 交出去，检查器自己按 id 取数据 */
  onInspect?: (toolCallId: string) => void
}

/** 四态各有自己的图标与语义标签；用 Record 保证新增状态时漏了会编译报错。 */
const STATUS_ICONS: Record<ToolRun['status'], ReactElement> = {
  running: <LoaderIcon size={16} className="shrink-0 animate-spin text-ink-muted" aria-label="运行中" />,
  ok: <CheckCircleIcon size={16} className="shrink-0 text-ok" aria-label="成功" />,
  error: <XCircleIcon size={16} className="shrink-0 text-danger" aria-label="失败" />,
  denied: <ShieldIcon size={16} className="shrink-0 text-warn" aria-label="已拒绝" />,
}

/** 摘要优先看这些键：都是"一眼认出这次调用干了什么"的字段。 */
const SUMMARY_KEYS = [
  'path',
  'file_path',
  'command',
  'pattern',
  'query',
  'url',
  'prompt',
  'skill',
  'name',
] as const

/** 参数摘要：先找可读的关键字段，找不到就退回紧凑 JSON；过长由外层 truncate 负责。 */
function summarizeArgs(args: Record<string, unknown>): string {
  for (const key of SUMMARY_KEYS) {
    const value = args[key]
    if (typeof value === 'string' && value.trim() !== '') return value
  }
  try {
    const text = JSON.stringify(args)
    return text === undefined || text === '{}' ? '' : text
  } catch {
    // 理论上不该有循环引用，但摘要失败不该把整张卡带崩。
    return ''
  }
}

/** 耗时：null（还没结束/没上报）显示「—」，不当 0——0ms 是"确实没花时间"。 */
function formatDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function ToolCard({ toolRun, defaultOpen, onInspect }: ToolCardProps): ReactElement {
  const [userChoice, setUserChoice] = useState<boolean | null>(null)
  const open = userChoice ?? defaultOpen ?? toolRun.status === 'running'

  const subagent = toolRun.subagent
  // 子 agent 卡上，读者要知道的是"这个子任务在干什么"，task 描述比任何参数都直接。
  const summary = subagent?.task ?? summarizeArgs(toolRun.args)

  return (
    <Card tone="paper" className="overflow-hidden">
      <div className="flex items-center gap-a8 py-a6 pl-a8 pr-a6">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setUserChoice(!open)}
          className="flex min-w-0 flex-1 items-center gap-a8 rounded-sm text-left transition-colors duration-fast ease-standard hover:bg-accent-soft"
        >
          <ChevronRightIcon
            size={14}
            className={cx(
              'shrink-0 text-ink-faint transition-transform duration-fast ease-standard',
              open && 'rotate-90',
            )}
          />
          {STATUS_ICONS[toolRun.status]}
          <span className="shrink-0 font-medium text-ui text-ink">{toolRun.tool}</span>
          {subagent ? <Badge tone="accent">子任务 #{subagent.index}</Badge> : null}
          <span className="min-w-0 flex-1 truncate text-hint text-ink-muted">{summary}</span>
          <span className="shrink-0 text-hint text-ink-faint tabular-nums">
            {formatDuration(toolRun.durationMs)}
          </span>
        </button>
        {onInspect ? (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`查看 ${toolRun.tool} 的调用详情`}
            onClick={() => onInspect(toolRun.toolCallId)}
          >
            查看
          </Button>
        ) : null}
      </div>
      {open ? (
        <div className="max-h-[320px] overflow-y-auto whitespace-pre-wrap rounded-sm bg-inset p-a8 text-caption text-ink-light">
          {toolRun.resultText !== '' ? toolRun.resultText : '（暂无输出）'}
        </div>
      ) : null}
    </Card>
  )
}
