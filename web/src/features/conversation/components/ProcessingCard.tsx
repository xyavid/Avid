import { useEffect, useRef } from 'react'

import { Badge, Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'

export interface ProcessingCardProps {
  phaseLabel: string
  round: number
  tokens: number
  activeTool: string | null
  /** 当前这一轮的思维链增量（A2）。空串 = 这一轮还没有思维链。 */
  reasoning: string
}

/**
 * 「思考中」是独立卡片：进行中显示 PROCESSING… chip，展开后是全宽、可滚动、
 * 自动滚底；结束后本组件不再渲染（调用方按 phase 决定）。折叠偏好写 localStorage。
 *
 * A2：推理模型的思维链原本在界面上完全不可见——几万字符的过程只体现为"卡住"，
 * 正文为空时更像是什么都没发生。现在把 `reasoning` 显示在展开区。
 */
export function ProcessingCard({
  phaseLabel,
  round,
  tokens,
  activeTool,
  reasoning,
}: ProcessingCardProps) {
  const { t } = useTranslation()
  const expanded = useUiStore((state) => state.thinkingExpanded)
  const toggle = useUiStore((state) => state.toggleThinking)
  const bottom = useRef<HTMLParagraphElement | null>(null)

  // 思维链是**追加**的：不滚底就永远只看得到开头几行。
  useEffect(() => {
    if (!expanded) return
    if (bottom.current) bottom.current.scrollTop = bottom.current.scrollHeight
  }, [expanded, reasoning])

  return (
    <div className="surface-chip flex flex-col gap-1 p-2">
      <div className="flex items-center gap-2">
        <Badge tone="info" pulse>
          {t('tools.processing')}
        </Badge>
        <span className="text-xs">{phaseLabel}</span>
        {reasoning ? (
          <span className="text-xs text-ink-muted">{t('chat.reasoning')}</span>
        ) : null}
        <span className="font-mono text-[11px] text-ink-muted">
          {t('chat.round', { round })} · {t('chat.tokens', { tokens })}
        </span>
        <Button
          size="sm"
          variant="secondary"
          className="ml-auto"
          aria-expanded={expanded}
          onClick={() => toggle()}
        >
          {expanded ? t('common.collapse') : t('common.expand')}
        </Button>
      </div>
      {expanded ? (
        <div className="flex flex-col gap-1">
          <p className="text-[11px]">
            {activeTool ? t('chat.activeTool', { tool: activeTool }) : t('chat.status.streaming')}
          </p>
          {reasoning ? (
            <p ref={bottom} className="scroll-area term max-h-72 overflow-y-auto p-2 text-[11px]">
              {reasoning}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
