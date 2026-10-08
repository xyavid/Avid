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
 * 动作行（复制 / 分支）同理——一次回话只挂一行，见 `turnActions`。
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

/**
 * 回话动作面：一次「助手回话」（两条用户消息之间）只挂一行动作，落在它最后一段
 * 正文上——回话是一个整体，中间每段都挂一排按钮只会把时间线切碎。
 *
 * 复制的是整段回话的原文（各段正文按序拼接）；分支点是**末段**那条已落库的消息，
 * 还在流的那段没有 entry_id（分叉点必须是磁盘上真实存在的条目），此时不出现分支钮。
 */
function turnActions(items: TimelineItem[]): Map<number, { text: string; branchAt: string | null }> {
  const actions = new Map<number, { text: string; branchAt: string | null }>()
  let texts: string[] = []
  let last: number | null = null
  const flush = () => {
    if (last !== null) {
      const item = items[last]
      actions.set(last, {
        text: texts.join('\n\n'),
        branchAt: item?.kind === 'assistant' ? item.entryId : null,
      })
    }
    texts = []
    last = null
  }
  items.forEach((item, index) => {
    if (item.kind === 'user') {
      flush()
      return
    }
    if (item.kind === 'assistant') {
      texts.push(item.text)
      last = index
    }
  })
  flush()
  return actions
}

export function Timeline({ items, workspaceRoot = null, onBranch }: TimelineProps) {
  const nodes: ReactNode[] = []
  const actions = turnActions(items)
  let headPending = true

  items.forEach((item, index) => {
    const key = itemKey(item, index)
    // 连续的工具行贴紧：动作读起来是一组，逐行之间不需要段落级的间距。
    const tight = item.kind === 'tool' && items[index - 1]?.kind === 'tool'

    if (item.kind === 'user') {
      headPending = true
      nodes.push(
        <div key={key} className="group" data-item="user" data-entry={item.entryId ?? undefined}>
          <UserBubble>{item.text}</UserBubble>
          <MessageActions text={item.text} className="justify-end" />
        </div>,
      )
      return
    }

    if (item.kind === 'assistant') {
      const showHead = headPending
      headPending = false
      const turn = actions.get(index)
      nodes.push(
        <div key={key} className="group" data-item="assistant" data-entry={item.entryId ?? undefined}>
          <AssistantMessage streaming={item.streaming} showHead={showHead}>
            {item.text}
          </AssistantMessage>
          {turn && <MessageActions text={turn.text} onBranch={branchHandler(turn.branchAt, onBranch)} />}
        </div>,
      )
      return
    }

    if (item.kind === 'reasoning') {
      nodes.push(
        <div key={key} data-item="reasoning">
          <ReasoningBlock
            text={item.text}
            streaming={item.streaming}
            durationMs={item.endedAt - item.startedAt}
          />
        </div>,
      )
      return
    }

    const label = toolLabel(item.name, item.args, workspaceRoot)
    nodes.push(
      // data-* 是给端到端脚本读时间线用的：段落的种类与身份写在 DOM 上，
      // 脚本不必猜 class 名（改样式不会让验收脚本静默失效）。
      <div key={key} data-item="tool" data-call={item.callId} className={tight ? '-mt-a8' : undefined}>
        <ToolCard
          icon={label.icon}
          verb={label.verb}
          target={label.target}
          status={item.status}
          durationMs={item.durationMs}
          steps={item.steps}
          workspaceRoot={workspaceRoot}
        >
          <span className="line-clamp-6 block whitespace-pre-wrap">{item.result ?? item.args}</span>
        </ToolCard>
      </div>,
    )
  })

  return (
    <div className="flex flex-col gap-a16" data-testid="timeline">
      {nodes}
    </div>
  )
}

function branchHandler(entryId: string | null, onBranch?: (entryId: string) => void): (() => void) | undefined {
  if (entryId === null || onBranch === undefined) return undefined
  return () => onBranch(entryId)
}
