/*
 * className 合并：**只有一个函数**，不引 clsx / tailwind-merge。
 *
 * 为什么不引依赖：这里只需要"拼接 + 丢掉假值"，没有冲突消解需求。一旦引入
 * tailwind-merge，就会开始"猜"哪个类该赢，把排序责任从代码搬到库的启发式里，
 * 排查样式问题时要同时读两层逻辑。十几行的确定性版本更划算。
 *
 * ⚠ 一处与直觉相反的事实（实测得出，写代码前先读这段）：
 * **参数顺序不决定胜负。** `cx` 只做字符串拼接，类名在 DOM 上的先后与 CSS
 * 优先级无关；同权重下真正决定胜负的是 **Tailwind 产出 CSS 的顺序**，
 * 而那个顺序是**按类名字母序**排的——既与配置里的书写顺序无关，
 * 也与 className 里的位置无关。
 *
 * 两个由此而来的具体坑（都实际踩过）：
 *   1. `cx('border-hair', className)` 里调用方传 `border-danger/40` **不会**覆盖：
 *      `.border-danger/40` 恒排在 `.border-hair` 之前。要覆盖同轴属性，得用一个
 *      **字典序在后**的具名 token（如 `border-state-danger` > `border-hair`）。
 *   2. `border-t-hair border-hair` **不是**"一条顶线"：`.border-hair` 的
 *      `border-width: 0.5px` 先出，`.border-t-hair` 只覆盖上边，于是留下**四边整框**。
 *      要做一条顶线，线宽交给 `border-t-hair` 之外不要再给 `border-hair` 的宽度，
 *      颜色用 `border-t-*` 单独覆。
 *
 * 详见 styles/tokens.css 与 ui/primitives/Card.tsx 的对应注释。
 */

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter((part): part is string => Boolean(part)).join(' ')
}
