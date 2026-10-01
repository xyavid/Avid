/**
 * 顶部条中段的「滑动胶囊」标签（截图里是聊天 / 频道）。
 *
 * 两条取舍写清楚：
 *
 * 1. **不做 transform 滑块**。截图里选中块的位移看着是滑过去的，但那要求先量出
 *    每个选项的宽度、再算出位移量——宽度会随文案与字体回退变化，量一次不够，
 *    还得在 resize / 字体加载后重量。本视觉本来就禁弹跳（hana 报告 §8.2），
 *    所以这里只做底色与墨色的 `transition-colors`：切换是"这一块亮起来"，
 *    不是"一块滑块滑过去"。代价是少了那点精致感，换来的是不确定宽度下的稳定。
 *
 * 2. **外壳是凹槽、选中项是纸片**：`bg-inset` + `p-[2px]` 的凹槽包住若干
 *    `rounded-sm` 的按钮，选中那块用 `bg-card shadow-1` 抬起来。层级靠"凹/凸"
 *    而不是靠颜色，于是全站仍然只有一个彩色在说话。
 *
 * 无障碍：`role="tablist"` + `aria-selected`；禁用的页签保留在列表里（而不是删掉），
 * 这样"这个面存在但暂时不可用"能被读出来，并用 `title` 说明原因。
 * 没做 roving tabindex——每个页签都是原生 button，Tab 可逐个到达；
 * 键盘用户多按几下 Tab，比一个需要自实现方向键语义的 tablist 更不容易出错。
 */

import type { ReactElement } from 'react'

import { cx } from '../ui/primitives'

export interface SurfaceTab {
  key: string
  label: string
  disabled?: boolean
}

export interface SurfaceTabsProps {
  tabs: SurfaceTab[]
  active: string
  onChange: (key: string) => void
  'aria-label': string
}

/** 禁用页签的悬停说明：不说"不可用"，说清是"还没做出来"。 */
const DISABLED_HINT = '暂未开放'

export function SurfaceTabs({
  tabs,
  active,
  onChange,
  'aria-label': ariaLabel,
}: SurfaceTabsProps): ReactElement {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="flex items-center gap-[2px] rounded-md bg-inset p-[2px]"
    >
      {tabs.map((tab) => {
        const selected = tab.key === active
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={tab.disabled === true}
            title={tab.disabled === true ? DISABLED_HINT : undefined}
            /* 禁用态的 click 在浏览器里不会触发，但测试与个别环境会直接派发事件，
               所以这里再守一道——回调不该收到一个"用户不可能做出"的动作。 */
            onClick={() => {
              if (tab.disabled === true) return
              onChange(tab.key)
            }}
            className={cx(
              'rounded-sm px-a12 py-a4 text-caption transition-colors duration-fast ease-standard',
              selected ? 'bg-card text-ink shadow-1' : 'text-ink-muted hover:text-ink',
              tab.disabled === true && 'cursor-not-allowed opacity-45',
            )}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
