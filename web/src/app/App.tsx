/**
 * 应用装配根（阶段 2）：骨架 + 组件墙速查板（?gallery=1）。
 * surfaces/conversation（阶段 4）将作为第一个表面嵌进 AppShell 的主区与右栏。
 */

import { AppShell } from './AppShell'
import { GalleryPage } from '../ui/gallery/GalleryPage'

export function App() {
  if (new URLSearchParams(window.location.search).has('gallery')) {
    return <GalleryPage />
  }
  return <AppShell />
}
