/** Browser entry: ships fonts, loads styles, mounts the app (assembly lives in src/app/). */

// Subsets: latin + latin-ext only (CJK falls back to system); full subsets bloat dist.
import '@fontsource/eb-garamond/latin-400.css'
import '@fontsource/eb-garamond/latin-ext-400.css'
import '@fontsource/eb-garamond/latin-500.css'
import '@fontsource/eb-garamond/latin-ext-500.css'
import '@fontsource/pt-serif/latin-400.css'
import '@fontsource/pt-serif/latin-ext-400.css'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-ext-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-ext-500.css'
import '@fontsource/jetbrains-mono/latin-400.css'
import '@fontsource/jetbrains-mono/latin-ext-400.css'

import './styles/index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './app/App'
import { initAppearance } from './state/appearance'

// Apply the stored theme before mounting to avoid a light-then-dark flash.
initAppearance()

const container = document.getElementById('root')
if (container === null) {
  // Silent failure would turn a blank page into a console-only diagnosis.
  throw new Error('#root 不存在：index.html 的挂载点与入口对不上')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
