import { Button, Tooltip } from '../../../ui/primitives'
import { useEffect } from 'react'

import { useTranslation } from '../../../lib/i18n'
import { EntryRow, StepGroup, ToolCallCard } from '../../../ui/patterns'
import type { Density } from '../../../state/uiStore'
import type { TimelineEntry } from '../../../events/reducer'
import { useGroupedTimeline } from '../hooks/useGroupedTimeline'
import { useTimelineWindow } from '../hooks/useTimelineWindow'
import type { ToolRun } from '../../../events/reducer'

export interface TimelineProps {
  entries: TimelineEntry[]
  tools: ToolRun[]
  density: Density
  onInspect: (entry: TimelineEntry) => void
  onInspectTool: (run: ToolRun) => void
  onCopy: (text: string) => void
  /** 初次加载（还没有任何事件到达）时的占位。 */
  loading?: boolean
  resetKey: string
  /** 命令序号：同值重复点击也要真的触发贴底（照 purrcat 的命令对象做法）。 */
  jumpToken?: number
}

/** 时间线：**唯一滚动容器**（导航与检查器各自滚动）。 */
export function Timeline({
  entries,
  tools,
  density,
  onInspect,
  onInspectTool,
  onCopy,
  loading = false,
  resetKey,
  jumpToken = 0,
}: TimelineProps) {
  const { t } = useTranslation()
  const blocks = useGroupedTimeline(entries, tools)
  const windowState = useTimelineWindow(blocks.length, resetKey)
  const { scrollToBottom } = windowState

  useEffect(() => {
    if (jumpToken > 0) scrollToBottom('smooth')
  }, [jumpToken, scrollToBottom])
  const visible = blocks.slice(Math.max(0, blocks.length - windowState.visibleCount))

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={windowState.containerRef}
        onScroll={windowState.handleScroll}
        className="scroll-area flex-1 px-3 py-4"
        role="log"
        aria-live="off"
      >
        {windowState.hasEarlier ? (
          <div className="mb-3 flex justify-center">
            <Button size="sm" onClick={windowState.loadEarlier}>
              {t('chat.loadEarlier')}
            </Button>
          </div>
        ) : null}

        {blocks.length === 0 ? (
          <div className="empty-note mx-auto mt-8 max-w-md">
            <p className="font-sketch text-base">{t('chat.empty.title')}</p>
            <p className="mt-1 text-xs">{t('chat.empty.hint')}</p>
          </div>
        ) : null}

        {loading && blocks.length === 0 ? (
          <p className="text-center text-sm text-ink/70">{t('common.loading')}</p>
        ) : null}

        <div className="flex flex-col gap-3">
          {visible.map((block) =>
            block.kind === 'entry' ? (
              <EntryRow
                key={block.id}
                entry={block.entry}
                density={density}
                onInspect={onInspect}
                onCopy={onCopy}
              />
            ) : block.kind === 'group' ? (
              <StepGroup
                key={block.id}
                title={t('tools.group.title', { count: block.runs.length })}
                tools={block.runs}
                contents={block.contents}
                density={density}
                onInspect={onInspectTool}
              />
            ) : (
              <ToolCallCard
                key={block.id}
                run={block.run}
                content={block.content}
                density={density}
                onInspect={onInspectTool}
              />
            ),
          )}
        </div>
      </div>

      {!windowState.atBottom ? (
        <div className="pointer-events-none absolute bottom-3 left-0 right-0 flex justify-center">
          <Tooltip label={t('chat.scrollToBottom')}>
            <Button
              size="sm"
              className="pointer-events-auto"
              onClick={() => windowState.scrollToBottom('smooth')}
            >
              {t('chat.scrollToBottom')}
            </Button>
          </Tooltip>
        </div>
      ) : null}
    </div>
  )
}
