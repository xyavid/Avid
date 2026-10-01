/*
 * Inspector：右侧检查器面板（宽 380px 由外层布局给，组件自身撑满）。
 *
 * 为什么要三个页签而不是把参数和结果叠在一起：同一份数据的三种读法服务于三种不同的问题
 * ——"它收到了什么参数"（参数）、"它改了什么"（改动）、"它返回了什么"（结果）。
 * 叠在一起会让最长的那一份（通常是结果）把另两份挤到看不见。
 *
 * `tab` 是**受控**的：选中项由上层持有（它同时决定面板里放哪个工具的 `selection`），
 * 组件只负责画与转发。这样"流式更新时选中项仍是同一次工具调用"就只有一个真相来源。
 *
 * 已知取舍：
 *   · 不做语法高亮（见 JsonView）；
 *   · 不改动数据的 diff 不做字符级对齐，只按行 LCS（见 lib/diff.ts）；
 *   · 没有选中项时 `diff` 页签是禁用的，但仍然渲染——把页签藏起来会让"这里能看改动"
 *     这件事变得不可发现。
 */

import type { ReactElement, ReactNode } from 'react'

import { BracesIcon, CloseIcon, DiffIcon, FileIcon } from '../../../ui/icons'
import { Button, cx } from '../../../ui/primitives'
import { DiffView } from './DiffView'
import { JsonView } from './JsonView'

export type InspectorTab = 'content' | 'diff' | 'json'

export interface InspectorSelection {
  title: string
  /** 工具调用参数（json 页签的来源） */
  value: unknown
  /** 结果文本 */
  content: string
  /** 若能从参数里看出编辑前后文本则给出 diff 页签内容 */
  diff?: { before: string; after: string }
}

export interface InspectorProps {
  selection: InspectorSelection | null
  tab: InspectorTab
  onTabChange: (tab: InspectorTab) => void
  onClose: () => void
}

const PANEL_ID = 'inspector-panel'

const TABS: ReadonlyArray<{ key: InspectorTab; label: string; icon: ReactNode }> = [
  { key: 'content', label: '结果', icon: <FileIcon size={14} /> },
  { key: 'diff', label: '改动', icon: <DiffIcon size={14} /> },
  { key: 'json', label: '参数', icon: <BracesIcon size={14} /> },
]

function EmptyState({ text }: { text: string }): ReactElement {
  return <p className="p-a16 text-ui text-ink-muted">{text}</p>
}

function panelBody(selection: InspectorSelection | null, tab: InspectorTab): ReactElement {
  if (selection === null) {
    return (
      <EmptyState text="还没有选中工具调用。在工具卡上点「查看」，这里会显示它的参数、结果与改动。" />
    )
  }
  if (tab === 'content') {
    if (selection.content.trim() === '') return <EmptyState text="这次调用没有返回内容。" />
    return (
      <pre className="whitespace-pre-wrap p-a8 font-mono text-hint text-ink-muted">
        {selection.content}
      </pre>
    )
  }
  if (tab === 'diff') {
    // "这个工具本来就没有改动"与"还没选中任何东西"是两回事，文案必须分开。
    if (selection.diff === undefined) {
      return <EmptyState text="这次调用没有可比较的改动：只有带前后文本的工具才给 diff。" />
    }
    return <DiffView before={selection.diff.before} after={selection.diff.after} />
  }
  return <JsonView value={selection.value} />
}

export function Inspector({
  selection,
  tab,
  onTabChange,
  onClose,
}: InspectorProps): ReactElement {
  const diffAvailable = selection?.diff !== undefined

  return (
    <div className="flex h-full w-full flex-col bg-deep">
      <header className="avid-hair-b flex h-[44px] shrink-0 items-center justify-between gap-a8 px-a12">
        <span className="min-w-0 truncate font-medium text-ui text-ink">
          {selection === null ? '检查器' : selection.title}
        </span>
        <Button
          size="icon"
          variant="ghost"
          icon={<CloseIcon size={14} />}
          aria-label="关闭检查器"
          onClick={onClose}
        />
      </header>

      <div
        role="tablist"
        aria-label="检查器视图"
        className="avid-hair-b flex shrink-0 items-center gap-a2 px-a8 py-a4"
      >
        {TABS.map((item) => {
          const disabled = item.key === 'diff' && !diffAvailable
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              id={`inspector-tab-${item.key}`}
              aria-selected={tab === item.key}
              aria-controls={PANEL_ID}
              disabled={disabled}
              onClick={() => onTabChange(item.key)}
              className={cx(
                'inline-flex items-center gap-a4 rounded-xs px-a8 py-a4 text-caption',
                'transition-colors duration-fast ease-standard',
                tab === item.key
                  ? 'bg-card text-ink shadow-1'
                  : 'text-ink-muted hover:bg-accent-soft hover:text-ink',
                disabled && 'cursor-not-allowed opacity-45 hover:bg-transparent hover:text-ink-muted',
              )}
            >
              {item.icon}
              {item.label}
            </button>
          )
        })}
      </div>

      <div
        role="tabpanel"
        id={PANEL_ID}
        aria-labelledby={`inspector-tab-${tab}`}
        className="min-h-0 flex-1 overflow-auto"
      >
        {panelBody(selection, tab)}
      </div>
    </div>
  )
}
