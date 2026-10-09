/**
 * Sidebar footer: settings is its own column pinned to the bottom (`mt-auto`) behind a hairline
 * top border, not another session-list row, so the blank space around the button stays inert.
 * It shares `SessionItem`'s metrics (px-[9px] py-[7px], rounded-sm, accent-light hover) but is the
 * column's only full-width button; it only fires, open state belongs to the assembly layer.
 */

import { Icon } from '../../ui/Icon'

export type SidebarFooterProps = {
  /** Open settings; the footer only fires, open state belongs to the assembly layer. */
  onOpenSettings: () => void
}

export function SidebarFooter({ onOpenSettings }: SidebarFooterProps) {
  return (
    <div className="mt-auto border-t border-hair pt-a8">
      <button
        type="button"
        onClick={onOpenSettings}
        className="flex w-full items-center gap-a8 rounded-sm px-[9px] py-[7px] font-ui text-ui text-ink transition-colors duration-fast ease-out hover:bg-accent-light"
      >
        {/* 16px: the gear glyph is small in its viewBox; 14px reads lighter than the label. */}
        <Icon name="settings" size={16} />
        <span>设置</span>
      </button>
    </div>
  )
}
