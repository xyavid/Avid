import { useState } from 'react'
import type { ReactNode } from 'react'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'

import { Backdrop } from '../ui/glass'
import { Button } from '../ui/primitives'
import { useTranslation } from '../lib/i18n'
import { useUiStore } from '../state/uiStore'
import { useViewport } from './useViewport'

export interface NavItem {
  key: string
  label: string
  to: string
  icon: ReactNode
}

export interface AppShellProps {
  /**
   * 会话列表。收一个 `close` 回调而不是直接收节点：列表在抽屉里被选中后必须
   * 自己把抽屉关掉，否则中档点完会话，75vh 的抽屉仍盖着刚选中的内容。
   */
  nav: (close: () => void) => ReactNode
  /** 路由内容：对话卡与检查器由 route 自己决定怎么摆（见 InspectorSlot）。 */
  children: ReactNode
  navItems: NavItem[]
  currentPath: string
  onNavigate: (to: string) => void
}

/** L3 骨架：纸张画布 + 三栏 + 断点降级 + 导航列收起/展开。只依赖界面域。 */
export function AppShell(props: AppShellProps) {
  const { t } = useTranslation()
  const { isWide, isMid } = useViewport()
  const navCollapsed = useUiStore((state) => state.navCollapsed)
  // 背景插画是界面域的偏好；L1 的 Backdrop 只接受 props（分层声明），所以在这里取。
  const backdropArt = useUiStore((state) => state.backdropArt)
  const toggleNav = useUiStore((state) => state.toggleNav)
  const [sheetOpen, setSheetOpen] = useState(false)
  const expanded = isWide && !navCollapsed

  const items = (withLabels: boolean) => (
    <ul className="flex flex-col gap-1">
      {props.navItems.map((item) => (
        <li key={item.key}>
          <Button
            size="sm"
            variant={props.currentPath === item.to ? 'primary' : 'secondary'}
            className="w-full justify-start"
            onClick={() => {
              props.onNavigate(item.to)
              setSheetOpen(false)
            }}
            aria-current={props.currentPath === item.to ? 'page' : undefined}
          >
            <span aria-hidden="true">{item.icon}</span>
            {withLabels ? <span>{item.label}</span> : <span className="sr-only">{item.label}</span>}
          </Button>
        </li>
      ))}
    </ul>
  )

  return (
    // 高度链的根：视口高度 + 不溢出。用 min-h-screen 时容器高度由内容决定，
    // 下面的 flex-1 / h-full 全部失去参照，于是消息区把整页撑高、输入条被推到
    // 视口之外（页面自己变成滚动容器）。h-dvh 在移动端地址栏伸缩时也用可视高度。
    <div className="relative h-dvh w-full overflow-hidden">
      <Backdrop art={backdropArt} />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-toast focus:rounded-chip focus:border-hair focus:border-ink/20 focus:bg-card focus:p-2 focus:shadow-lift-2"
      >
        {t('common.skipToContent')}
      </a>

      <div className="relative z-base mx-auto flex h-full min-h-0 w-full max-w-[1800px] gap-3 p-3">
        {isMid ? (
          <nav
            aria-label={t('common.appName')}
            className={`surface-sidebar flex shrink-0 flex-col gap-2 ${expanded ? 'w-80' : 'w-16'}`}
          >
            <div className={expanded ? 'flex items-center gap-2' : 'flex flex-col items-center gap-2'}>
              {expanded ? <span className="text-lg">{t('common.appName')}</span> : null}
              {isWide ? (
                // 收起态必须**竖排**：64px 轨道放不下「图标 + 文字」两个按钮并排，
                // 并排时文字按钮会溢出轨道、压到会话卡上（实测溢出 44px）。
                <Button
                  size={expanded ? 'sm' : 'icon'}
                  variant="secondary"
                  aria-expanded={expanded}
                  onClick={() => toggleNav()}
                >
                  <span aria-hidden="true">
                    {expanded ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
                  </span>
                  <span className={expanded ? undefined : 'sr-only'}>
                    {expanded ? t('common.collapse') : t('common.expand')}
                  </span>
                </Button>
              ) : null}
              {isMid && !isWide ? (
                // 只有中档需要这个入口：那里 `expanded` 恒为 false（它要 isWide），
                // 会话列表因此不挂载，只能靠抽屉。宽档不能渲染它——`isMid` 的语义是
                // 「≥960」，宽档同样成立，于是它会在「收起」旁边多出一枚图标按钮，
                // 点开只是把右侧已经可见的列表再盖一层（实测截图里就是这个）。
                <Button
                  size="icon"
                  variant="secondary"
                  aria-expanded={sheetOpen}
                  onClick={() => setSheetOpen(true)}
                >
                  <span aria-hidden="true">
                    <PanelLeftOpen size={16} />
                  </span>
                  <span className="sr-only">{t('common.nav.sessionList')}</span>
                </Button>
              ) : null}
            </div>
            {items(expanded)}
            <div className="min-h-0 flex-1">{expanded ? props.nav(() => setSheetOpen(false)) : null}</div>
          </nav>
        ) : null}

        {sheetOpen ? (
          <div className="surface-panel fixed inset-x-3 top-3 z-drawer flex h-[75vh] flex-col gap-2 p-3">
            <div className="flex items-center justify-between">
              <span className="text-lg">{t('common.appName')}</span>
              <Button size="sm" onClick={() => setSheetOpen(false)}>
                {t('common.close')}
              </Button>
            </div>
            {/* 中档的导航项已在左侧图标轨里，抽屉只补会话列表（因此约 10 行）。 */}
            {isMid ? null : items(true)}
            <div className="min-h-0 flex-1">{props.nav(() => setSheetOpen(false))}</div>
          </div>
        ) : null}

        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
          {!isMid ? (
            <div className="flex items-center gap-2">
              <span className="text-lg">{t('common.appName')}</span>
              <Button size="sm" onClick={() => setSheetOpen(true)}>
                {t('common.nav.toggleNav')}
              </Button>
            </div>
          ) : null}
          <main
            id="main"
            tabIndex={-1}
            className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 wide:flex-row"
          >
            {props.children}
          </main>
        </div>
      </div>
    </div>
  )
}
