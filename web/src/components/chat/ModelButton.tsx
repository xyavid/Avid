/**
 * 模型选择（阶段 13）：输入区左侧的胶囊，决定**本次运行**用哪个模型；缺省跟随设置。
 *
 * 为什么要有它：模型目前只在「设置 → 模型」里配（一处配置，全局生效）。但试模型是
 * 高频动作——同一个问题换个模型再问一遍，不该先把全局设置改掉再改回来。所以这里给的是
 * **按运行的覆盖**：`StartRunInput.model` 只作用于这一次运行，其余按设置解析。
 *
 * 候选的来源要说清楚：`capabilities.known_models` 取自内核的**窗口表**（它认得上下文
 * 窗口的模型），不是提供商的模型目录。选了一个你的中转站没开通的名字，会在调用时由
 * 提供方报错——弹层里那句「内核认得的模型」就是提醒这件事。
 *
 * 形态与权限胶囊同一套（胶囊按钮 + 底部弹层，点外部不关、选完自己关）。
 */

import { useState } from 'react'

import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ModelButtonProps = {
  /** 本次运行的覆盖；null = 跟随设置。 */
  model: string | null
  onChange: (model: string | null) => void
  /** 设置里解析出来的模型（「跟随设置」那一行的说明）。 */
  effective: string | null
  /** 内核认得的模型候选。 */
  known: string[]
}

export function ModelButton({ model, onChange, effective, known }: ModelButtonProps) {
  const [open, setOpen] = useState(false)
  const pick = (next: string | null) => {
    onChange(next)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="模型"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cx(
          'inline-flex h-[26px] items-center gap-a6 rounded-sm border-hairline border-hair px-a8 font-ui text-caption transition-colors duration-fast ease-out hover:bg-overlay-light',
          model ? 'text-accent' : 'text-ink-light',
        )}
      >
        <Icon name="sparkle" size={12} />
        <span className="max-w-[150px] truncate">{model ?? '跟随设置'}</span>
        {!model && effective && <span className="text-ink-muted">{effective}</span>}
        <span className={cx('text-ink-muted transition-transform duration-fast ease-out', open ? '' : 'rotate-180')}>
          <Icon name="chevron-down" size={12} />
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="模型"
          className="absolute bottom-full left-0 z-20 mb-a8 w-[280px] rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
        >
          <button
            type="button"
            onClick={() => pick(null)}
            className="flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a6 text-left transition-colors duration-fast ease-out hover:bg-overlay-light"
          >
            <span className="flex min-w-0 flex-col">
              <span className="font-ui text-caption text-ink">跟随设置</span>
              <span className="truncate font-ui text-micro text-ink-muted">
                {effective ?? '设置里还没有模型'}
              </span>
            </span>
            {model === null && (
              <span className="shrink-0 text-accent">
                <Icon name="check" size={12} />
              </span>
            )}
          </button>

          <p className="mt-a4 px-a8 font-ui text-micro text-ink-muted">内核认得的模型</p>
          {known.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => pick(name)}
              className="flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a4 text-left font-ui text-caption text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
            >
              <span className="min-w-0 truncate">{name}</span>
              {model === name && (
                <span className="shrink-0 text-accent">
                  <Icon name="check" size={12} />
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
