import { useState } from 'react'
import { GitBranch } from 'lucide-react'

import { ApiError } from '../../../api/client'
import { useBranches, useCreateBranch } from '../../../api/queries'
import { useErrorText } from '../../../lib/errors'
import { useTranslation } from '../../../lib/i18n'
import { Button } from '../../../ui/primitives'
import { BranchList } from './BranchList'

export interface BranchSelectorProps {
  sessionId: string
  /** 当前查看的分支名（L4 的状态，不是服务端状态）。 */
  current: string
  /** 活动 run 期间禁用：服务端也会拒分叉，UI 先不要给出会失败的入口。 */
  disabled?: boolean
  onSwitch: (name: string) => void
}

/**
 * 分支选择器：显示当前分支、切换、从当前链尾分叉。
 *
 * 数据由组件自己取（L2 的 hook），route 只传「当前是谁」与「切到谁」——feature 之间
 * 不互相 import，会话与分支因此彼此不认识。
 *
 * 「从链尾分叉」是**保守**的那一个：它复制当前分支整条链，用于「另开一条路试试」。
 * 精确地在某一轮处分叉走条目动作行里的「从此处分支」（route 的 onFork）。
 */
export function BranchSelector({
  sessionId,
  current,
  disabled = false,
  onSwitch,
}: BranchSelectorProps) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const branches = useBranches(sessionId)
  const create = useCreateBranch()
  const [open, setOpen] = useState(false)
  const [alert, setAlert] = useState<string | null>(null)

  const list = branches.data?.branches ?? []
  const active = list.find((item) => item.name === current)

  const failure = (error: unknown) =>
    setAlert(
      errorText(
        error instanceof ApiError ? error.code : undefined,
        error instanceof Error ? error.message : undefined,
      ),
    )

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-sketch text-xs text-ink/70">{t('branches.current')}</span>
        <Button
          size="sm"
          variant="secondary"
          aria-expanded={open}
          disabled={disabled}
          onClick={() => setOpen((value) => !value)}
        >
          <span aria-hidden="true">
            <GitBranch size={14} />
          </span>
          {current}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          loading={create.isPending}
          disabled={disabled}
          onClick={() =>
            create.mutate(
              { sessionId, at: active?.tip_entry_id ?? null },
              {
                onSuccess: (created) => {
                  setAlert(null)
                  setOpen(false)
                  onSwitch(created.name)
                },
                onError: failure,
              },
            )
          }
        >
          {t('branches.fork')}
        </Button>
      </div>

      {open ? (
        <BranchList
          branches={list}
          current={current}
          onSelect={(name) => {
            setOpen(false)
            onSwitch(name)
          }}
        />
      ) : null}

      {alert ? (
        <p role="alert" className="text-xs text-ink/70">
          {alert}
        </p>
      ) : null}
    </div>
  )
}
