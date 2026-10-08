/**
 * 收尾折叠行（阶段 52 追加）：一轮跑完后，过程收成的一行。
 *
 * 它就是「完成后只留最后那条大幅消息」的开关——行本身写「完成了什么、花了多久」，
 * 点开把这轮的过程（思考 / 中间正文 / 工具）铺回原位，再点收起。参考界面同形：
 * 文案左、折角紧跟文案、下面一条发丝线把它和收尾正文分开。
 *
 * 为什么要有用时：一轮回话值不值得点开，一半的信息在「它跑了多久」；这个读数
 * 从消息段的 `ts` 来（见 `timeline.ts` 的 `MessageTs`），没有读数就只说「已完成」——
 * 宁可少一个数，不编一个数。
 */

import { elapsedLabel } from '../../ui/duration'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type TurnSummaryProps = {
  /** 本轮用时；null = 没有读数。 */
  durationMs: number | null
  /** 过程是否铺开着。 */
  open: boolean
  onToggle: () => void
}

export function TurnSummary({ durationMs, open, onToggle }: TurnSummaryProps) {
  const label = durationMs === null ? '已完成' : `已完成，用时 ${elapsedLabel(durationMs)}`

  return (
    <button
      type="button"
      aria-expanded={open}
      data-item="summary"
      onClick={onToggle}
      className="group flex w-full items-center gap-a6 border-b border-hair pb-a6 text-left"
    >
      <span
        className={cx(
          'font-ui text-hint transition-colors duration-fast ease-out',
          open ? 'text-ink-light' : 'text-ink-muted group-hover:text-ink-light',
        )}
      >
        {label}
      </span>
      <span
        className={cx(
          'text-ink-muted transition-transform duration-fast ease-out group-hover:text-ink-light',
          open ? 'rotate-180' : undefined,
        )}
      >
        {/* 收起时朝下（"下面还有东西"），点开后翻上去——与参考界面的折角同形 */}
        <Icon name="chevron-down" size={12} />
      </span>
    </button>
  )
}
