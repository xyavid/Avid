/*
 * JsonView：参数原文。
 *
 * 为什么不显式做语法高亮：这一页的回答是"这个工具到底收到了什么参数"，
 * 2 空格缩进 + mono 字体已经足够读；引一个高亮库要多背一份体积，
 * 而高亮在纸本主题下还会引入第二种彩色（报告 §9 里"多彩堆砌"是硬禁区）。
 * 真需要高亮时再评估，眼下这是显式的范围取舍。
 *
 * 不可序列化的输入（循环引用 / BigInt）必须回落到 `String(value)`：
 * 组件抛异常会把整个检查器面板一起带走，那是"因为一个丑参数白屏"。
 */

import type { ReactElement } from 'react'

import { cx } from '../../../ui/primitives'

export interface JsonViewProps {
  value: unknown
  className?: string
}

function stringify(value: unknown): string {
  try {
    // JSON.stringify(undefined, ...) 返回 undefined 而不是字符串，这一类也一并兜住。
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}

export function JsonView({ value, className }: JsonViewProps): ReactElement {
  return (
    <pre
      className={cx(
        'whitespace-pre p-a8 font-mono text-hint leading-normal text-ink-muted',
        className,
      )}
    >
      {stringify(value)}
    </pre>
  )
}
