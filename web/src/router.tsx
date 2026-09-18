import { Navigate, createBrowserRouter } from 'react-router-dom'

import { ConversationRoute } from './routes/ConversationRoute'
import { RootLayout } from './routes/RootLayout'
import { SettingsRoute } from './routes/SettingsRoute'
import { SkillsRoute } from './routes/SkillsRoute'
import { TaskBoardRoute } from './routes/TaskBoardRoute'

/**
 * URL ↔ feature 组合。导航有两种（§8.4）：**换工作面的走路由**（会话 / 任务板 /
 * 技能 / 设置），同一个工作面里的子面板就地切换（检查器的三个视图不进 URL）。
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      { index: true, element: <Navigate to="/sessions" replace /> },
      { path: 'sessions', element: <ConversationRoute /> },
      { path: 'sessions/:sessionId', element: <ConversationRoute /> },
      { path: 'tasks', element: <TaskBoardRoute /> },
      { path: 'skills', element: <SkillsRoute /> },
      { path: 'settings', element: <SettingsRoute /> },
      { path: '*', element: <Navigate to="/sessions" replace /> },
    ],
  },
])
