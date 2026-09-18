import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useSkills } from '../../../api/queries'
import { ApiError } from '../../../api/client'
import type { LocaleApi } from '../../../lib/i18n'
import type { Skill } from '../../../api/types'

/** 服务端错误码是稳定契约：先查 errors.<code>，缺词条时回落 errors.unknown。 */
function errorText(t: LocaleApi['t'], error: unknown): string {
  if (error instanceof ApiError) {
    const key = `errors.${error.code}`
    const text = t(key)
    return text === key ? t('errors.unknown', { code: error.code }) : text
  }
  return t('common.networkError')
}

function SkillItem({ skill }: { skill: Skill }) {
  return (
    <li className="sketch-chip flex flex-col gap-1 p-3">
      <span className="font-sketch text-sm">{skill.name}</span>
      <span className="truncate text-xs text-ink/70">{skill.description}</span>
    </li>
  )
}

function SkillsError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
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

/** 技能目录：只列 name/description，技能全文由 agent 按需加载。 */
export function SkillCatalog() {
  const { t } = useTranslation()
  const query = useSkills()
  const skills = query.data ?? []

  return (
    <section className="sketch-panel flex flex-col gap-3 p-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-sketch text-lg">{t('skills.title')}</h1>
        <p className="text-xs text-ink/70">{t('skills.hint')}</p>
        <p className="text-xs text-ink/70">{t('skills.count', { count: skills.length })}</p>
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
