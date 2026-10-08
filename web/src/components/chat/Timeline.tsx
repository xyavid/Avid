/**
 * 时间线：把有序段落渲染成对话列。
 *
 * 只吃 `TimelineItem[]`，不碰线格式：段落从哪来（会话回拉还是事件流）是
 * `state/timeline.ts` 的事，这里只管怎么画。一份列表服务两段人生——运行中逐段
 * 追加，收尾后按轮折叠——所以没有「活区块」这第二个渲染器。
 *
 * 轮内顺序就是事件到达的顺序：思考 → 正文 → 它触发的工具。相邻工具行贴紧
 * （连续动作读起来是一组），其余段落之间留呼吸。
 * 标识行只在本轮第一次出现的正文上显示：同一轮后面几段是续写，再挂一次名字是噪音。
 * 动作行（复制 / 分支）同理——一次回话只挂一行，见 `turnActions`。
 *
 * 折叠：一轮跑完（末段是已落库的正文）就把**过程**收进一行
 * （`TurnSummary`：「已完成，用时 …」，点开还原），只留那条大幅消息。判定在
 * `state/timeline.ts` 的 `turnGroups`，这里只按它给的形状画；`liveTail` 是唯一的例外
 * ——本轮还在跑就铺着（过程要逐段看得见），跑完才折。
 */

import { useState } from 'react'
import type { ReactNode } from 'react'

import type { TimelineItem } from '../../state/timeline'
import { itemKey, subagentSteps, turnGroups } from '../../state/timeline'
import { AssistantMessage } from './AssistantMessage'
import { MessageActions } from './MessageActions'
import { ReasoningBlock } from './ReasoningBlock'
import { ToolCard } from './ToolCard'
import { TurnSummary } from './TurnSummary'
import { toolDetail } from './toolDetail'
import { toolLabel } from './toolLabel'
import { UserBubble } from './UserBubble'

export type TimelineProps = {
  items: TimelineItem[]
  /** 容器上的 data-testid：对话列是 `timeline`，子智能体面板里换一个——验收脚本按它区分两条时间线。 */
  testId?: string
  /** 工作区根：工具行把路径显示成相对它；null = 原样显示绝对路径。 */
  workspaceRoot?: string | null
  /** 从这条消息分叉；只给已落库的助手消息（还在流里的那条没有 entry_id）。 */
  onBranch?: (entryId: string) => void
  /** 最后一轮还在跑：这一轮不折（过程中的样子要逐段看得见）。 */
  liveTail?: boolean
  /** 点子智能体卡时通知调用方（打开右列的「子智能体」面板）。 */
  onOpenSubagents?: () => void
}

/**
 * 回话动作面：一次「助手回话」（两条用户消息之间）只挂一行动作，落在它最后一段
 * 正文上——回话是一个整体，中间每段都挂一排按钮只会把时间线切碎。
 *
 * 复制的是整段回话的原文（各段正文按序拼接，含被折叠的过程段）；分支点是**末段**
 * 那条已落库的消息，还在流的那段没有 entry_id（分叉点必须是磁盘上真实存在的条目），
 * 此时不出现分支钮。
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

type TurnAction = { text: string; branchAt: string | null }

type Ctx = {
  workspaceRoot: string | null
  onBranch?: (entryId: string) => void
  onOpenSubagents?: () => void
  /** 本轮第一段正文吃标识行；由调用方按"这一轮画过正文没有"消费。 */
  head: { pending: boolean }
  /** 整轮的动作面（复制 / 分支），按段落在组内的下标索引进来的。 */
  actions: Map<number, TurnAction>
}

/** 段落 → 节点。`list` 是折叠判定后要画的那一段（可能是整轮，也可能只有过程），
 *  `offset` 是它在组内的起点（key 与动作面都按组内下标对齐）。 */
function itemNodes(list: TimelineItem[], offset: number, ctx: Ctx): ReactNode[] {
  return list.map((item, index) => {
    const at = offset + index
    const key = itemKey(item, at)
    // 连续的工具行贴紧：动作读起来是一组，逐行之间不需要段落级的间距。
    const tight = item.kind === 'tool' && list[index - 1]?.kind === 'tool'

    if (item.kind === 'user') {
      ctx.head.pending = true
      return (
        <div key={key} className="group" data-item="user" data-entry={item.entryId ?? undefined}>
          <UserBubble>{item.text}</UserBubble>
          <MessageActions text={item.text} className="justify-end" />
        </div>
      )
    }

    if (item.kind === 'assistant') {
      const showHead = ctx.head.pending
      ctx.head.pending = false
      const turn = ctx.actions.get(at)
      return (
        <div key={key} className="group" data-item="assistant" data-entry={item.entryId ?? undefined}>
          <AssistantMessage streaming={item.streaming} showHead={showHead}>
            {item.text}
          </AssistantMessage>
          {turn && <MessageActions text={turn.text} onBranch={branchHandler(turn.branchAt, ctx.onBranch)} />}
        </div>
      )
    }

    if (item.kind === 'error') {
      // 失败记账：一段带危险色的窄条，不进气泡、不挂动作行（它不是"谁说的话"）
      return (
        <div
          key={key}
          data-item="error"
          data-entry={item.entryId ?? undefined}
          className="rounded-sm border-hairline border-hair bg-danger/5 px-a8 py-a4 font-ui text-hint leading-[1.7] text-danger"
        >
          {item.text}
        </div>
      )
    }

    if (item.kind === 'reasoning') {
      return (
        <div key={key} data-item="reasoning">
          <ReasoningBlock
            text={item.text}
            streaming={item.streaming}
            durationMs={item.endedAt - item.startedAt}
          />
        </div>
      )
    }

    const label = toolLabel(item.name, item.args, ctx.workspaceRoot)
    // 文件类工具的详情视图（差异 / 代码）：折叠态不画，但展开时要有——它只需要参数与
    // 结果，两条来源（事件流 / 重读会话）都有，所以刷新后同形。
    const detail = toolDetail(item.name, item.args, item.result, item.status)
    return (
      // data-* 是给端到端脚本读时间线用的：段落的种类与身份写在 DOM 上，
      // 脚本不必猜 class 名（改样式不会让验收脚本静默失效）。
      <div key={key} data-item="tool" data-call={item.callId} className={tight ? '-mt-a8' : undefined}>
        <ToolCard
          icon={label.icon}
          verb={label.verb}
          target={label.target}
          status={item.status}
          durationMs={item.durationMs}
          steps={subagentSteps(item)}
          workspaceRoot={ctx.workspaceRoot}
          detail={detail}
          onOpen={item.name === 'subagent' ? ctx.onOpenSubagents : undefined}
        >
          <span className="line-clamp-6 block whitespace-pre-wrap">{item.result ?? item.args}</span>
        </ToolCard>
      </div>
    )
  })
}

export function Timeline({
  items,
  testId = 'timeline',
  workspaceRoot = null,
  onBranch,
  liveTail = false,
  onOpenSubagents,
}: TimelineProps) {
  // 手动展开的轮：折叠是默认，点开的那几轮记在这儿（切换会话/刷新即回到默认）。
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set())
  const groups = turnGroups(items)

  const toggle = (key: string) => {
    setOpened((cur) => {
      const next = new Set(cur)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const nodes: ReactNode[] = []
  groups.forEach((group, index) => {
    const running = liveTail && index === groups.length - 1
    // 可折一轮的三个条件：有收尾正文（末尾不是工具 = 这轮真跑完了）、有过程可收、
    // 且不是正在跑的那一轮。跑着的时候铺开——过程要逐段看得见。
    const foldable = group.answer !== null && group.process.length > 0 && !running
    const open = foldable && opened.has(group.key)
    const ctx: Ctx = {
      workspaceRoot,
      onBranch,
      onOpenSubagents,
      head: { pending: true },
      actions: turnActions(group.items),
    }
    const from = group.user === null ? 0 : 1

    if (group.user !== null) nodes.push(...itemNodes([group.user], group.offset, ctx))
    if (foldable) {
      // 折叠行是这一轮过程的抬头：收着时它是全部，点开后过程铺在它下面，还能再收起。
      nodes.push(
        <TurnSummary
          key={`${group.key}:summary`}
          durationMs={group.durationMs}
          open={open}
          onToggle={() => toggle(group.key)}
        />,
      )
      if (open) nodes.push(...itemNodes(group.process, from, ctx))
    } else {
      nodes.push(...itemNodes(group.process, from, ctx))
    }
    if (group.answer !== null) {
      nodes.push(...itemNodes([group.answer], group.items.length - 1, ctx))
    }
  })

  return (
    <div className="flex flex-col gap-a16" data-testid={testId}>
      {nodes}
    </div>
  )
}

function branchHandler(entryId: string | null, onBranch?: (entryId: string) => void): (() => void) | undefined {
  if (entryId === null || onBranch === undefined) return undefined
  return () => onBranch(entryId)
}
