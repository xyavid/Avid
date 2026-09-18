import { errorMessage } from '../../../lib/errors'
import { Button, Field, Input } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useMeta } from '../../../api/queries'
import { useUiStore } from '../../../state/uiStore'
import { AboutCard, BuildStamp, FeaturesTable, MetaRow, ToolsRow } from './AboutCard'
import type { Density } from '../../../lib/density'
import type { TextScale } from '../../../state/uiStore'
import type { Meta } from '../../../api/types'

const SCALES: TextScale[] = [90, 100, 110, 125]
const DENSITIES: Density[] = ['compact', 'comfy']

/** 服务端错误码是稳定契约：先查 errors.<code>，缺词条时回落 errors.unknown。 */
interface ChoiceOption<T extends string | number> {
  value: T
  label: string
}

/** 单选组：按钮切换（primitives 只有按钮/输入两种原子），aria-pressed 报选中态。 */
function ChoiceRow<T extends string | number>({
  label,
  options,
  selected,
  onSelect,
}: {
  label: string
  options: ChoiceOption<T>[]
  selected: T
  onSelect: (value: T) => void
}) {
  return (
    <Field label={label}>
      <div role="group" aria-label={label} className="flex flex-wrap gap-2">
        {options.map((option) => (
          <Button
            key={option.value}
            size="sm"
            variant={option.value === selected ? 'primary' : 'secondary'}
            aria-pressed={option.value === selected}
            onClick={() => onSelect(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>
    </Field>
  )
}

function AppearanceSection() {
  const { t } = useTranslation()
  const textScale = useUiStore((state) => state.textScale)
  const setTextScale = useUiStore((state) => state.setTextScale)
  const density = useUiStore((state) => state.density)
  const setDensity = useUiStore((state) => state.setDensity)

  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-sketch text-base">{t('common.settings.appearance')}</h2>
      <ChoiceRow
        label={t('common.settings.textScale')}
        options={SCALES.map((value) => ({ value, label: String(value) }))}
        selected={textScale}
        onSelect={setTextScale}
      />
      <ChoiceRow
        label={t('common.settings.density')}
        options={DENSITIES.map((value) => ({
          value,
          label: t(`common.settings.density.${value}`),
        }))}
        selected={density}
        onSelect={setDensity}
      />
    </section>
  )
}

function BehaviorSection() {
  const { t } = useTranslation()
  const autoApprove = useUiStore((state) => state.autoApprove)
  const setAutoApprove = useUiStore((state) => state.setAutoApprove)

  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-sketch text-base">{t('common.settings.behavior')}</h2>
      <Field label={t('common.settings.autoApprove')} htmlFor="settings-auto-approve">
        <span className="w-control">
          <Input
            id="settings-auto-approve"
            type="checkbox"
            checked={autoApprove}
            onChange={(event) => setAutoApprove(event.target.checked)}
          />
        </span>
      </Field>
    </section>
  )
}

function EngineSection({ meta }: { meta: Meta }) {
  const { t } = useTranslation()
  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-sketch text-base">{t('common.settings.engine')}</h2>
      <MetaRow label={t('common.settings.model')}>
        {meta.capabilities.model ?? (
          <span className="text-ink/70">{t('common.settings.modelMissing')}</span>
        )}
      </MetaRow>
      <MetaRow label={t('common.settings.apiVersion')}>
        <span className="font-mono">{meta.api_version}</span>
      </MetaRow>
      <MetaRow label={t('common.settings.workspace')}>
        <span className="font-mono">{meta.capabilities.workspace}</span>
      </MetaRow>
      <MetaRow label={t('common.settings.build')}>
        <BuildStamp build={meta.build} />
      </MetaRow>
      <MetaRow label={t('common.settings.tools')}>
        <ToolsRow tools={meta.capabilities.tools} />
      </MetaRow>
      <MetaRow label={t('common.settings.features')}>
        <FeaturesTable features={meta.features} />
      </MetaRow>
      <AboutCard meta={meta} />
    </section>
  )
}

function MetaError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="empty-note flex flex-col items-center gap-2">
      <p className="font-sketch text-danger">{t('errors.title')}</p>
      <p>{errorMessage(t, error)}</p>
      <Button size="sm" onClick={onRetry}>
        {t('common.retry')}
      </Button>
    </div>
  )
}

/** 设置页：外观与行为写界面域，内核区只读 meta。 */
export function SettingsPanel() {
  const { t } = useTranslation()
  const meta = useMeta()

  return (
    <section className="sketch-panel flex flex-col gap-4 p-4">
      <h1 className="font-sketch text-lg">{t('common.settings.title')}</h1>
      <AppearanceSection />
      <BehaviorSection />
      {meta.isPending ? (
        <p className="empty-note">{t('common.loading')}</p>
      ) : meta.error ? (
        <MetaError
          error={meta.error}
          onRetry={() => {
            void meta.refetch()
          }}
        />
      ) : meta.data ? (
        <EngineSection meta={meta.data} />
      ) : null}
      <p className="text-xs text-ink/70">{t('common.settings.configHint')}</p>
    </section>
  )
}
