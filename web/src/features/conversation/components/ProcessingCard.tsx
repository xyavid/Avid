import { Badge, Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'

export interface ProcessingCardProps {
  phaseLabel: string
  round: number
  tokens: number
  activeTool: string | null
}

/**
 * 「思考中」是独立卡片：进行中显示 PROCESSING… chip，展开后是全宽、可滚动、
 * 自动滚底；结束后本组件不再渲染（调用方按 phase 决定）。折叠偏好写 localStorage。
 */
export function ProcessingCard({ phaseLabel, round, tokens, activeTool }: ProcessingCardProps) {
  const { t } = useTranslation()
  const expanded = useUiStore((state) => state.thinkingExpanded)
  const toggle = useUiStore((state) => state.toggleThinking)

  return (
    <div className="sketch-chip flex flex-col gap-1 p-2">
      <div className="flex items-center gap-2">
        <Badge tone="info" pulse>
          {t('tools.processing')}
        </Badge>
        <span className="font-sketch text-xs">{phaseLabel}</span>
        <span className="font-mono text-[11px] text-ink/70">
          {t('chat.round', { round })} · {t('chat.tokens', { tokens })}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          aria-expanded={expanded}
          onClick={() => toggle()}
        >
          {expanded ? t('common.collapse') : t('common.expand')}
        </Button>
      </div>
      {expanded ? (
        <p className="scroll-area term max-h-72 overflow-y-auto p-2 text-[11px]">
          {activeTool ? t('chat.activeTool', { tool: activeTool }) : t('chat.status.streaming')}
        </p>
      ) : null}
    </div>
  )
}
