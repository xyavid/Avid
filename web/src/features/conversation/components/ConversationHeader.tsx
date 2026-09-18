import { ArrowDown, PanelRight, ShieldAlert } from 'lucide-react'

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

/**
 * 头部：标题 + MEM 条 + 三个工具按钮 + 常驻 ID 胶带。
 *
 * 胶带**在流式布局里**（不再是绝对定位）：绝对定位时第二张胶带（运行/没有活动运行）
 * 正好压住下方两个工具按钮——实测在 1440/1280、检查器开合四种组合下都相交。
 * 现在左侧内容块与右侧胶带列同处一行，`flex-wrap` 让胶带在窄卡片下换行，永不相压。
 */
export function ConversationHeader(props: ConversationHeaderProps) {
  const { t } = useTranslation()
  const ratio = props.memoryRatio === null ? 0 : Math.max(0, Math.min(1, props.memoryRatio))

  return (
    <header className="flex flex-col gap-2 border-b-bold border-ink p-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="truncate font-sketch text-xl">
              {props.sessionName ?? t('sessions.unnamed')}
            </h1>
            <Badge tone={phaseTone(props.phase)}>{t(`chat.status.${props.phase}`)}</Badge>
            <span className="shrink-0 font-mono text-[11px] text-ink/70">
              {t('chat.round', { round: props.round })} ·{' '}
              {t('chat.tokens', { tokens: props.tokens })}
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span className="shrink-0 font-sketch text-[11px] text-ink/70">{t('chat.memory')}</span>
            <span
              className="h-2 min-w-0 flex-1 overflow-hidden rounded-blob border-hair border-ink bg-sand"
              role="img"
              aria-label={t('chat.memory')}
            >
              <span className="hatch block h-full" style={{ width: `${Math.round(ratio * 100)}%` }} />
            </span>
            <div className="flex shrink-0 items-center gap-1">
              <Tooltip label={t('chat.header.scrollToBottom')}>
                <Button
                  size="icon"
                  aria-label={t('chat.header.scrollToBottom')}
                  onClick={props.onScrollToBottom}
                >
                  <ArrowDown size={16} aria-hidden="true" />
                </Button>
              </Tooltip>
              <Tooltip label={t('chat.header.inspector')}>
                <Button
                  size="icon"
                  variant={props.inspectorOpen ? 'primary' : 'secondary'}
                  aria-pressed={props.inspectorOpen}
                  aria-label={t('chat.header.inspector')}
                  onClick={props.onToggleInspector}
                >
                  <PanelRight size={16} aria-hidden="true" />
                </Button>
              </Tooltip>
              <Tooltip label={t('chat.header.approvals')}>
                <span className="relative inline-flex">
                  <Button
                    size="icon"
                    variant={props.pendingApprovals > 0 ? 'danger' : 'secondary'}
                    aria-label={t('chat.header.approvals')}
                    onClick={props.onFocusApprovals}
                  >
                    <ShieldAlert size={16} aria-hidden="true" />
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
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1">
          <Tape
            label={t('chat.sessionLabel')}
            value={props.sessionId}
            emptyText={t('chat.noRun')}
          />
          <Tape label={t('chat.runLabel')} value={props.runId} emptyText={t('chat.noRun')} />
        </div>
      </div>
    </header>
  )
}
