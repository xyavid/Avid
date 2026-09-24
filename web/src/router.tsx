import { Navigate, createBrowserRouter } from 'react-router-dom'

import { ConversationRoute } from './routes/ConversationRoute'
import { RootLayout } from './routes/RootLayout'
import { SettingsRoute } from './routes/SettingsRoute'
import { SkillsRoute } from './routes/SkillsRoute'

/**
 * URL ↔ feature 组合。导航有两种（§8.4）：**换工作面的走路由**（任务板 / 技能 / 设置；
 * 会话列表常驻导航列，所以「会话」不是导航项，`/sessions` 只作为根路径与未知路径的落点），
 * 同一个工作面里的子面板就地切换（检查器的三个视图不进 URL）。
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      { index: true, element: <Navigate to="/sessions" replace /> },
      { path: 'sessions', element: <ConversationRoute /> },
      { path: 'sessions/:sessionId', element: <ConversationRoute /> },
      { path: 'skills', element: <SkillsRoute /> },
      { path: 'settings', element: <SettingsRoute /> },
      { path: '*', element: <Navigate to="/sessions" replace /> },
    ],
  },
])
