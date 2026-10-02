/** @type {import('tailwindcss').Config} */
/*
 * Tailwind 侧**只做名字绑定**：值全部来自 src/styles/tokens.css 的 CSS 变量，
 * 这里不写字面量色值、不写像素——否则 token 就有两个来源，换肤会漏掉一半。
 *
 * 绑定形态：需要 /alpha 修饰的颜色走 `--x-rgb` 三元组（`rgb(var(--x) / <alpha-value>)`）；
 * 发丝线（固定 0.18）与 accent-light 等预制色直接引用原始变量，不支持 /alpha——
 * 它们的透明度是设计决定，不该在调用点随手改。
 *
 * 间距键名 a2…a40 有意**不占用** Tailwind 默认数字刻度：默认 `p-4` 是 16px，
 * 而本项目的 `--space-4` 是 4px。键名错开让「写错刻度」变成可见的类名错误，
 * 而不是静默的 4 倍间距。
 */

/** @param {string} name token 名（不含 -- 前缀，指三元组镜像变量） */
const rgb = (name) => `rgb(var(--${name}-rgb) / <alpha-value>)`
/** @param {string} name token 名（不含 -- 前缀） */
const v = (name) => `var(--${name})`

const space = {
  a2: v('space-2'),
  a4: v('space-4'),
  a6: v('space-6'),
  a8: v('space-8'),
  a10: v('space-10'),
  a12: v('space-12'),
  a16: v('space-16'),
  a24: v('space-24'),
  a32: v('space-32'),
  a40: v('space-40'),
}

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        /* 底：纸面 → 抬升 → 侧栏 */
        paper: rgb('bg'),
        card: rgb('bg-card'),
        sidebar: rgb('sidebar-bg'),
        /* 叠层四档：纸面上的轻压痕（图标 hover 底、胶囊容器底） */
        overlay: {
          subtle: v('overlay-subtle'),
          light: v('overlay-light'),
          medium: v('overlay-medium'),
          strong: v('overlay-strong'),
        },
        /* 字：暖墨三档 */
        ink: rgb('text'),
        'ink-light': rgb('text-light'),
        'ink-muted': rgb('text-muted'),
        /* 发丝线（固定透明度，不支持 /alpha） */
        hair: v('border'),
        /* 唯一强调色「远山青」 */
        accent: rgb('accent'),
        'accent-hover': v('accent-hover'),
        'accent-light': v('accent-light'),
        /* 语义色：低饱和 */
        ok: rgb('green'),
        coral: rgb('coral'),
        danger: rgb('danger'),
        /* 代码高亮四色（固定值，不做 /alpha 修饰） */
        syntax: {
          comment: v('syntax-comment'),
          keyword: v('syntax-keyword'),
          string: v('syntax-string'),
          number: v('syntax-number'),
        },
      },
      fontFamily: {
        ui: v('font-ui'),
        serif: v('font-serif'),
        'serif-text': v('font-serif-text'),
        mono: v('font-mono'),
      },
      fontSize: {
        title: v('fs-title'),
        body: v('fs-body'),
        ui: v('fs-ui'),
        caption: v('fs-caption'),
        hint: v('fs-hint'),
        micro: v('fs-micro'),
        chat: v('chat-message-font-size'),
      },
      spacing: space,
      borderWidth: {
        /* 发丝线宽度：与 border-hair（颜色）分属两个属性，可安全组合 */
        hairline: v('hairline-width'),
      },
      borderRadius: {
        /* 覆盖默认刻度：rounded-sm/md/lg 从此等于 token（5/8/12px） */
        xs: v('radius-xs'),
        sm: v('radius-sm'),
        md: v('radius-md'),
        lg: v('radius-lg'),
        input: v('radius-input'),
        card: v('radius-card'),
        'chat-card': v('radius-chat-card'),
        'chat-surface': v('radius-chat-surface'),
      },
      width: {
        sidebar: v('sidebar-width'),
        rail: v('channel-inspector-width'),
      },
      maxWidth: {
        chat: v('chat-column-width'),
        'chat-input': v('chat-input-column-width'),
      },
      height: {
        titlebar: v('titlebar-h'),
        control: v('control-h'),
      },
      boxShadow: {
        /* focus 光晕（组件墙：边框转 accent + 2px accent-light 光晕） */
        'focus-ring': '0 0 0 2px var(--accent-light)',
        /* 微阴影（组件墙：标签页选中块 / 工具卡，量化自 --shadow token） */
        soft: '0 1px 3px var(--shadow)',
      },
      transitionDuration: {
        instant: v('duration-instant'),
        fast: v('duration-fast'),
        slow: v('duration-slow'),
      },
      transitionTimingFunction: {
        /* 覆盖 ease-out/in：从此等于 token 曲线 */
        out: v('ease-out'),
        in: v('ease-in'),
        standard: v('ease-standard'),
        smooth: v('ease-smooth'),
      },
    },
  },
  plugins: [],
}
