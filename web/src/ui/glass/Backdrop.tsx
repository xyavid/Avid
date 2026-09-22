/**
 * 页面背景：光斑网格。
 *
 * 纯 CSS 的四道径向渐变（值在 `ui/tokens.css` 的 `--avid-blob-*`）。为什么要有背景变化：
 * **模糊只能模糊背景里已有的东西**——纯平底色上，玻璃只剩高光边与投影能被看出来，
 * 「液态」的那点折射错觉必须由背景的明暗变化提供。这也是参照物的做法（它用一幅插画，
 * 我们用纯 CSS 顶替：插画不进仓，见阶段 23b 的取舍记录）。
 *
 * 装饰层：`aria-hidden` 且 `pointer-events: none`，不进无障碍树、不吃点击。
 */
export function Backdrop() {
  return <div className="app-backdrop" aria-hidden="true" />
}
