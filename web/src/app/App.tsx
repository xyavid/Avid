/** App assembly root: conversation surface by default, gallery page with `?gallery=1`. */

import { ConversationPage } from '../surfaces/conversation/ConversationPage'
import { GalleryPage } from '../ui/gallery/GalleryPage'

export function App() {
  if (new URLSearchParams(window.location.search).has('gallery')) {
    return <GalleryPage />
  }
  return <ConversationPage />
}
