import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { ApiError } from '../../../api/client'
import { useTaskView } from '../hooks/useTaskView'
import { TaskCard } from './TaskCard'
import { TaskFilters } from './TaskFilters'
import type { LocaleApi } from '../../../lib/i18n'
import type { Task } from '../../../api/types'

/** 服务端错误码是稳定契约：先查 errors.<code>，缺词条时回落 errors.unknown。 */
function errorText(t: LocaleApi['t'], error: unknown): string {
  if (error instanceof ApiError) {
    const key = `errors.${error.code}`
    const text = t(key)
    return text === key ? t('errors.unknown', { code: error.code }) : text
  }
  return t('common.networkError')
}

function TaskList({ tasks }: { tasks: Task[] }) {
  const { t } = useTranslation()
  if (tasks.length === 0) return <p className="empty-note">{t('tasks.empty')}</p>
  return (
    <ul className="flex flex-col gap-3">
      {tasks.map((task, index) => (
        <li key={task.id}>
          <TaskCard task={task} index={index} />
        </li>
      ))}
    </ul>
  )
}

function TaskError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="empty-note flex flex-col items-center gap-2">
      <p className="font-sketch text-danger">{t('errors.title')}</p>
      <p>{errorText(t, error)}</p>
      <Button size="sm" onClick={onRetry}>
        {t('common.retry')}
      </Button>
    </div>
  )
}

/** 只读任务板：筛选在界面域，数据在查询缓存，组件只做投影。 */
export function TaskBoard() {
  const { t } = useTranslation()
  const { tasks, counts, pending, error, refetch } = useTaskView()

  return (
    <section className="sketch-panel flex flex-col gap-3 p-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-sketch text-lg">{t('tasks.title')}</h1>
        <p className="text-xs text-ink/70">{t('tasks.readonly')}</p>
        <p className="text-xs text-ink/70">{t('tasks.count', { count: tasks.length })}</p>
      </header>
      <TaskFilters counts={counts} />
      {pending ? (
        <p className="empty-note">{t('common.loading')}</p>
      ) : error ? (
        <TaskError
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      ) : (
        <TaskList tasks={tasks} />
      )}
    </section>
  )
}
