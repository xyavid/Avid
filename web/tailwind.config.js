/** @type {import('tailwindcss').Config} */
// 设计 token 的**唯一来源**是 src/ui/tokens.css：这里只做名字绑定，
// 不写任何字面量颜色/阴影/圆角。于是 §8.2 的「换主题」和 §8.6 的「整体缩放」
// 都只需要改那一个文件。
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // 设计文档 §8.6 的三档断点：宽 ≥1280、中 960–1279、窄 <960。
      // 用 extend 是为了保留 Tailwind 自带的 sm/md/lg，同时多出 mid/wide。
      screens: {
        mid: '960px',
        wide: '1280px',
      },
      colors: {
        paper: 'rgb(var(--avid-paper-rgb) / <alpha-value>)',
        card: 'rgb(var(--avid-card-rgb) / <alpha-value>)',
        sand: 'rgb(var(--avid-sand-rgb) / <alpha-value>)',
        input: 'rgb(var(--avid-input-rgb) / <alpha-value>)',
        ink: 'rgb(var(--avid-ink-rgb) / <alpha-value>)',
        accent: 'rgb(var(--avid-accent-rgb) / <alpha-value>)',
        ok: 'rgb(var(--avid-ok-rgb) / <alpha-value>)',
        warn: 'rgb(var(--avid-warn-rgb) / <alpha-value>)',
        danger: 'rgb(var(--avid-danger-rgb) / <alpha-value>)',
        info: 'rgb(var(--avid-info-rgb) / <alpha-value>)',
        mark: 'rgb(var(--avid-mark-rgb) / <alpha-value>)',
        'ok-bg': 'rgb(var(--avid-ok-bg-rgb) / <alpha-value>)',
        'warn-bg': 'rgb(var(--avid-warn-bg-rgb) / <alpha-value>)',
        'danger-bg': 'rgb(var(--avid-danger-bg-rgb) / <alpha-value>)',
        'info-bg': 'rgb(var(--avid-info-bg-rgb) / <alpha-value>)',
        'term-bg': 'rgb(var(--term-bg-rgb) / <alpha-value>)',
        'term-fg': 'rgb(var(--term-fg-rgb) / <alpha-value>)',
        // 次级文字的唯一入口：值本身是 rgb(...)，不吃 <alpha-value>。
        'ink-muted': 'var(--avid-ink-muted)',
      },
      borderRadius: {
        chip: 'var(--r-chip)',
        face: 'var(--r-face)',
        card: 'var(--r-card)',
        panel: 'var(--r-panel)',
        pill: 'var(--r-pill)',
      },
      boxShadow: {
        'lift-1': 'var(--lift-1)',
        'lift-2': 'var(--lift-2)',
        'lift-3': 'var(--lift-3)',
      },
      borderWidth: {
        hair: 'var(--stroke-hair)',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      zIndex: {
        base: 'var(--z-base)',
        sticky: 'var(--z-sticky)',
        drawer: 'var(--z-drawer)',
        modal: 'var(--z-modal)',
        toast: 'var(--z-toast)',
        shell: 'var(--z-shell-chrome)',
      },
      minHeight: {
        control: 'var(--avid-control-height)',
      },
      minWidth: {
        control: 'var(--avid-control-height)',
      },
      height: {
        control: 'var(--avid-control-height)',
      },
      width: {
        control: 'var(--avid-control-height)',
      },
    },
  },
  plugins: [],
}
