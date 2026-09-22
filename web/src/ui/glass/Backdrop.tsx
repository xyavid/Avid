import type { CSSProperties } from 'react'

/**
 * 页面背景：光斑网格。
 *
 * 纯 CSS 的四道径向渐变（值在 `ui/tokens.css` 的 `--avid-blob-*`）。为什么要有背景变化：
 * **模糊只能模糊背景里已有的东西**——纯平底色上，玻璃只剩高光边与投影能被看出来，
 * 「液态」的那点折射错觉必须由背景的明暗变化提供。这也是参照物的做法（它用一幅插画，
 * 我们用纯 CSS 顶替：插画不进仓，见阶段 23b 的取舍记录）。
 *
 * 装饰层：`aria-hidden` 且 `pointer-events: none`，不进无障碍树、不吃点击。
 *
 * `art` 是用户在本机选的背景插画（data URL）。**由 L3 以 props 传入，这一层不读 store**：
 * L1 只接受 props 是分层声明（§3.4 规则 5），`check:layers` 会拦。
 * 插画**不进仓**（`budget.json` 的 `texture_bytes` 仍是 0），所以要有一个运行期入口。
 */
export interface BackdropProps {
  /** 背景插画的 data URL；null / 省略 = 只用 CSS 光斑。 */
  art?: string | null
}

export function Backdrop({ art }: BackdropProps = {}) {
  return (
    <>
      <div className="app-backdrop" aria-hidden="true" />
      {art ? (
        <div
          className="app-backdrop-art"
          aria-hidden="true"
          // 自定义属性只能内联给（值来自用户选择，不写进 token）。
          style={{ '--art': `url("${art}")` } as CSSProperties}
        />
      ) : null}
    </>
  )
}
