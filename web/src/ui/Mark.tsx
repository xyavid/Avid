/**
 * Avid 标识（阶段 33 · 阶段 7）——荷花（等距风）。
 *
 * 与 `Icon.tsx` 是两类东西，别混用：
 *   · `Icon`：24 视框**线性** stroke 图标（1.5 线宽、fill none、有使用点才登记）；
 *   · `AvidMark`：一幅**自带配色的插画**（用户提供的描摹件原样搬运），只出图案，
 *     不参与换肤、也不随 currentColor 变色——它有自己的颜色，这是使用者的决定。
 *
 * **为什么是 <img> 而不是内联 path**：这幅画有 431 条路径、约 47 KB，内联进组件会把它
 * 塞进首屏 JS 包；`<img>` 让 Vite 把它当静态资源单独发出（`assetsInlineLimit: 0`），
 * 首屏 JS 只多一个 URL 字符串，且浏览器能缓存这份图。
 *
 * **落位纪律**：不做圆托、不垫色板——用户要求直接贴在页面上、背景透明。
 * 尺寸由调用点给（顶栏 22 / 助手头像 24 / 欢迎态 96）。
 *
 * **两处副本**：`src/assets/avid-mark.svg`（本组件用的）与 `public/favicon.svg`
 * （多一块纸底，否则浅色花瓣在浅色标签栏里看不见）。两边是同一幅画，
 * 由 `Mark.test.tsx` 逐字对账，只改一边会当场报错。
 */

import markUrl from '../assets/avid-mark.svg'

export type AvidMarkProps = {
  /** 边长（px）。 */
  size?: number
  className?: string
}

export function AvidMark({ size = 24, className }: AvidMarkProps) {
  return (
    <img
      src={markUrl}
      width={size}
      height={size}
      alt=""
      aria-hidden
      draggable={false}
      className={className}
    />
  )
}
