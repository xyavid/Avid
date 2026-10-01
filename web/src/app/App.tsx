/**
 * 应用装配根（阶段 4）：默认渲染对话表面；?gallery=1 打开组件墙速查板。
 */

import { ConversationPage } from '../surfaces/conversation/ConversationPage'
import { GalleryPage } from '../ui/gallery/GalleryPage'

export function App() {
  if (new URLSearchParams(window.location.search).has('gallery')) {
    return <GalleryPage />
  }
  return <ConversationPage />
}
