/**
 * 检查器的定位外壳。
 *
 * 为什么要一层外壳：`Inspector` 只负责内容（页签、空态），**它不该知道自己是常驻
 * 一栏还是浮层**。定位方式由本文件按视口决定，于是"窄屏下检查器变浮层"这件事
 * 只有一个落点，而不是散在组件内部的媒体查询里。
 *
 * 浮层形态的三条讲究：
 *   · 遮罩用 scrim token（`--avid-scrim-15`），**不是阴影**——报告 §2.7 明确禁止
 *     拿 scrim 做 box-shadow，反过来也一样；
 *   · 面板是**实底**（`bg-card`），不是毛玻璃——glassmorphism 在 §9 的硬黑名单里；
 *   · 浮层只遮右侧、从顶部条之下开始，`transition` 走 `duration-slow`（大块进场用慢档）。
 */

import { Inspector } from '../features/inspector'
import type { InspectorSelection, InspectorTab } from '../features/inspector'

export interface InspectorSlotProps {
  /** true = 常驻右栏（由外壳给宽度）；false = 右侧浮层。 */
  inline: boolean
  selection: InspectorSelection | null
  tab: InspectorTab
  onTabChange: (tab: InspectorTab) => void
  onClose: () => void
}

export function InspectorSlot({
  inline,
  selection,
  tab,
  onTabChange,
  onClose,
}: InspectorSlotProps) {
  /*
   * 没有选中项时**什么都不渲染**：检查器是"查看某次工具调用"的从属面板，
   * 空着的一栏会让人以为那里本该有内容。关闭动作由它自己的按钮发起，
   * 所以这里不额外给关闭态。
   */
  if (selection === null) return null

  if (inline) {
    return (
      <Inspector selection={selection} tab={tab} onTabChange={onTabChange} onClose={onClose} />
    )
  }

  return (
    <div className="fixed inset-0 z-overlay flex justify-end">
      {/* 遮罩：点击关闭。`aria-hidden` + 无文本——它是纯装饰性点击区。 */}
      <div
        aria-hidden="true"
        className="flex-1 bg-[var(--avid-scrim-15)]"
        onClick={onClose}
      />
      <div className="h-full w-[380px] border-l-hair bg-card shadow-2">
        <Inspector selection={selection} tab={tab} onTabChange={onTabChange} onClose={onClose} />
      </div>
    </div>
  )
}
