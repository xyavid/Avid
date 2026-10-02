/** 浏览器入口：字体打包 + 样式装配 + 挂载。装配逻辑在 src/app/。 */

/* 字体（报告 §5）：woff2 随包分发，字重只用 400/500（PT Serif 无 500，暂只装 400）。
   CJK 衬线走系统回退栈（--font-* 里已列）。子集只装 latin + latin-ext：界面与正文
   实际命中的就这两档（中文走系统回退），默认的全子集会把西里尔/希腊/越南文各
   几十 KB 也带进产物——门禁量的是 dist，那一大块永远是死重。 */
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

// 挂载前先按存储的偏好定主题，避免先亮后暗的闪烁。
initAppearance()

const container = document.getElementById('root')
if (container === null) {
  // 静默失败会让"白屏"变成一个要翻控制台才能定位的问题。
  throw new Error('#root 不存在：index.html 的挂载点与入口对不上')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
