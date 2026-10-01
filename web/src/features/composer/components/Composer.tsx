/*
 * Composer：输入区的组装层。
 *
 * 它把四件事收在一个地方：草稿与键盘、提交守卫、权限选择（含 full 的显式确认）、
 * 以及一行的读数与动作。为什么不让父层代管草稿：草稿的每一次按键都会重渲染输入框，
 * 而父层同时还订阅着每帧都在变的流式视图——草稿一旦上提，输入延迟会被 token 流拖住。
 *
 * 两条安全相关的设计：
 *   1. 切到 `full` 必然先过一次显式确认弹窗；取消则**保持原模式**。
 *      `full` 会关掉沙箱与出网限制，服务端也要求 `full_access_ack`——
 *      界面上的这次确认就是那个"有意识动作"的落点，不能省。
 *   2. 提交守卫把 `disabled` / `running` / 提交中 / 空白四件事一起判，
 *      避免"点了两次跑了两遍"与"空格触发一次空运行"。
 *
 * 已知取舍：Esc 清空做成两段式（第一次只提示"再按一次"）而不是 `window.confirm`。
 * 原生确认框会打断输入节奏、样式不可控，而草稿是用户的劳动成果，一次误触就抹掉
 * 比多按一次键糟糕得多。
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, KeyboardEvent as ReactKeyboardEvent, ReactElement } from 'react'

import type { PermissionMode, SandboxState, UsageReport } from '../../../api/types'
import { SendIcon, StopIcon } from '../../../ui/icons'
import { Button, Dialog } from '../../../ui/primitives'
import { presetOf } from '../lib/permission'
import { PermissionSelector } from './PermissionSelector'
import { UsageMeter } from './UsageMeter'

export interface ComposerProps {
  /** 是否禁用（无会话 / 会话被占用等） */
  disabled: boolean
  /** 运行中：把发送换成停止 */
  running: boolean
  phase: 'idle' | 'running' | 'awaiting_approval' | 'finished' | 'failed' | 'cancelled'
  permission: PermissionMode
  onPermissionChange: (mode: PermissionMode) => void
  sandbox: SandboxState | null
  usage: UsageReport | null
  /** 提交一次运行；成功后由调用方清空草稿（返回 Promise 以便按钮显示 loading） */
  /**
   * 提交一次运行。
   *
   * `fullAck` 只在**用户确认过「完全访问」弹窗**后为 true（见下方的 full 确认流程），
   * 它不是从 `mode` 反推出来的：服务端要求 `full` 必须带 `full_access_ack: true`，
   * 而那条规则的全部意义在于"关掉沙箱与出网边界是一次有意识的动作"。
   * 若改成"选了 full 就自动带上 ack"，这个动作就退化成选菜单里的第三项。
   */
  onSubmit: (input: {
    prompt: string
    mode: PermissionMode
    fullAck?: boolean
  }) => Promise<void> | void
  onCancel: () => void
  onRetry?: () => void
  placeholder?: string
}

const MIN_ROWS = 3
const MAX_ROWS = 10
const DEFAULT_PLACEHOLDER = '说点什么…（Enter 发送，Shift+Enter 换行）'

/** 确认弹窗里把三轴翻译成人话；取值来自预设，这里只负责措辞。 */
const APPROVAL_TEXT: Record<'user' | 'classifier' | 'none', string> = {
  user: '越界动作停下来问你',
  classifier: '分类器代你裁决',
  none: '不再询问',
}
const SANDBOX_TEXT: Record<'workspace' | 'disabled', string> = {
  workspace: '限于工作区',
  disabled: '已禁用',
}
const NETWORK_TEXT: Record<'restricted' | 'open', string> = {
  restricted: '无出网',
  open: '不限',
}

export function Composer({
  disabled,
  running,
  phase,
  permission,
  onPermissionChange,
  sandbox,
  usage,
  onSubmit,
  onCancel,
  onRetry,
  placeholder = DEFAULT_PLACEHOLDER,
}: ComposerProps): ReactElement {
  const [draft, setDraft] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [escArmed, setEscArmed] = useState(false)
  const [pendingFull, setPendingFull] = useState(false)

  /*
   * 行数按换行符估：没有实测 scrollHeight，因为 jsdom 下测量恒为 0（用例就跑不出来），
   * 而长行的软换行在渲染前本来也算不准。代价是超长单行不会自动撑高——
   * 上限 10 行 + 内部滚动兜住了这种情况。
   */
  const rows = useMemo(
    () => Math.min(MAX_ROWS, Math.max(MIN_ROWS, draft.split('\n').length)),
    [draft],
  )

  const canSubmit = !disabled && !running && !submitting && draft.trim().length > 0

  const clearDraft = useCallback((): void => {
    setDraft('')
    setEscArmed(false)
  }, [])

  /**
   * 用户是否已就当前这轮「完全访问」授权做出过确认。
   *
   * 为什么用 ref 而不是 state：它不参与渲染，只影响下一次提交的请求体；
   * 放进 state 会让每次确认多一次无意义的重渲染。
   * 为什么切回别的档要清掉：授权不该跨模式残留——用户切回 manual 再切回 full 时，
   * 必须再看一次弹窗，否则"切走再切回"就成了绕过确认的路子。
   */
  const fullAcknowledged = useRef(false)

  /** 只消费"这次提交的那份草稿"；用户若在提交期间又打了字，那段新内容不能被抹掉。 */
  const consumeDraft = useCallback((submitted: string): void => {
    setDraft((current) => (current === submitted ? '' : current))
    setEscArmed(false)
  }, [])

  const submit = useCallback((): void => {
    if (disabled || running || submitting) return
    const submitted = draft
    const prompt = submitted.trim()
    if (prompt === '') return
    /*
     * 草稿的归属：`ComposerProps` 里没有 value/onChange，所以草稿只能由本组件持有，
     * 清空也只能由本组件做（"由调用方清空"在只有 onSubmit 的契约下做不到）。
     * 所以按"成功即消费"处理：
     *   · 返回 Promise：**成功才清空**，失败保留——调用方（useRunStream）明确把起运行
     *     失败往上抛，就是为了让用户不用重打一遍；
     *   · 返回 void：调用方没有给失败通道，按"已受理"清空，否则输入区会一直留着旧内容。
     * 比对用的是**未 trim 的原文**：提交的是 trim 后的 prompt，但草稿里可能带着首尾空白，
     * 拿 trim 后的值去比会永远不相等，草稿就清不掉了。
     */
    const outcome = onSubmit({
      prompt,
      mode: permission,
      // 只有"当前就是 full 且用户确认过"才带 ack；其余情况整个键不出现。
      ...(permission === 'full' && fullAcknowledged.current ? { fullAck: true } : {}),
    })
    if (outcome !== undefined && typeof outcome.then === 'function') {
      setSubmitting(true)
      // 两个分支都要收尾：只挂 onFulfilled 的话，失败的 Promise 会变成未处理的 rejection。
      void outcome.then(
        () => {
          setSubmitting(false)
          consumeDraft(submitted)
        },
        () => setSubmitting(false),
      )
      return
    }
    consumeDraft(submitted)
  }, [disabled, running, submitting, draft, onSubmit, permission, consumeDraft])

  const requestPermission = (mode: PermissionMode): void => {
    if (mode === permission) return
    // full 要显式确认；其余档直接切。
    if (presetOf(mode).needsFullAck) {
      setPendingFull(true)
      return
    }
    // 切到任何非 full 档都清掉确认位：授权不跨模式残留（见 fullAcknowledged 的说明）。
    fullAcknowledged.current = false
    onPermissionChange(mode)
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      // 输入法合成期的 Enter 是"确认候选词"，不是提交（否则中文用户永远打不出换行）。
      if (event.nativeEvent.isComposing) return
      event.preventDefault()
      submit()
      return
    }
    if (event.key === 'Escape' && draft !== '') {
      event.preventDefault()
      if (!escArmed) {
        setEscArmed(true)
        return
      }
      clearDraft()
    }
  }

  const onChange = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    setDraft(event.target.value)
    // 继续输入就让上一次的"上膛"作废：否则按过 Esc 又接着打字，下次 Esc 会直接清空。
    if (escArmed) setEscArmed(false)
  }

  const fullPreset = presetOf('full')

  return (
    <div className="avid-hair-t flex flex-col gap-a8 bg-canvas px-a16 py-a12">
      <div className="flex flex-col rounded-md border-hair bg-card transition-colors duration-fast ease-standard focus-within:border-accent">
        <textarea
          value={draft}
          rows={rows}
          disabled={disabled}
          placeholder={placeholder}
          aria-label="输入"
          onChange={onChange}
          onKeyDown={onKeyDown}
          className="min-h-[--avid-composer-min-h] w-full resize-none bg-transparent px-a12 py-a10 text-body text-ink placeholder:text-ink-faint"
        />
      </div>

      <div className="flex items-center justify-between gap-a8">
        <div className="flex items-center gap-a6">
          <PermissionSelector
            value={permission}
            onChange={requestPermission}
            sandbox={sandbox}
            disabled={disabled}
          />
          {phase === 'awaiting_approval' ? (
            <span className="text-hint text-warn">等待审批</span>
          ) : null}
          {phase === 'failed' && onRetry !== undefined ? (
            <Button variant="ghost" size="sm" onClick={onRetry}>
              重试
            </Button>
          ) : null}
        </div>

        <div className="flex items-center gap-a8">
          {escArmed ? <span className="text-hint text-ink-faint">再按一次 Esc 清空</span> : null}
          <UsageMeter usage={usage} />
          {running ? (
            <Button variant="secondary" icon={<StopIcon size={14} />} onClick={onCancel}>
              停止
            </Button>
          ) : (
            <Button
              variant="primary"
              icon={<SendIcon size={14} />}
              loading={submitting}
              disabled={disabled || !canSubmit}
              onClick={submit}
            >
              发送
            </Button>
          )}
        </div>
      </div>

      <Dialog
        open={pendingFull}
        width="sm"
        title="切换到「完全访问」？"
        description="这一档会关掉沙箱与出网限制，其中的命令以 Avid 进程的权限直接执行。授权对本次选择期间发出的每一次运行生效（每次请求体都带 full_access_ack）；切回其它档后需要重新确认。"
        onClose={() => setPendingFull(false)}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setPendingFull(false)}>
              取消
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={() => {
                setPendingFull(false)
                fullAcknowledged.current = true
                onPermissionChange('full')
              }}
            >
              我明白，切换
            </Button>
          </>
        }
      >
        {/*
          三轴逐条列出，取值从预设读：让"我明白"这三个字有具体所指，
          而不是对着一个模式名点头。
        */}
        <ul className="flex flex-col gap-a2">
          <li>审批：{APPROVAL_TEXT[fullPreset.approval]}</li>
          <li>沙箱：{SANDBOX_TEXT[fullPreset.sandbox]}</li>
          <li>出网：{NETWORK_TEXT[fullPreset.network]}</li>
        </ul>
        <p className="mt-a8 text-hint text-ink-muted">
          其余两档（{presetOf('manual').label} / {presetOf('auto').label}）的沙箱与出网逐字相同，
          随时可以切回来。
        </p>
      </Dialog>
    </div>
  )
}
