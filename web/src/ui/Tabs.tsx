/**
 * 标签页（报告 §7.4：经典 sliding pill）。
 * 结构：胶囊容器（overlay-light 底 + radius-md）内浮着一块 bg-card 滑块，
 * useLayoutEffect 量取选中项的 offsetLeft/offsetWidth，滑块以
 * transform + width 过渡过去（duration-slow + ease-smooth，报告原文）。
 * 滑块在文字层之下（z-0 vs z-1），选中文字色转 --text。
 */

import { useLayoutEffect, useRef, useState } from 'react'

import { cx } from './cx'

export type TabsProps = {
  items: string[]
  value: string
  onChange: (value: string) => void
  className?: string
}

export function Tabs({ items, value, onChange, className }: TabsProps) {
  const itemRefs = useRef(new Map<string, HTMLButtonElement>())
  const [pill, setPill] = useState<{ x: number; w: number } | null>(null)

  useLayoutEffect(() => {
    const el = itemRefs.current.get(value)
    if (el) setPill({ x: el.offsetLeft, w: el.offsetWidth })
  }, [value, items])

  return (
    <div role="tablist" className={cx('relative inline-flex gap-a2 rounded-md bg-overlay-light p-a2', className)}>
      {pill && (
        <span
          data-testid="tabs-pill"
          aria-hidden
          className="absolute inset-y-a2 left-0 rounded-sm bg-card shadow-soft transition-[transform,width] duration-slow ease-smooth"
          style={{ transform: `translateX(${pill.x}px)`, width: `${pill.w}px` }}
        />
      )}
      {items.map((item) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={item === value}
          ref={(el) => {
            if (el) itemRefs.current.set(item, el)
            else itemRefs.current.delete(item)
          }}
          onClick={() => onChange(item)}
          className={cx(
            'relative z-[1] rounded-sm px-[15px] py-[5px] font-ui text-caption font-medium transition-colors duration-fast ease-out',
            item === value ? 'text-ink' : 'text-ink-light hover:text-ink',
          )}
        >
          {item}
        </button>
      ))}
    </div>
  )
}
