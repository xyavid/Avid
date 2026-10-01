/**
 * 富文本渲染层的统一出口。
 *
 * 与 `ui/primitives`、`ui/icons` 同一手法：调用方只 import 这个文件，
 * 目录内部怎么分（组件 / 扩展插件）是实现细节。
 */

export { Markdown } from './Markdown'
export type { MarkdownProps } from './Markdown'
