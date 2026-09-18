import { Badge, Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'
import { TASK_FILTERS } from '../hooks/useTaskView'
import type { TaskCounts } from '../hooks/useTaskView'

export interface TaskFiltersProps {
  counts: TaskCounts
}

/** 五个筛选按钮：选中态用 primary，点击写回界面域（不触发请求）。 */
export function TaskFilters({ counts }: TaskFiltersProps) {
  const { t } = useTranslation()
  const filter = useUiStore((state) => state.taskFilter)
  const setFilter = useUiStore((state) => state.setTaskFilter)

  return (
    <div role="group" aria-label={t('tasks.title')} className="flex flex-wrap items-center gap-2">
      {TASK_FILTERS.map((key) => {
        const active = key === filter
        return (
          <Button
            key={key}
            size="sm"
            variant={active ? 'primary' : 'secondary'}
            aria-pressed={active}
            onClick={() => setFilter(key)}
          >
            <span>{t(`tasks.filter.${key}`)}</span>
            <Badge tone={active ? 'mark' : 'neutral'} count={counts[key]} />
          </Button>
        )
      })}
    </div>
  )
}
