import { Badge, Button, Tooltip } from '../../../ui/primitives'
import { Tape } from '../../../ui/sketch'
import { useTranslation } from '../../../lib/i18n'
import type { RunPhase } from '../../../events/reducer'

export interface ConversationHeaderProps {
  sessionName: string | null
  sessionId: string | null
  runId: string | null
  phase: RunPhase
  round: number
  tokens: number
  pendingApprovals: number
  /** 最近一次压缩的 after / before：MEM 条用它表达上下文占用。 */
  memoryRatio: number | null
  inspectorOpen: boolean
  onToggleInspector: () => void
  onFocusApprovals: () => void
  onScrollToBottom: () => void
}

function phaseTone(phase: RunPhase): 'neutral' | 'info' | 'ok' | 'warn' | 'danger' {
  if (phase === 'failed') return 'danger'
  if (phase === 'awaiting_approval') return 'warn'
  if (phase === 'done') return 'ok'
  if (phase === 'streaming' || phase === 'cancelling' || phase === 'submitting') return 'info'
  return 'neutral'
}

/** 头部：标题 + MEM 条 + 40×40 工具按钮（带计数徽标）+ 常驻 ID 胶带。 */
export function ConversationHeader(props: ConversationHeaderProps) {
  const { t } = useTranslation()
  const ratio = props.memoryRatio === null ? 0 : Math.max(0, Math.min(1, props.memoryRatio))

  return (
    <header className="relative flex flex-col gap-2 border-b-bold border-ink p-3">
      <div className="flex items-center gap-2 pr-28">
        <h1 className="truncate font-sketch text-xl">
          {props.sessionName ?? t('sessions.unnamed')}
        </h1>
        <Badge tone={phaseTone(props.phase)}>{t(`chat.status.${props.phase}`)}</Badge>
        <span className="font-mono text-[11px] text-ink/70">
          {t('chat.round', { round: props.round })} · {t('chat.tokens', { tokens: props.tokens })}
        </span>
      </div>

      <div className="flex items-center gap-3">
        <span className="font-sketch text-[11px] text-ink/70">{t('chat.memory')}</span>
        <span
          className="h-2 flex-1 overflow-hidden rounded-blob border-hair border-ink bg-sand"
          role="img"
          aria-label={t('chat.memory')}
        >
          <span className="hatch block h-full" style={{ width: `${Math.round(ratio * 100)}%` }} />
        </span>
        <div className="flex items-center gap-1">
          <Tooltip label={t('chat.scrollToBottom')}>
            <Button size="icon" aria-label={t('chat.scrollToBottom')} onClick={props.onScrollToBottom}>
              ↓
            </Button>
          </Tooltip>
          <Tooltip label={t('common.inspect')}>
            <Button
              size="icon"
              variant={props.inspectorOpen ? 'primary' : 'secondary'}
              aria-pressed={props.inspectorOpen}
              aria-label={t('common.inspect')}
              onClick={props.onToggleInspector}
            >
              ⌕
            </Button>
          </Tooltip>
          <Tooltip label={t('approvals.title')}>
            <span className="relative inline-flex">
              <Button
                size="icon"
                variant={props.pendingApprovals > 0 ? 'danger' : 'secondary'}
                aria-label={t('approvals.title')}
                onClick={props.onFocusApprovals}
              >
                !
              </Button>
              {props.pendingApprovals > 0 ? (
                <span className="pulse absolute -right-2 -top-2 rounded-blob border-hair border-ink bg-danger-bg px-1 font-mono text-[10px]">
                  {props.pendingApprovals}
                </span>
              ) : null}
            </span>
          </Tooltip>
        </div>
      </div>

      <div className="absolute right-3 top-2 flex flex-col items-end gap-1">
        <Tape
          label={t('chat.sessionLabel')}
          value={props.sessionId}
          emptyText={t('chat.noRun')}
        />
        <Tape label={t('chat.runLabel')} value={props.runId} emptyText={t('chat.noRun')} />
      </div>
    </header>
  )
}
