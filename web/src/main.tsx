import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
// 顺序有意义：构件类先，tokens（含 @tailwind utilities）后，工具类才能覆盖构件类。
import './ui/sketch.css'
import './ui/tokens.css'

const container = document.getElementById('root')
if (!container) {
  throw new Error('缺少 #root 容器')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
