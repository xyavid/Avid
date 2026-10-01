/*
 * 审批队列：一次运行里所有待决动作。
 *
 * 为什么不用 `role="alertdialog"`：它是一个**队列**，不是一个"必须立刻回答否则不能继续"
 * 的模态。用 alertdialog 会在每条到达时抢焦、抢朗读，反过来妨碍用户先看清楚参数。
 * 这里用带 aria-label 的 `<section>`，语义是"这里有一组待办"。
 *
 * 安全取舍（刻意的，不是疏忽）：
 *   · 「允许」不做 `autoFocus`，也不绑 Enter。拒绝是更安全的默认动作，
 *     而"焦点落在危险按钮上 + 回车确认"是这类界面最经典的一次误批。
 *   · 两个按钮都显式 `type="button"`：万一将来被放进表单里，也不会误触发表单提交。
 *   · `busy` 时两个按钮一起禁用：服务端对重复投递只回 `accepted:false`，
 *     界面若还让点第二次，用户会以为第一次没生效。
 *
 * 倒计时只在调用方给了 `now` 时才显示——组件里读 `Date.now()` 会让它对时间不可测，
 * 也让"同一份 props 渲染同一份界面"这条保证失效。
 */

import type { ReactElement } from 'react'

import type { Approval } from '../../../api/types'
import { Button, Card } from '../../../ui/primitives'

export interface ApprovalBarProps {
  approvals: Approval[]
  onAnswer: (approvalId: string, decision: 'allow' | 'deny') => void
  busy?: boolean
  /** 服务端过期时间；用于显示剩余时间，可为 null */
  now?: number
}

/** 剩余秒数；`now` 缺省或过期时间不可用时返回 null（不显示倒计时）。 */
function remainingSeconds(approval: Approval, now: number | undefined): number | null {
  if (now === undefined || !Number.isFinite(approval.expires_at)) return null
  return Math.max(0, Math.ceil((approval.expires_at - now) / 1000))
}

/** 参数摘要：工具参数来自 JSON，正常一定可序列化；真遇上环引用也不能让界面崩。 */
function formatArguments(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args, null, 2) ?? String(args)
  } catch {
    return String(args)
  }
}

export function ApprovalBar({
  approvals,
  onAnswer,
  busy = false,
  now,
}: ApprovalBarProps): ReactElement | null {
  if (approvals.length === 0) return null

  return (
    <section aria-label="待审批" className="flex flex-col gap-a8 px-a16 py-a8">
      {approvals.map((approval) => {
        const remaining = remainingSeconds(approval, now)
        // 描边色用 `border-state-warn`（实色 token）而不是"warn 色 + 40% alpha"：
        // `Card` 根类里已经带了 `border-hair`，两个类都留在 DOM 上时由 CSS 顺序决定胜负，
        // 而 Tailwind 把同轴颜色类按**类名字母序**输出——只要色名的字典序排在 hair 之前
        // （danger、accent 都是），`.border-hair` 就会静默盖掉它：描边变成普通发丝线且不报错。
        // state-* 排在 hair 之后，覆盖方向确定（依据见 tokens.css 里 --avid-state-warn-rgb 的注释）。
        return (
          <Card key={approval.approval_id} tone="paper" className="border-state-warn p-a12">
            <div className="flex items-baseline justify-between gap-a8">
              <span className="font-medium text-ui text-ink">{approval.tool}</span>
              {remaining === null ? null : (
                <span className="text-hint text-ink-muted tabular-nums">
                  {remaining > 0 ? `剩余 ${remaining} 秒` : '已过期'}
                </span>
              )}
            </div>

            {approval.reason === '' ? null : (
              <p className="mt-a4 text-ui text-ink-light">{approval.reason}</p>
            )}

            <pre className="mt-a6 max-h-[160px] overflow-auto whitespace-pre-wrap rounded-sm bg-inset p-a8 font-mono text-hint text-ink-muted">
              {formatArguments(approval.arguments)}
            </pre>

            {/* 允许在左、拒绝在右：右下方是"顺手点"的位置，把它留给更安全的动作。 */}
            <div className="mt-a8 flex items-center justify-end gap-a6">
              <Button
                type="button"
                variant="primary"
                size="sm"
                disabled={busy}
                onClick={() => onAnswer(approval.approval_id, 'allow')}
              >
                允许
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => onAnswer(approval.approval_id, 'deny')}
              >
                拒绝
              </Button>
            </div>
          </Card>
        )
      })}
    </section>
  )
}
