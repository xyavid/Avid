/** @type {import('tailwindcss').Config} */
/*
 * Tailwind 侧**只做名字绑定**：值全部来自 web/src/styles/tokens.css 的 CSS 变量。
 * 这里不写字面量色值、不写像素——否则 token 就有两个来源，换肤会漏掉一半。
 *
 * 绑定形态：`'<alpha-value>'` 让 `text-ink/70` 这类透明度修饰可用。
 * 变量本身存的是 `R G B` 三元组（不是 hex），所以 `rgb(var(--x) / <alpha-value>)`
 * 才能合成——这是 token 文件里"颜色以三元组给出"那条规则的用途。
 */

/** @param {string} name token 名（不含 --avid- 前缀） */
const rgbVar = (name) => `rgb(var(--avid-${name}-rgb) / <alpha-value>)`

const space = {
  a2: 'var(--avid-space-2)',
  a4: 'var(--avid-space-4)',
  a6: 'var(--avid-space-6)',
  a8: 'var(--avid-space-8)',
  a10: 'var(--avid-space-10)',
  a12: 'var(--avid-space-12)',
  a16: 'var(--avid-space-16)',
  a24: 'var(--avid-space-24)',
  a32: 'var(--avid-space-32)',
  a40: 'var(--avid-space-40)',
}

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        /* 底：纸面 → 抬升 → 侧栏 → 凹陷 */
        canvas: rgbVar('bg'),
        deep: rgbVar('bg-deep'),
        card: rgbVar('card'),
        float: rgbVar('float'),
        inset: rgbVar('inset'),
        /* 字：暖墨三档 + 极淡档 */
        ink: rgbVar('text'),
        'ink-light': rgbVar('text-light'),
        'ink-muted': rgbVar('text-muted'),
        'ink-faint': rgbVar('text-faint'),
        /* 唯一彩色 */
        accent: rgbVar('accent'),
        'accent-hover': rgbVar('accent-hover'),
        'accent-soft': rgbVar('accent-soft'),
        /* 发丝线 */
        hair: rgbVar('border'),
        'hair-strong': rgbVar('border-strong'),
        /* 语义 */
        ok: rgbVar('ok'),
        warn: rgbVar('warn'),
        danger: rgbVar('danger'),
        info: rgbVar('info'),
        'ok-bg': rgbVar('ok-bg'),
        'warn-bg': rgbVar('warn-bg'),
        'danger-bg': rgbVar('danger-bg'),
        'info-bg': rgbVar('info-bg'),
        /* 面：气泡与代码 */
        'user-bubble': rgbVar('user-bg'),
        'tool-bg': rgbVar('tool-bg'),
        'code-bg': rgbVar('code-bg'),
      },
      spacing: space,
      gap: space,
      borderRadius: {
        xs: 'var(--avid-radius-xs)',
        sm: 'var(--avid-radius-sm)',
        md: 'var(--avid-radius-md)',
        lg: 'var(--avid-radius-lg)',
        full: 'var(--avid-radius-full)',
      },
      borderWidth: {
        hair: 'var(--avid-stroke-hair)',
        thin: 'var(--avid-stroke-thin)',
      },
      fontSize: {
        title: ['var(--avid-fs-title)', { lineHeight: 'var(--avid-lh-tight)' }],
        body: ['var(--avid-fs-body)', { lineHeight: 'var(--avid-lh-normal)' }],
        ui: ['var(--avid-fs-ui)', { lineHeight: 'var(--avid-lh-normal)' }],
        caption: ['var(--avid-fs-caption)', { lineHeight: 'var(--avid-lh-tight)' }],
        hint: ['var(--avid-fs-hint)', { lineHeight: 'var(--avid-lh-tight)' }],
        micro: ['var(--avid-fs-micro)', { lineHeight: 'var(--avid-lh-tight)' }],
      },
      fontFamily: {
        ui: 'var(--avid-font-ui)',
        mono: 'var(--avid-font-mono)',
      },
      boxShadow: {
        '1': 'var(--avid-shadow-1)',
        '2': 'var(--avid-shadow-2)',
        '3': 'var(--avid-shadow-3)',
      },
      transitionDuration: {
        instant: 'var(--avid-duration-instant)',
        fast: 'var(--avid-duration-fast)',
        slow: 'var(--avid-duration-slow)',
      },
      transitionTimingFunction: {
        out: 'var(--avid-ease-out)',
        in: 'var(--avid-ease-in)',
        standard: 'var(--avid-ease-standard)',
        smooth: 'var(--avid-ease-smooth)',
      },
      maxWidth: {
        'chat-col': 'var(--avid-chat-col-w)',
      },
      zIndex: {
        base: 'var(--avid-z-base)',
        sticky: 'var(--avid-z-sticky)',
        drawer: 'var(--avid-z-drawer)',
        dropdown: 'var(--avid-z-dropdown)',
        overlay: 'var(--avid-z-overlay)',
        modal: 'var(--avid-z-modal)',
        tooltip: 'var(--avid-z-tooltip)',
      },
      keyframes: {
        /* 纯透明度：用于一切"出现/消失" */
        'paper-fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'paper-fade-out': { from: { opacity: '1' }, to: { opacity: '0' } },
        /* 透明度 + 微位移：位移量刻意小（4px），不弹跳 */
        'paper-fade-up': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        /* 模态/浮层入场：缩放到 1，**不超出**原始尺寸，避免"弹"的观感 */
        'paper-scale-in': {
          from: { opacity: '0', transform: 'scale(0.97) translateY(4px)' },
          to: { opacity: '1', transform: 'scale(1) translateY(0)' },
        },
        /* 流式尾部：文字逐段到位时极轻的淡入，不用光标闪 */
        'paper-stream-tail': { from: { opacity: '0.35' }, to: { opacity: '1' } },
        /* 呼吸灯：只动透明度，不动 scale */
        'paper-pulse': { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
        'paper-spin': { from: { transform: 'rotate(0deg)' }, to: { transform: 'rotate(360deg)' } },
      },
      animation: {
        'fade-in': 'paper-fade-in var(--avid-duration-fast) var(--avid-ease-out) both',
        'fade-out': 'paper-fade-out var(--avid-duration-instant) var(--avid-ease-in) both',
        'fade-up': 'paper-fade-up var(--avid-duration-fast) var(--avid-ease-out) both',
        'scale-in': 'paper-scale-in var(--avid-duration-slow) var(--avid-ease-smooth) both',
        'stream-tail': 'paper-stream-tail var(--avid-duration-fast) var(--avid-ease-out) both',
        pulse: 'paper-pulse 1.6s var(--avid-ease-standard) infinite',
        spin: 'paper-spin 0.9s linear infinite',
      },
    },
  },
  plugins: [],
}
