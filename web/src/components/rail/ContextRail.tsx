/**
 * 右栏（参考图「工作区 / 上下文」区）：只读信息卡，读 meta 能力面与选中会话。
 * 没有装饰性内容——每个数字都能在后端找到出处；后续阶段（todo、用量）在此扩卡。
 */

import type { Meta, SessionSummary } from '../../api/types'
import { Card } from '../../ui/Card'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-a8">
      <span className="font-ui text-hint text-ink-muted">{label}</span>
      <span className="truncate font-ui text-ui text-ink">{value}</span>
    </div>
  )
}

export type ContextRailProps = {
  meta: Meta | null
  session: SessionSummary | null
}

export function ContextRail({ meta, session }: ContextRailProps) {
  const workspace = session?.workspace
  const sandbox = meta?.capabilities.sandbox
  return (
    <div className="flex flex-col gap-a16">
      <Card radius="md" title="工作区">
        <Row label="名称" value={workspace?.name ?? meta?.capabilities.workspace ?? '—'} />
        {workspace?.root && (
          <div className="mt-a4 break-all font-mono text-micro text-ink-muted">{workspace.root}</div>
        )}
      </Card>
      <Card radius="md" title="上下文">
        <Row label="模型" value={meta?.capabilities.model ?? '—'} />
        <Row label="工具" value={meta ? `${meta.capabilities.tools.length} 项` : '—'} />
        <Row label="技能" value={meta ? `${meta.capabilities.skills.length} 项` : '—'} />
        <Row
          label="沙箱"
          value={sandbox ? (sandbox.available ? `可用（${sandbox.backend}）` : '不可用') : '—'}
        />
        <Row
          label="权限归属"
          value={workspace?.default_permission ?? session?.workspace?.default_permission ?? '—'}
        />
      </Card>
    </div>
  )
}
