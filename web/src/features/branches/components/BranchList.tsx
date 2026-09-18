import { useTranslation } from '../../../lib/i18n'
import type { Branch } from '../../../api/types'
import { Button } from '../../../ui/primitives'

export interface BranchListProps {
  branches: Branch[]
  current: string
  onSelect: (name: string) => void
}

/**
 * 分支列表：名字 + 条数 + 当前标记。
 *
 * 切换只是改 L4 里的 `branch` 状态（服务端没有「当前分支」这个概念——它只有一组
 * 链尾值），所以这个组件不发任何请求。
 */
export function BranchList({ branches, current, onSelect }: BranchListProps) {
  const { t } = useTranslation()

  if (branches.length === 0) {
    return <p className="text-xs text-ink/70">{t('branches.empty')}</p>
  }

  return (
    <ul className="flex flex-col gap-1">
      {branches.map((branch) => (
        <li key={branch.name}>
          <Button
            size="sm"
            variant={branch.name === current ? 'primary' : 'secondary'}
            className="w-full justify-start"
            aria-current={branch.name === current ? 'true' : undefined}
            onClick={() => onSelect(branch.name)}
          >
            <span className="font-sketch">{branch.name}</span>
            <span className="ml-auto text-[10px] text-ink/70">
              {t('branches.entryCount', { count: branch.entry_count })}
            </span>
          </Button>
        </li>
      ))}
    </ul>
  )
}
