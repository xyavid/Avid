/**
 * 上下文容量环（阶段 52）：输入区左簇里的一只圆环 + 点开的明细弹层。
 *
 * 为什么是环而不是一行读数：占用率是**运行中每一轮都在变**的数（每轮模型调用都会刷新
 * usage），它值得一个一眼可读的形状——环满到什么程度、变红没有，扫一眼就知道；
 * 精确到小数点的数留给点开的明细。
 *
 * 读数来源：运行中吃 `run_status` / `run_finished` 事件里的 usage 快照（每轮刷新，
 * 所以环是动态的），收尾后与分支落盘的那份对齐（`state/useRunStream.ts`）。
 * 没有读数一律显示「—」，环画成空轨——「未上报」与「确实是 0」不是一回事。
 *
 * 明细的形状对着参考界面：容量标题 + 用的/总量（占用率）+ 容量条 + 分块构成 +
 * 底部的平均缓存命中率。分块只有三类（内核只按 系统提示词 / 系统工具 / 消息 记账，
 * 见 `agent/state.py` 的 `_split_context`），不编没有的维度。
 */

import { useState } from 'react'

import type { UsageReport } from '../../api/types'
import { cx } from '../../ui/cx'

/** 环画到多满就该警觉：越过这条线换 danger 色（纸本语言里没有第三档黄）。 */
const DANGER_AT = 0.85

const RING_SIZE = 18
const RING_STROKE = 2.5

function ratioOf(usage: UsageReport | null): number | null {
  const ratio = usage?.context.utilization
  return ratio === null || ratio === undefined ? null : Math.min(1, Math.max(0, ratio))
}

/** 环：底轨 + 进度弧。`ratio` 变化时弧长有过渡，所以看着是"长出来的"。 */
function Ring({ ratio }: { ratio: number | null }) {
  const radius = (RING_SIZE - RING_STROKE) / 2
  const circumference = 2 * Math.PI * radius
  const filled = (ratio ?? 0) * circumference
  return (
    <svg
      width={RING_SIZE}
      height={RING_SIZE}
      viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
      aria-hidden
      className="-rotate-90"
    >
      <circle
        cx={RING_SIZE / 2}
        cy={RING_SIZE / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeOpacity={0.2}
        strokeWidth={RING_STROKE}
      />
      <circle
        cx={RING_SIZE / 2}
        cy={RING_SIZE / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth={RING_STROKE}
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference}`}
        className="transition-[stroke-dasharray] duration-slow ease-out"
      />
    </svg>
  )
}

/** token 数：够大就按「万」收（整数不带小数点：20万 / 5.6万）；null → 「—」。 */
function compactTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value >= 10_000) {
    const wan = value / 10_000
    return `${Number.isInteger(wan) ? wan.toFixed(0) : wan.toFixed(1)}万`
  }
  return value.toLocaleString('en-US')
}

function pct(ratio: number | null | undefined): string {
  return ratio === null || ratio === undefined ? '—' : `${(ratio * 100).toFixed(1)}%`
}

export type ContextRingProps = {
  usage: UsageReport | null
}

export function ContextRing({ usage }: ContextRingProps) {
  const [open, setOpen] = useState(false)
  const ratio = ratioOf(usage)
  const hot = ratio !== null && ratio >= DANGER_AT
  const context = usage?.context ?? null
  const parts = context?.parts ?? null
  // 分块是"当前这轮的提示词构成"，所以占比以已用 tokens 为分母，不是窗口。
  const share = (value: number): string =>
    context?.tokens ? `${((value / context.tokens) * 100).toFixed(1)}%` : '—'

  return (
    <div className="relative">
      <button
        type="button"
        aria-label={`上下文容量 ${pct(ratio)}`}
        aria-expanded={open}
        title="上下文容量"
        onClick={() => setOpen((value) => !value)}
        className={cx(
          'inline-flex h-[26px] items-center gap-a6 rounded-sm border-hairline border-hair px-a8 font-ui text-caption transition-colors duration-fast ease-out hover:bg-overlay-light',
          hot ? 'text-danger' : 'text-ink-light',
        )}
      >
        <Ring ratio={ratio} />
        <span className="tabular-nums">{pct(ratio)}</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="上下文容量"
          className="absolute bottom-full left-0 z-20 mb-a8 w-[320px] rounded-md border-hairline border-hair bg-card p-a12 shadow-soft"
        >
          <div className="flex items-baseline justify-between gap-a8">
            <span className="font-ui text-ui text-ink">上下文容量</span>
            <span className="truncate font-ui text-hint text-ink-muted">
              {compactTokens(context?.tokens)}/{compactTokens(context?.window)}（{pct(ratio)}）
            </span>
          </div>

          {/* 容量条：与环同一个数，这里给"还差多少到顶"的长度感 */}
          <div className="mt-a8 h-[6px] overflow-hidden rounded-full bg-overlay-medium">
            <div
              className={cx(
                'h-full rounded-full transition-[width] duration-slow ease-out',
                hot ? 'bg-danger' : 'bg-accent',
              )}
              style={{ width: `${(ratio ?? 0) * 100}%` }}
            />
          </div>

          <div className="mt-a12 flex flex-col gap-a6">
            <PartRow label="消息" value={parts === null ? '—' : share(parts.messages)} />
            <PartRow label="系统工具" value={parts === null ? '—' : share(parts.tools)} />
            <PartRow label="系统提示词" value={parts === null ? '—' : share(parts.system)} />
          </div>

          <div className="mt-a12 flex items-baseline justify-between gap-a8 border-t border-hair pt-a8">
            <span className="font-ui text-ui text-ink">平均缓存命中率</span>
            <span className="font-ui text-ui text-ink">{pct(usage?.cache.hit_ratio)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

/** 比例条（参考界面里每行前面的小色点换成一段与占比等长的短线）。 */
function PartRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-a8">
      <span aria-hidden className="h-[6px] w-[6px] shrink-0 rounded-full bg-accent" />
      <span className="font-ui text-hint text-ink">{label}</span>
      <span className="ml-auto font-ui text-hint text-ink-muted tabular-nums">{value}</span>
    </div>
  )
}
