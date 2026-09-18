import { useCallback } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ListChecks, MessagesSquare, Settings, Sparkles } from 'lucide-react'

import { AppShell } from '../layouts/AppShell'
import type { NavItem } from '../layouts/AppShell'
import { SessionList } from '../features/sessions'
import { useTranslation } from '../lib/i18n'

/** L4：URL ↔ feature 组合。导航列需要「当前会话」，所以它读 URL 而不是 store。 */
export function RootLayout() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()

  const navItems: NavItem[] = [
    { key: 'sessions', label: t('common.nav.sessions'), to: '/sessions', icon: <MessagesSquare size={16} /> },
    { key: 'tasks', label: t('common.nav.tasks'), to: '/tasks', icon: <ListChecks size={16} /> },
    { key: 'skills', label: t('common.nav.skills'), to: '/skills', icon: <Sparkles size={16} /> },
    { key: 'settings', label: t('common.nav.settings'), to: '/settings', icon: <Settings size={16} /> },
  ]

  const activeId = location.pathname.startsWith('/sessions/')
    ? decodeURIComponent(location.pathname.slice('/sessions/'.length))
    : null

  const onSelect = useCallback(
    (sessionId: string) => navigate(`/sessions/${encodeURIComponent(sessionId)}`),
    [navigate],
  )

  return (
    <AppShell
      nav={<SessionList activeId={activeId} onSelect={onSelect} />}
      navItems={navItems}
      currentPath={location.pathname}
      onNavigate={(to) => navigate(to)}
    >
      <Outlet />
    </AppShell>
  )
}
