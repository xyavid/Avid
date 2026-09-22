import type { ReactNode } from 'react'
import { clsx } from 'clsx'

import type { UsageReport } from '../../../api/types'
import { useBranches, useMeta } from '../../../api/queries'
import { useTranslation } from '../../../lib/i18n'
import { useRunSelector } from '../../../state/runStore'
import { Tooltip } from '../../../ui/primitives'
import type { UsagePart, UsageTone } from '../lib/usage'
import { barPercent, formatPercent, formatTokens, pickUsage, usageParts, usageTone } from '../lib/usage'

export interface UsageMeterProps {
  /** 会话 id；null 时不取分支数据（还没有会话）。 */
  sessionId: string | null
  /** 当前查看的分支：用量按分支记账，切分支就该显示那一条链的读数。 */
  branch: string
}

/** 三块各自的颜色：系统提示词用中性沙色、工具定义用陶土、对话消息用信息蓝。 */
const PART_BAR: Record<UsagePart['key'], string> = {
  system: 'bg-ink/40',
  tools: 'bg-accent',
  messages: 'bg-info-bg',
}

const TONE_TEXT: Record<UsageTone, string> = {
  neutral: 'text-ink/70',
  warn: 'text-warn',
  danger: 'text-danger',
}

const TONE_BAR: Record<UsageTone, string> = {
  neutral: 'bg-ink/50',
  warn: 'bg-warn',
  danger: 'bg-danger',
}

const PART_LABEL: Record<UsagePart['key'], string> = {
  system: 'usage.part.system',
  tools: 'usage.part.tools',
  messages: 'usage.part.messages',
}

/**
 * 用量指示器：`上下文已用 34%` + 迷你进度条 + `缓存命中 78%`（挂在输入条那一行的空位里）。
 *
 * 两个域各自取数、在这里合并：
 *   · 活动域（`useRunSelector`）——运行中每轮 `run_status` 带来的实时读数；
 *   · 查询域（`useBranches`）——落盘的"该分支上一次运行"读数，进入会话与刷新时用它。
 * 合并规则在 `lib/usage.ts` 的 `pickUsage`（活动域优先、查询域兜底）。
 *
 * 常驻的只有两个百分比：占用率按 `<60% / 60–85% / >85%` 三档上色（阈值与依据见
 * `docs/guide/web-ui.md` §3.3）。完整读数——三块文本的估算占比、缓存读写、压缩——
 * 在悬浮明细里（`UsageDetail`），不抢输入的注意力。
 *
 * 没有占用率（不认识该模型的窗口）时退回只报 tokens：**不编分母**，也不显示 0%。
 * 没有读数时显示「用量 —」：`null` 表示"没有这个数"（端点没上报），与"确实是 0"不同。
 *
 * **按能力表分支**（同 `features.deltas`）：内核没在 `/api/meta` 里声明 `usage` 时
 * 整个指示器不画——旧内核上它只会永远挂着一个「用量 —」，那比没有更让人困惑。
 * 三个 hook 都在分支之前调用（hooks 规则）：判定只决定**渲染什么**，不决定调用什么。
 */
export function UsageMeter({ sessionId, branch }: UsageMeterProps) {
  const { t } = useTranslation()
  const meta = useMeta()
  const live = useRunSelector((view) => view.usage)
  const branches = useBranches(sessionId)
  const saved = branches.data?.branches.find((item) => item.name === branch)?.usage ?? null
  const usage = pickUsage(live, saved)

  // meta 还没到（首屏）也先不画：宁可晚一拍，也不要在能力未知时先占一行位置。
  if (meta.data?.features.usage !== 1) return null

  if (!usage) {
    return (
      <span
        data-testid="usage-meter"
        className="whitespace-nowrap font-mono text-[11px] text-ink/50"
      >
        {t('usage.meter.empty')}
      </span>
    )
  }

  const utilization = usage.context.utilization
  const tone = usageTone(utilization)
  const cache =
    usage.cache.hit_ratio === null
      ? t('usage.meter.cacheUnknown')
      : t('usage.meter.cacheHitShort', {
          percent: formatPercent(usage.cache.hit_ratio),
        })

  return (
    <Tooltip label={<UsageDetail usage={usage} />}>
      <span
        data-testid="usage-meter"
        tabIndex={0}
        className="flex cursor-help items-center gap-2 whitespace-nowrap font-mono text-[11px]"
      >
        {utilization === null ? (
          <span className="text-ink/60">
            {t('usage.meter.contextTokens', {
              tokens: formatTokens(usage.context.tokens),
            })}
          </span>
        ) : (
          <>
            <span className={TONE_TEXT[tone]}>
              {t('usage.meter.contextPercent', {
                percent: formatPercent(utilization),
              })}
            </span>
            <span className="block h-1.5 w-16 overflow-hidden rounded-full border-hair border-ink/40 bg-sand">
              <span
                className={clsx('block h-full', TONE_BAR[tone])}
                style={{ width: `${barPercent(utilization)}%` }}
              />
            </span>
          </>
        )}
        <span className="text-ink/40">·</span>
        <span className="text-ink/60">{cache}</span>
      </span>
    </Tooltip>
  )
}

/**
 * 悬浮明细：占用率与总数、三块文本的堆叠条与清单、以及缓存与压缩的真实读数。
 *
 * 单独导出是为了能脱离 Radix 气泡直接单测（气泡内容只在打开时才挂载）。
 * 缺的数整段不出现——"没有这个数"不拿 0 顶。
 */
export function UsageDetail({ usage }: { usage: UsageReport }) {
  const { t } = useTranslation()
  const parts = usageParts(usage.context.parts)
  const utilization = usage.context.utilization
  const window = usage.context.window

  return (
    <div data-testid="usage-detail" className="flex w-60 flex-col gap-2 p-1 text-[11px]">
      <div className="flex items-baseline justify-between gap-2">
        <span
          className={clsx('font-mono', TONE_TEXT[usageTone(utilization)])}
        >
          {utilization === null
            ? t('usage.detail.noWindow')
            : t('usage.detail.used', { percent: formatPercent(utilization) })}
        </span>
        <span className="font-mono text-ink/70">
          {formatTokens(usage.context.tokens)}
          {window === null ? '' : ` / ${formatTokens(window)}`}
        </span>
      </div>

      {parts.length > 0 ? (
        <>
          <span className="flex h-1.5 overflow-hidden rounded-full border-hair border-ink/40 bg-sand">
            {parts.map((part) => (
              <span
                key={part.key}
                className={clsx('block h-full', PART_BAR[part.key])}
                style={{ width: `${Math.round(part.ratio * 100)}%` }}
              />
            ))}
          </span>
          <ul className="flex flex-col gap-1">
            {parts.map((part) => (
              <li key={part.key} className="flex items-center gap-2">
                <span
                  className={clsx('block h-2 w-2 shrink-0 rounded-chip', PART_BAR[part.key])}
                />
                <span className="flex-1 font-sketch">{t(PART_LABEL[part.key])}</span>
                <span className="font-mono text-ink/70">
                  {`~${formatTokens(part.tokens)}`}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-ink/50">{t('usage.detail.estimated')}</p>
        </>
      ) : null}

      <ul className="flex flex-col gap-1">
        {usage.cache.read_tokens === null ? null : (
          <DetailRow
            label={t('usage.detail.read')}
            value={formatTokens(usage.cache.read_tokens)}
          />
        )}
        {usage.cache.write_tokens === null ? null : (
          <DetailRow
            label={t('usage.detail.write')}
            value={formatTokens(usage.cache.write_tokens)}
          />
        )}
        {usage.cache.hit_ratio === null ? null : (
          <DetailRow
            label={t('usage.detail.hit')}
            value={formatPercent(usage.cache.hit_ratio)}
          />
        )}
        <DetailRow
          label={t('usage.detail.compactions')}
          value={String(usage.compaction.count)}
        />
        {usage.compaction.last_compaction_tokens === null ? null : (
          <DetailRow
            label={t('usage.detail.lastCompaction')}
            value={formatTokens(usage.compaction.last_compaction_tokens)}
          />
        )}
        {usage.compaction.last_step ? (
          <DetailRow label={t('usage.detail.lastStep')} value={usage.compaction.last_step} />
        ) : null}
      </ul>
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <li className="flex items-center justify-between gap-2">
      <span className="font-sketch text-ink/70">{label}</span>
      <span className="font-mono text-ink/70">{value}</span>
    </li>
  )
}
