/*
 * 检查器（inspector）的公开面。
 *
 * `InspectorTab` 与 `InspectorSelection` 被上层状态（导航与页面组装）直接引用，
 * 所以它们和 `Inspector` 一样属于契约的一部分，命名不再改。
 */

export { DiffView } from './components/DiffView'
export type { DiffViewProps } from './components/DiffView'
export { Inspector } from './components/Inspector'
export type { InspectorProps, InspectorSelection, InspectorTab } from './components/Inspector'
export { JsonView } from './components/JsonView'
export type { JsonViewProps } from './components/JsonView'
export { diffLines, toUnified } from './lib/diff'
export type { DiffLine } from './lib/diff'
