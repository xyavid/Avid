/**
 * 时间线：把有序段落渲染成对话列。
 *
 * 只吃 `TimelineItem[]`，不碰线格式：段落从哪来（会话回拉还是事件流）是
 * `state/timeline.ts` 的事，这里只管怎么画。一份列表服务两段人生——运行中逐段
 * 追加，收尾后原样留着——所以没有「活区块」这第二个渲染器。
 *
 * 轮内顺序就是事件到达的顺序：思考 → 正文 → 它触发的工具。相邻工具行贴紧
 * （连续动作读起来是一组），其余段落之间留呼吸。
 * 标识行只在本轮第一次出现正文时显示：同一轮后面几段是续写，再挂一次名字是噪音。
 */

import type { ReactNode } from 'react'

import type { TimelineItem } from '../../state/timeline'
import { itemKey } from '../../state/timeline'
import { AssistantMessage } from './AssistantMessage'
import { MessageActions } from './MessageActions'
import { ReasoningBlock } from './ReasoningBlock'
import { ToolCard } from './ToolCard'
import { toolLabel } from './toolLabel'
import { UserBubble } from './UserBubble'

export type TimelineProps = {
  items: TimelineItem[]
  /** 工作区根：工具行把路径显示成相对它；null = 原样显示绝对路径。 */
  workspaceRoot?: string | null
  /** 从这条消息分叉；只给已落库的助手消息（还在流里的那条没有 entry_id）。 */
  onBranch?: (entryId: string) => void
}

export function Timeline({ items, workspaceRoot = null, onBranch }: TimelineProps) {
  const nodes: ReactNode[] = []
  let headPending = true

  items.forEach((item, index) => {
    const key = itemKey(item, index)
    // 连续的工具行贴紧：动作读起来是一组，逐行之间不需要段落级的间距。
    const tight = item.kind === 'tool' && items[index - 1]?.kind === 'tool'

    if (item.kind === 'user') {
      headPending = true
      nodes.push(
        <div key={key} className="group">
          <UserBubble>{item.text}</UserBubble>
          <MessageActions text={item.text} className="justify-end" />
        </div>,
      )
      return
    }

    if (item.kind === 'assistant') {
      const showHead = headPending
      headPending = false
      nodes.push(
        <div key={key} className="group">
          <AssistantMessage streaming={item.streaming} showHead={showHead}>
            {item.text}
          </AssistantMessage>
          <MessageActions text={item.text} onBranch={branchHandler(item.entryId, onBranch)} />
        </div>,
      )
      return
    }

    if (item.kind === 'reasoning') {
      nodes.push(
        <ReasoningBlock
          key={key}
          text={item.text}
          streaming={item.streaming}
          durationMs={item.endedAt - item.startedAt}
        />,
      )
      return
    }

    const label = toolLabel(item.name, item.args, workspaceRoot)
    nodes.push(
      <ToolCard
        key={key}
        className={tight ? '-mt-a8' : undefined}
        icon={label.icon}
        verb={label.verb}
        target={label.target}
        status={item.status}
        durationMs={item.durationMs}
        steps={item.steps}
        workspaceRoot={workspaceRoot}
      >
        <span className="line-clamp-6 block whitespace-pre-wrap">{item.result ?? item.args}</span>
      </ToolCard>,
    )
  })

  return <div className="flex flex-col gap-a16">{nodes}</div>
}

function branchHandler(entryId: string | null, onBranch?: (entryId: string) => void): (() => void) | undefined {
  if (entryId === null || onBranch === undefined) return undefined
  return () => onBranch(entryId)
}
