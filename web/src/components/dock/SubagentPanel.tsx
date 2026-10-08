/**
 * 子智能体面板（阶段 53）：右列里看每个子任务自己干了什么。
 *
 * 两级，与文件面板同构：**列表**（每条子任务一张入口卡：任务名 · N 步 · 运行中）↔
 * **详情**（那一条子运行自己的时间线）。详情直接复用 `components/chat/Timeline`——
 * 子运行的段落与父时间线是同一套模型、同一个渲染器，所以这里没有第二套界面语言。
 *
 * 面板里**不写说明性文字**（用户要求）：界面只摆操作需要的东西，解释进 `title`
 * （悬停才出现）。两件本可以写成长句的事实——子运行明细不落库、单条成败只在内核
 * 汇总的结果里——因此都只留工具提示，不占版面。
 */

import { useState } from 'react'

import type { SubagentRunView } from '../../state/timeline'
import { Icon } from '../../ui/Icon'
import { cx } from '../../ui/cx'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { Timeline } from '../chat/Timeline'

export type SubagentPanelProps = {
  /** 整条时间线里摊平出来的子运行（顺序即派发顺序）。 */
  runs: SubagentRunView[]
  /** 这一轮还在跑：给运行中的标记；否则只说「已结束」。 */
  live: boolean
  workspaceRoot: string | null
}

/** 一条子运行里画出来的步数（工具段）；面板列表只说这个，不评判成败。 */
function stepCount(run: SubagentRunView): number {
  return run.items.filter((item) => item.kind === 'tool').length
}

function StatusMark({ running }: { running: boolean }) {
  if (!running) return null
  return (
    <span
      aria-label="运行中"
      className="h-[5px] w-[5px] shrink-0 rounded-full bg-accent"
      style={{ animation: 'hana-pulse 1.6s ease-in-out infinite' }}
    />
  )
}

export function SubagentPanel({ runs, live, workspaceRoot }: SubagentPanelProps) {
  const scrollRef = useAutoHideScroll<HTMLDivElement>()
  // 选中哪一条是面板内部状态（页面不管）：`null` = 停在列表。
  const [selected, setSelected] = useState<number | null>(null)
  const run = selected === null ? null : (runs[selected] ?? null)

  if (runs.length === 0) {
    return <p className="p-a12 font-ui text-hint text-ink-muted">还没有子智能体</p>
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-a6 border-b border-hair px-a8 py-a6">
        {run === null ? (
          <span className="flex-1 px-a4 font-ui text-hint text-ink-muted">
            {`${runs.length} 个子任务${live ? ' · 运行中' : ''}`}
          </span>
        ) : (
          <button
            type="button"
            aria-label="回到子任务列表"
            title="子任务列表"
            onClick={() => setSelected(null)}
            className="flex min-w-0 flex-1 items-center gap-a6 rounded-sm px-a4 py-a6 text-left transition-colors duration-fast ease-out hover:bg-overlay-light"
          >
            <span aria-hidden className="shrink-0 rotate-90 text-ink-muted">
              <Icon name="chevron-down" size={12} />
            </span>
            <span className="min-w-0 truncate font-ui text-ui text-ink">{run.task}</span>
          </button>
        )}
      </div>

      <div ref={scrollRef} className="scroll-auto min-h-0 flex-1 overflow-y-auto">
        {run === null ? (
          <nav aria-label="子任务列表" className="flex flex-col gap-a8 p-a12">
            {runs.map((item, index) => (
              <button
                key={`${item.callId}:${item.index}`}
                type="button"
                onClick={() => setSelected(index)}
                title={
                  item.items.length === 0
                    ? '这条子任务的执行明细不落库（刷新或切走会话后只剩任务清单）；结论在主对话那张子智能体卡上'
                    : undefined
                }
                className={cx(
                  'flex items-start gap-a12 rounded-card border-hairline border-hair px-a12 py-a12 text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
                  selected === index && 'bg-overlay-light',
                )}
              >
                <span className="mt-[1px] shrink-0 text-accent">
                  <Icon name="bot" size={15} />
                </span>
                <span className="flex min-w-0 flex-1 flex-col gap-a2">
                  <span className="flex items-center gap-a6">
                    <span className="min-w-0 flex-1 truncate font-ui text-ui text-ink">{item.task}</span>
                    <StatusMark running={item.running && live} />
                  </span>
                  {item.items.length > 0 && (
                    <span className="font-ui text-hint text-ink-muted">{`${stepCount(item)} 步`}</span>
                  )}
                </span>
              </button>
            ))}
          </nav>
        ) : run.items.length === 0 ? (
          <p
            className="p-a12 font-ui text-hint text-ink-muted"
            title="子运行的执行明细不落库；结论在主对话那张子智能体卡上"
          >
            没有可显示的明细
          </p>
        ) : (
          // 子运行的时间线：与主对话同一个渲染器。它不折（子运行没有"收尾消息"这回事），
          // 但按轮分组的判定与父级共用一套代码。
          <div className="px-a12 py-a12">
            <Timeline testId="timeline-subagent" items={run.items} workspaceRoot={workspaceRoot} liveTail />
          </div>
        )}
      </div>
    </div>
  )
}
