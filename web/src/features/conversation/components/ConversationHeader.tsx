import { Badge } from '../../../ui/primitives'
import { Tape } from '../../../ui/sketch'
import { useTranslation } from '../../../lib/i18n'
import type { RunPhase } from '../../../events/reducer'

export interface ConversationHeaderProps {
  sessionName: string | null
  /** 归属工作区名；null = 这个会话 header 里没有归属（更早创建的会话）。 */
  workspaceName: string | null
  sessionId: string | null
  runId: string | null
  phase: RunPhase
  round: number
  tokens: number
}

function phaseTone(phase: RunPhase): 'neutral' | 'info' | 'ok' | 'warn' | 'danger' {
  if (phase === 'failed') return 'danger'
  if (phase === 'awaiting_approval') return 'warn'
  if (phase === 'done') return 'ok'
  if (phase === 'streaming' || phase === 'cancelling' || phase === 'submitting') return 'info'
  return 'neutral'
}

/**
 * 头部：标题 + 状态 + 轮次/token + 常驻 ID 胶带。
 *
 * 这里曾经还有一条"上下文占用"（最后一次压缩的 after/before）与三颗工具按钮：
 *   · 占用条只在压缩发生过之后才有值，平时恒为空——它显示的不是"现在用了多少上下文"，
 *     与用户对"占用"的预期不符，已去掉；
 *   · ↓ 与时间线里那颗悬浮的「回到最新」重复，只留后者；
 *   · 「检查器」与「待决审批」两颗没有实际作用：前者与条目动作行的「查看」重复
 *     （检查器本来就由「查看」打开、由它自己的关闭按钮收起），后者滚动到审批条而这个
 *     条子就在同一屏内。两颗都删掉，需要时再按真实需求加回来。
 *
 * 胶带**在流式布局里**（不再是绝对定位）：绝对定位时第二张胶带会压住左侧内容。
 * 现在左侧内容块与右侧胶带列同处一行，`flex-wrap` 让胶带在窄卡片下换行，永不相压。
 */
export function ConversationHeader(props: ConversationHeaderProps) {
  const { t } = useTranslation()

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

          {props.workspaceName ? (
            <p className="truncate font-sketch text-[11px] text-ink/70">
              {t('chat.workspace', { name: props.workspaceName })}
            </p>
          ) : null}

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
