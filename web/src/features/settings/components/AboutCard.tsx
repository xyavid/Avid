/**
 * 关于卡片 + 内核字段的展示原语。
 *
 * 这里的每一块都只对 `Meta` 做只读投影：不发起请求、不留副本。
 * `MetaRow` / `BuildStamp` / `ToolsRow` / `FeaturesTable` 也给设置页的内核区复用，
 * 所以顺手从这里导出——同 feature 内引用，不跨边界。
 */

import { Badge } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { ReactNode } from 'react'
import type { BuildInfo, Meta } from '../../../api/types'

export interface MetaRowProps {
  label: string
  children: ReactNode
}

/** 一行「标签 + 值」；值允许换行（工作区路径会很长）。 */
export function MetaRow({ label, children }: MetaRowProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-ink/70">{label}</span>
      <span className="break-anywhere text-sm">{children}</span>
    </div>
  )
}

/** 打包产物才显示 sha/时间；从 checkout 运行时两者都没有意义。 */
export function BuildStamp({ build }: { build: BuildInfo }) {
  const { t } = useTranslation()
  if (build.source !== 'bundled') {
    return <span className="text-ink/70">{t('common.settings.buildDev')}</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-2 font-mono text-xs">
      <span>{build.git_sha ?? t('common.unknown')}</span>
      <span>{build.built_at ?? t('common.unknown')}</span>
    </span>
  )
}

export function ToolsRow({ tools }: { tools: string[] }) {
  const { t } = useTranslation()
  if (tools.length === 0) return <span className="text-ink/70">{t('common.none')}</span>
  return (
    <span className="flex flex-wrap gap-1">
      {tools.map((tool) => (
        <Badge key={tool} tone="neutral">
          {tool}
        </Badge>
      ))}
    </span>
  )
}

export function FeaturesTable({ features }: { features: Record<string, number> }) {
  const { t } = useTranslation()
  const entries = Object.entries(features)
  if (entries.length === 0) return <span className="text-ink/70">{t('common.none')}</span>
  return (
    <span className="flex flex-col gap-1 font-mono text-xs">
      {entries.map(([name, value]) => (
        <span key={name} className="flex items-center justify-between gap-2">
          <span>{name}</span>
          <span>{value}</span>
        </span>
      ))}
    </span>
  )
}

/** 小卡片：版本 / 构建 / 健康，全部取自 meta，不发新请求。 */
export function AboutCard({ meta }: { meta: Meta }) {
  const { t } = useTranslation()
  return (
    <div className="sketch-chip flex flex-col gap-2 bg-sand p-3">
      <MetaRow label={t('common.settings.apiVersion')}>
        <span className="font-mono">{meta.api_version}</span>
      </MetaRow>
      <MetaRow label={t('common.settings.build')}>
        <BuildStamp build={meta.build} />
      </MetaRow>
      <MetaRow label={t('common.settings.health')}>
        <Badge tone="ok">
          {t('common.settings.healthValue', { seconds: meta.stream.heartbeat_seconds })}
        </Badge>
      </MetaRow>
    </div>
  )
}
