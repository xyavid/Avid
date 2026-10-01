/** 浏览器入口：只做挂载，不做装配（路由、状态域、Provider 等新结构定了再说）。 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import './index.css'
import { App } from './App'

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
