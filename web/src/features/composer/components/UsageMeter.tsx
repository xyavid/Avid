import type { UsageReport } from '../../../api/types'
import { useBranches } from '../../../api/queries'
import { useTranslation } from '../../../lib/i18n'
import { useRunSelector } from '../../../state/runStore'
import { Tooltip } from '../../../ui/primitives'
import { formatPercent, formatTokens, pickUsage } from '../lib/usage'

export interface UsageMeterProps {
  /** 会话 id；null 时不取分支数据（还没有会话）。 */
  sessionId: string | null
  /** 当前查看的分支：用量按分支记账，切分支就该显示那一条链的读数。 */
  branch: string
}

/**
 * 用量指示器：上下文占用 · 缓存命中 · 压缩次数（挂在输入条那一行的空位里）。
 *
 * 两个域各自取数、在这里合并：
 *   · 活动域（`useRunSelector`）——运行中每轮 `run_status` 带来的实时读数；
 *   · 查询域（`useBranches`）——落盘的"该分支上一次运行"读数，进入会话与刷新时用它。
 *
 * 合并规则在 `lib/usage.ts` 的 `pickUsage`（活动域优先、查询域兜底）。没有读数时
 * 显示「—」而不是 0：`null` 表示**没有这个数**（端点没上报 / 不认识该模型的窗口），
 * 这与"确实是 0"不同，编一个 0 出来会让人以为上下文是空的。
 *
 * 为什么用工具提示装明细而不是铺在行里：这行是输入区，常驻的只有三个数；完整读数
 * （输入、窗口、缓存读写、命中率、压缩后）放在悬浮说明里，不抢输入的注意力。
 */
export function UsageMeter({ sessionId, branch }: UsageMeterProps) {
  const { t } = useTranslation()
  const live = useRunSelector((view) => view.usage)
  const branches = useBranches(sessionId)
  const saved = branches.data?.branches.find((item) => item.name === branch)?.usage ?? null
  const usage = pickUsage(live, saved)

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

  const context = contextText(usage, t)
  const cache =
    usage.cache.hit_ratio === null
      ? t('usage.meter.cacheUnknown')
      : t('usage.meter.cacheHit', { percent: formatPercent(usage.cache.hit_ratio) })
  const summary = [context, cache, t('usage.meter.compaction', {
    count: usage.compaction.count,
  })].join(' · ')

  return (
    <Tooltip label={usageDetail(usage, t)}>
      <span
        data-testid="usage-meter"
        tabIndex={0}
        className="cursor-help whitespace-nowrap font-mono text-[11px] text-ink/70"
      >
        {summary}
      </span>
    </Tooltip>
  )
}

type Translate = ReturnType<typeof useTranslation>['t']

/**
 * 上下文那一段。三种可空组合各给一条文案：有窗口才谈占用率，没有窗口就只报 tokens
 * （分母不认识该模型——这时算出来的百分比会是一个假数）。
 */
function contextText(usage: UsageReport, t: Translate): string {
  const tokens = formatTokens(usage.context.tokens)
  if (usage.context.window === null) {
    return t('usage.meter.contextTokens', { tokens })
  }
  const window = formatTokens(usage.context.window)
  if (usage.context.utilization === null) {
    return t('usage.meter.contextOfWindow', { tokens, window })
  }
  return t('usage.meter.contextOfWindowPercent', {
    tokens,
    window,
    percent: formatPercent(usage.context.utilization),
  })
}

/** 悬浮明细：只列这次上报真的带了的数，缺的整段不出现。 */
function usageDetail(usage: UsageReport, t: Translate): string {
  const rows = [
    t('usage.detail.input', { tokens: formatTokens(usage.context.tokens) }),
  ]
  if (usage.context.window !== null) {
    rows.push(t('usage.detail.window', { tokens: formatTokens(usage.context.window) }))
  }
  if (usage.cache.read_tokens !== null) {
    rows.push(t('usage.detail.read', { tokens: formatTokens(usage.cache.read_tokens) }))
  }
  if (usage.cache.write_tokens !== null) {
    rows.push(t('usage.detail.write', { tokens: formatTokens(usage.cache.write_tokens) }))
  }
  if (usage.cache.hit_ratio !== null) {
    rows.push(
      t('usage.detail.hit', { percent: formatPercent(usage.cache.hit_ratio) }),
    )
  }
  rows.push(t('usage.detail.compactions', { count: usage.compaction.count }))
  if (usage.compaction.last_compaction_tokens !== null) {
    rows.push(
      t('usage.detail.lastCompaction', {
        tokens: formatTokens(usage.compaction.last_compaction_tokens),
      }),
    )
  }
  if (usage.compaction.last_step) {
    rows.push(t('usage.detail.lastStep', { step: usage.compaction.last_step }))
  }
  return rows.join(' · ')
}
