import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { Density } from '../../../lib/density'
import type { InspectorTab } from '../../../state/uiStore'
import { DiffView } from './DiffView'
import { JsonView } from './JsonView'

export interface InspectorSelection {
  title: string
  text: string
  arguments?: Record<string, unknown>
}

export interface InspectorProps {
  open: boolean
  tab: InspectorTab
  density: Density
  selection: InspectorSelection | null
  onTabChange: (tab: InspectorTab) => void
  onClose: () => void
}

const TABS: InspectorTab[] = ['content', 'diff', 'json']

/**
 * 检查器：三个视图**就地切换**，不进 URL 历史（否则浏览器后退键会被面板开关塞满）。
 * 高度用原生 `resize-y`，不引可拖拽分栏库；窄屏时它升格为全屏覆盖层（CSS 负责）。
 */
export function Inspector({ open, tab, density, selection, onTabChange, onClose }: InspectorProps) {
  const { t } = useTranslation()
  if (!open) return null

  const labels: Record<InspectorTab, string> = {
    content: t('tools.inspector.content'),
    diff: t('tools.inspector.diff'),
    json: t('tools.inspector.json'),
  }

  return (
    <aside
      aria-label={t('tools.inspector.content')}
      className="sketch-panel flex h-[55vh] max-h-[85vh] min-h-[35vh] w-full resize-y flex-col gap-2 overflow-hidden p-3 lg:h-auto lg:max-h-none"
    >
      <header className="flex items-center gap-2">
        <h3 className="truncate font-sketch text-sm">{selection?.title ?? t('common.none')}</h3>
        <Button size="sm" variant="secondary" className="ml-auto" onClick={onClose}>
          {t('common.close')}
        </Button>
      </header>

      <div role="tablist" aria-label={t('common.inspect')} className="flex gap-1">
        {TABS.map((item) => (
          <Button
            key={item}
            size="sm"
            variant={item === tab ? 'primary' : 'secondary'}
            role="tab"
            aria-selected={item === tab}
            onClick={() => onTabChange(item)}
          >
            {labels[item]}
          </Button>
        ))}
      </div>

      <div className="scroll-area flex-1">
        {!selection ? (
          <p className="empty-note">{t('common.none')}</p>
        ) : tab === 'content' ? (
          <pre
            className={`term overflow-x-auto whitespace-pre-wrap break-anywhere p-3 ${
              density === 'compact' ? 'text-[11px]' : 'text-xs'
            }`}
          >
            {selection.text}
          </pre>
        ) : tab === 'diff' ? (
          <DiffView text={selection.text} arguments={selection.arguments} />
        ) : (
          <JsonView value={{ text: selection.text, arguments: selection.arguments }} />
        )}
      </div>
    </aside>
  )
}
