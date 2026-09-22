import { errorMessage } from '../../../lib/errors'
import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useSkills } from '../../../api/queries'
import type { Skill } from '../../../api/types'

/** 服务端错误码是稳定契约：先查 errors.<code>，缺词条时回落 errors.unknown。 */
function SkillItem({ skill }: { skill: Skill }) {
  return (
    <li className="surface-chip flex flex-col gap-1 p-3">
      <span className="text-sm">{skill.name}</span>
      <span className="truncate text-xs text-ink-muted">{skill.description}</span>
    </li>
  )
}

function SkillsError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="empty-note flex flex-col items-center gap-2">
      <p className="text-danger">{t('errors.title')}</p>
      <p>{errorMessage(t, error)}</p>
      <Button size="sm" onClick={onRetry}>
        {t('common.retry')}
      </Button>
    </div>
  )
}

/** 技能目录：只列 name/description，技能全文由 agent 按需加载。 */
export function SkillCatalog() {
  const { t } = useTranslation()
  const query = useSkills()
  const skills = query.data ?? []

  return (
    <section className="surface-panel flex flex-col gap-3 p-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold">{t('skills.title')}</h1>
        <p className="text-xs text-ink-muted">{t('skills.hint')}</p>
        <p className="text-xs text-ink-muted">{t('skills.count', { count: skills.length })}</p>
      </header>
      {query.isPending ? (
        <p className="empty-note">{t('common.loading')}</p>
      ) : query.error ? (
        <SkillsError
          error={query.error}
          onRetry={() => {
            void query.refetch()
          }}
        />
      ) : skills.length === 0 ? (
        <p className="empty-note">{t('skills.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {skills.map((skill) => (
            <SkillItem key={skill.name} skill={skill} />
          ))}
        </ul>
      )}
    </section>
  )
}
