/**
 * 子智能体面板（阶段 53）：右列里看每个子任务自己干了什么。
 *
 * 两级，与文件面板同构：**列表**（每条子任务一张入口卡：任务名 · N 步 · 运行中）↔
 * **详情**（那一条子运行自己的时间线）。详情直接复用 `components/chat/Timeline`——
 * 子运行的段落与父时间线是同一套模型、同一个渲染器，所以这里没有第二套界面语言。
 *
 * 一件必须说实话的事：子运行的**明细不落库**（子运行没有 sink，增量不重放）。
 * 任务清单来自父级那次调用的参数（落在会话 JSONL 里），所以刷新 / 切会话后还在；
 * `items` 会是空的。这时面板说「明细不落库，结论看卡片」，而不是画一个空时间线
 * 假装它什么都没干。
 *
 * 成败不在这里下判断：单条子任务的失败写在内核汇总的那段结果里（父级卡片可见），
 * 面板不解析那段措辞——与 `toolDetail` 的同一条纪律。
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
    return (
      <p className="p-a12 font-ui text-hint text-ink-muted">
        这次会话还没有派过子智能体。主 agent 认为子任务互相独立时会派它们并行去干。
      </p>
    )
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
                className={cx(
                  'flex items-start gap-a12 rounded-card border-hairline border-hair px-a12 py-a12 text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
                  selected === index && 'bg-overlay-light',
                )}
              >
                <span className="mt-[1px] shrink-0 text-accent">
                  <Icon name="git-branch" size={15} />
                </span>
                <span className="flex min-w-0 flex-1 flex-col gap-a2">
                  <span className="flex items-center gap-a6">
                    <span className="min-w-0 flex-1 truncate font-ui text-ui text-ink">{item.task}</span>
                    <StatusMark running={item.running && live} />
                  </span>
                  <span className="font-ui text-hint text-ink-muted">
                    {item.items.length === 0 ? '明细不落库（只有任务清单）' : `${stepCount(item)} 步`}
                  </span>
                </span>
              </button>
            ))}
          </nav>
        ) : run.items.length === 0 ? (
          <p className="p-a12 font-ui text-hint text-ink-muted">
            这条子任务的执行明细不落库（子运行不写会话），刷新或切走会话后就只剩任务清单；
            它的结论在主对话里那张子智能体卡上。
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
