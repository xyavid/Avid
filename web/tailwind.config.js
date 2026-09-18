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
      },
      borderRadius: {
        'sketch-1': 'var(--sketch-r1)',
        'sketch-2': 'var(--sketch-r2)',
        'sketch-3': 'var(--sketch-r3)',
        chip: 'var(--sketch-r-chip)',
        blob: 'var(--sketch-r-blob)',
      },
      boxShadow: {
        'sticker-1': 'var(--sticker-1)',
        'sticker-2': 'var(--sticker-2)',
        'sticker-3': 'var(--sticker-3)',
        'sticker-4': 'var(--sticker-4)',
        'sticker-6': 'var(--sticker-6)',
        'sticker-8': 'var(--sticker-8)',
        'sticker-12': 'var(--sticker-12)',
        'sticker-16': 'var(--sticker-16)',
        'sticker-inset-2': 'var(--sticker-inset-2)',
        'sticker-inset-4': 'var(--sticker-inset-4)',
        'sticker-accent-4': 'var(--sticker-accent-4)',
        'sticker-accent-6': 'var(--sticker-accent-6)',
      },
      borderWidth: {
        hair: 'var(--stroke-hair)',
        bold: 'var(--stroke-bold)',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
        sketch: 'var(--font-sketch)',
      },
      rotate: {
        tilt: 'var(--tilt-1)',
        'tilt-2': 'var(--tilt-2)',
        'tilt-6': 'var(--tilt-6)',
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
