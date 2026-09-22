/**
 * 导航列的标题行：标题、搜索开关、新增工作区。
 *
 * 「新增工作区」按能力表决定存在与否（`canAddWorkspace`）：老内核没有这个端点时不显示
 * 按钮，而不是点出一个 404。loading 覆盖"选择器还开着"与"登记请求在飞"两段，
 * 因为用户看到的都是"按下去之后没反应"。
 */
import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'

export interface SessionListHeaderProps {
  searchOpen: boolean
  canAddWorkspace: boolean
  adding: boolean
  onToggleSearch: () => void
  onAddWorkspace: () => void
}

export function SessionListHeader({
  searchOpen,
  canAddWorkspace,
  adding,
  onToggleSearch,
  onAddWorkspace,
}: SessionListHeaderProps) {
  const { t } = useTranslation()

  return (
    <div className="flex items-center justify-between">
      <h2 className="font-sketch text-lg">{t('sessions.workspacesTitle')}</h2>
      <Button
        size="icon"
        variant="secondary"
        aria-label={t('sessions.search.open')}
        title={t('sessions.search.open')}
        aria-expanded={searchOpen}
        onClick={onToggleSearch}
      >
        <span aria-hidden="true">🔍</span>
      </Button>
      {canAddWorkspace ? (
        <Button
          size="icon"
          variant="secondary"
          aria-label={t('sessions.workspace.add')}
          title={t('sessions.workspace.add')}
          loading={adding}
          onClick={onAddWorkspace}
        >
          <span aria-hidden="true">＋</span>
        </Button>
      ) : null}
    </div>
  )
}
