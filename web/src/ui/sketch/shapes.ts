/**
 * 手绘形状轮换（§8.1 ①）：相邻**同级**卡片依次取 1 → 2 → 3，禁止连续两张同形。
 *
 * 抽成一处是因为它同时被时间线条目卡与任务卡用到——参照实现里那三个圆角常量被复制到
 * 9 个文件各一份，正是「真实重复」的教科书案例。
 */
export const SKETCH_SHAPES = ['shape-1', 'shape-2', 'shape-3'] as const

/** 取模容错：负数与 NaN 都回落到第一个形状，而不是渲染出 undefined。 */
export function shapeFor(index: number): string {
  const safe = Number.isFinite(index) ? Math.abs(Math.trunc(index)) : 0
  return SKETCH_SHAPES[safe % SKETCH_SHAPES.length] ?? SKETCH_SHAPES[0]
}
