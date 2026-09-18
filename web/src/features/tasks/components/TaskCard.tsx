import { clsx } from 'clsx'
import { useState } from 'react'

import { Badge, Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { BadgeTone } from '../../../ui/primitives'
import type { Task } from '../../../api/types'

const STATUS_TONES: Record<Task['status'], BadgeTone> = {
  pending: 'warn',
  in_progress: 'info',
  completed: 'ok',
}

/** 形状按索引轮换 1→2→3→1，相邻卡片永不同形。 */
const SHAPES = ['shape-1', 'shape-2', 'shape-3'] as const

function shapeFor(index: number): string {
  return SHAPES[index % SHAPES.length] ?? 'shape-1'
}

function DependencyList({ task }: { task: Task }) {
  const { t } = useTranslation()
  if (task.blockedBy.length === 0) return null
  return (
    <ul className="flex flex-col gap-1 font-mono text-xs text-ink/70">
      {task.blockedBy.map((id) => (
        <li key={id} className="break-anywhere">
          {task.dependency_titles[id] ?? t('tasks.dependencyMissing', { id })}
        </li>
      ))}
    </ul>
  )
}

function TaskDetail({ task }: { task: Task }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2 px-3 pb-3">
      <div className="ink-rule" />
      {task.description ? <p className="text-sm text-ink/70">{task.description}</p> : null}
      <DependencyList task={task} />
      <div className="flex flex-wrap items-center gap-2">
        {task.can_start ? <Badge tone="ok">{t('tasks.canStart')}</Badge> : null}
        {task.blocked ? <Badge tone="danger">{t('tasks.blocked')}</Badge> : null}
      </div>
    </div>
  )
}

export interface TaskCardProps {
  task: Task
  index: number
}

export function TaskCard({ task, index }: TaskCardProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <article className={clsx('sketch-card', shapeFor(index))}>
      <Button
        variant="secondary"
        size="sm"
        className="w-full justify-between gap-2 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="flex flex-col items-start gap-1">
          <span className="font-sketch text-sm">{task.subject}</span>
          {task.owner ? (
            <span className="flex items-center gap-1 font-mono text-xs text-ink/70">
              <span>{t('tasks.owner')}</span>
              <span className="text-ink">{task.owner}</span>
            </span>
          ) : null}
        </span>
        <Badge tone={STATUS_TONES[task.status]}>{t(`tasks.status.${task.status}`)}</Badge>
      </Button>
      {open ? <TaskDetail task={task} /> : null}
    </article>
  )
}
