/** @type {import('tailwindcss').Config} */
// 阶段 32 清空了旧前端的全部设计 token：色板 / 圆角 / 阴影 / 字族 / z 层级 / 断点
// 一并删除（它们绑的是已删的 src/ui/tokens.css）。
//
// 新视觉语言定下来之前这里**只留扫描范围**，不写任何字面量，也不预先发明 token 名——
// 名字一旦写进来就会有人用，用了就得有值，于是视觉方案会在讨论之前被这五个绑定定死。
// 新语言落地时的约定不变：token 的唯一来源是一份 CSS 变量表，这里只做名字绑定。
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
}
