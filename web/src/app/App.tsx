/** App assembly root: conversation surface by default, gallery page with `?gallery=1`. */

import { lazy, Suspense } from 'react'

import { ConversationPage } from '../surfaces/conversation/ConversationPage'

/** The gallery is a dev-only surface (`?gallery=1`) that no interface links to: keep it off first paint. */
const GalleryPage = lazy(() =>
  import('../ui/gallery/GalleryPage').then((m) => ({ default: m.GalleryPage })),
)

export function App() {
  if (new URLSearchParams(window.location.search).has('gallery')) {
    return (
      <Suspense fallback={null}>
        <GalleryPage />
      </Suspense>
    )
  }
  return <ConversationPage />
}
