/**
 * 时间线：对话主区的列表容器。
 *
 * 分块与索引都在组件内部算（调用方只把原始数据递进来），因为"怎么合并"与"怎么取回工具运行"
 * 是这一层自己的事；调用方不必知道 TimelineBlock 的存在就能用。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'

import type { TimelineEntry, ToolRun } from '../../../events/reducer'
import { Button } from '../../../ui/primitives'
import { ChevronDownIcon } from '../../../ui/icons'
import { blockToolCallId, groupTimeline } from '../lib/groupTimeline'
import type { TimelineBlock } from '../lib/groupTimeline'
import { MessageBubble } from './MessageBubble'
import { NoticeRow } from './NoticeRow'
import { ToolCard } from './ToolCard'

/**
 * 「已经在底部」的判定容差（px）。
 *
 * 为什么用 40 而不是 0：`clientHeight` 是取整后的整数，`scrollHeight` 在缩放与非整数行高下
 * 常带小数，再加上滚动惯性停下来的最后一两个像素，视觉上明明贴着底，
 * `scrollHeight - scrollTop - clientHeight` 仍可能差 1~3px。用 0 会让"跟随自动滚底"
 * 在流式过程中反复掉线，表现成列表一顿一顿地跳。
 *
 * 为什么不能再大：超过约一行半的高度，就会把"明显上滚、正在读旧消息"错判成还在底部，
 * 于是自动滚底把读者拽回末尾——那正是这段交互要避免的事。40 约等于一行正文的高度，
 * 是"贴底"与"在读旧内容"之间最小的一档。
 */
const STICK_TO_BOTTOM_PX = 40

export interface TimelineProps {
  /** 原始条目；组件内部调 groupTimeline（调用方不必先算） */
  entries: readonly TimelineEntry[]
  /** 工具运行表，按 toolCallId 取 ToolRun */
  tools: readonly ToolRun[]
  onInspect?: (toolCallId: string) => void
  streaming: boolean
  now?: number
}

/** 给"找不到 ToolRun"的工具块造一条提示条目：带上工具名与 id，便于排查。 */
function missingToolNotice(block: TimelineBlock): TimelineEntry {
  const first = block.entries[0]
  const tool = first?.tool ?? '未知工具'
  const id = first?.toolCallId
  return {
    id: `missing:${block.key}`,
    kind: 'notice',
    ts: first?.ts ?? 0,
    text: id === undefined ? `${tool} 的调用记录缺失` : `${tool}（${id}）的调用记录不在本次重放窗口内`,
    notice: 'info',
  }
}

export function Timeline({
  entries,
  tools,
  onInspect,
  streaming,
  now,
}: TimelineProps): ReactElement {
  const blocks = useMemo(() => groupTimeline(entries), [entries])
  const toolIndex = useMemo(() => {
    const index = new Map<string, ToolRun>()
    for (const run of tools) index.set(run.toolCallId, run)
    return index
  }, [tools])

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [pinned, setPinned] = useState(true)

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX)
  }, [])

  // 刚打开会话时直接落在末尾：此时还没有"正在读的位置"可保护。
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    // 只有"流式中"且"用户还在底部"才跟随。用户一旦上滚，pinned 变 false，这里就不再动手。
    if (el === null || !streaming || !pinned) return
    // 直接写 scrollTop 而不是 smooth 滚动：流式每帧都在变高，平滑滚动会被不断打断重启，
    // 反而更抖；这里要的是"始终贴着底"这个结果。
    el.scrollTop = el.scrollHeight
  }, [blocks, streaming, pinned])

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el === null) return
    // 瞬时到位，不用平滑滚动：日志视图里"回到最新那条"是一次定位，不是一段动画；
    // 而且平滑滚动会与流式的高频增高互相打断，反而是这套交互里最抖的一种做法。
    el.scrollTop = el.scrollHeight
    setPinned(true)
  }, [])

  const renderBlock = (block: TimelineBlock): ReactElement => {
    if (block.kind === 'tool') {
      const toolCallId = blockToolCallId(block)
      const run = toolCallId === null ? undefined : toolIndex.get(toolCallId)
      if (run === undefined) {
        // 降级成一条提示，而不是不画：静默跳过会让时间线凭空缺一块，看起来像消息丢了。
        // 常见成因是重连只补齐了 tool 条目、没带回工具运行表。
        return <NoticeRow entry={missingToolNotice(block)} />
      }
      return <ToolCard toolRun={run} onInspect={onInspect} />
    }
    if (block.kind === 'notice') {
      return (
        <>
          {block.entries.map((entry) => (
            <NoticeRow key={entry.id} entry={entry} />
          ))}
        </>
      )
    }
    // user / assistant：一个块可能有多条（相邻合并），逐条各画一个气泡。
    return (
      <div className="flex flex-col gap-a8">
        {block.entries.map((entry) => (
          <MessageBubble key={entry.id} entry={entry} now={now} />
        ))}
      </div>
    )
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        role="log"
        // aria-live 只给这个容器：给每一行都加会让屏幕阅读器把每次重排念成新消息。
        aria-live="polite"
        onScroll={handleScroll}
        className="flex min-h-0 flex-1 flex-col gap-a12 overflow-y-auto px-a16 py-a16"
      >
        {blocks.length === 0 ? (
          <p className="text-hint text-ink-faint">还没有内容</p>
        ) : (
          blocks.map((block) => (
            <div key={block.key} className="animate-fade-up">
              {renderBlock(block)}
            </div>
          ))
        )}
      </div>
      {/* 离开底部就给出回去的入口：只按 streaming 显示的话，已结束的长会话反而没有这个入口。 */}
      {!pinned && blocks.length > 0 ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-a8 flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            icon={<ChevronDownIcon size={14} />}
            onClick={jumpToBottom}
            className="pointer-events-auto"
          >
            回到底部
          </Button>
        </div>
      ) : null}
    </div>
  )
}
