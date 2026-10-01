/**
 * 会话搜索框（视觉依据：hana 报告 §7.2）。
 *
 * 刻意"无边框、顶部一条发丝线"：搜索不是一块独立控件，而是头部与分组列表之间的
 * 一道分缝；给它描边会把这一栏切成三块碎块。层级靠发丝线承担，阴影不参与分层。
 */

import type { ReactElement } from 'react'

import { SearchIcon } from '../../../ui/icons'

export interface NavSearchProps {
  value: string
  onChange: (next: string) => void
  placeholder?: string
}

export function NavSearch({
  value,
  onChange,
  placeholder = '搜索会话…',
}: NavSearchProps): ReactElement {
  return (
    <div className="avid-hair-t relative px-a8 py-a6">
      {/* 图标只是提示，不接事件：pointer-events-none 让点击穿透到输入框 */}
      <SearchIcon
        size={14}
        className="pointer-events-none absolute left-a16 top-1/2 -translate-y-1/2 text-ink-faint"
      />
      {/* 不用 focus:outline-none：全局 :focus-visible 的强调色描边是键盘可达性的落点，
          这里覆盖掉它只为了视觉，代价太大。 */}
      <input
        type="search"
        aria-label="搜索会话"
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-[30px] w-full rounded-sm bg-inset pl-a32 pr-a8 text-caption text-ink placeholder:text-ink-faint"
      />
    </div>
  )
}
