/**
 * 应用装配根（阶段 1）：目前只有骨架，没有路由与状态域。
 * surfaces/conversation（阶段 4）将作为第一个表面嵌进 AppShell 的主区与右栏。
 */

import { AppShell } from './AppShell'

export function App() {
  return <AppShell />
}
