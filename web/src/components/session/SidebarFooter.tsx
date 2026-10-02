/**
 * 侧栏底栏（阶段 33 · 阶段 8）：设置入口从顶栏挪到侧栏最底部，**单开一栏**——
 * 顶发丝线 + 独立留白把它和上面的会话列表分开，`mt-auto` 让它贴住栏底。
 *
 * 为什么不留一个图标按钮悬在顶栏右上角：那一栏属于「当前在哪个项目/会话」的语境，
 * 而设置是全局动作；放到会话选择栏的最底部，语境与操作分层，顶栏也能只留标识与字标。
 *
 * 与 `SessionItem` 同一套度量（px-[9px] py-[7px]、圆角 sm、hover 走 accent 浅底，
 * 见组件墙「会话列表」一节），但它是栏里唯一的整宽按钮，不是列表项。
 */

import { Icon } from '../../ui/Icon'

export type SidebarFooterProps = {
  /** 打开设置界面。底栏只触发，开关状态归装配层（对话表面）。 */
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
        <Icon name="settings" size={14} />
        <span>设置</span>
      </button>
    </div>
  )
}
