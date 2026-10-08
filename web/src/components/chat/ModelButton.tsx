/**
 * 模型选择（阶段 13；阶段 54 去掉「跟随设置」）：输入区左侧的胶囊，决定**本次运行**
 * 用哪个模型——由用户在候选里自己挑，没有"不选"这一档。
 *
 * 候选只有 BYOK（「设置 → 模型」里配的提供商与模型），内核不预置任何模型选项；
 * 没配的时候这里给的是一句去设置里添加的指引，发送钮同时保持禁用（没选模型不发车）。
 * 选了一个你的中转站没开通的名字，会在调用时由提供方报错。
 *
 * 形态与权限胶囊同一套（胶囊按钮 + 底部弹层，点外部不关、选完自己关）。
 */

import { useState } from 'react'

import type { ModelCandidate } from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ModelButtonProps = {
  /** 本次运行的模型（providerId/modelId）；null = 还没选。 */
  model: string | null
  onChange: (model: string) => void
  /** BYOK 候选（providerId/modelId ref + 展示名）；来自「设置 → 模型」的用户配置。 */
  candidates: ModelCandidate[]
}

export function ModelButton({ model, onChange, candidates }: ModelButtonProps) {
  const [open, setOpen] = useState(false)
  const pick = (next: string) => {
    onChange(next)
    setOpen(false)
  }
  const current = candidates.find((candidate) => candidate.ref === model)
  const label = current?.label ?? model ?? '选择模型'

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="模型"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cx(
          'inline-flex h-[26px] items-center gap-a6 rounded-sm border-hairline px-a8 font-ui text-caption transition-colors duration-fast ease-out hover:bg-overlay-light',
          // 还没选：强调色描边——发送钮此时是禁用的，原因要看得见（不写说明句）
          model === null ? 'border-accent text-accent' : 'border-hair text-ink',
        )}
      >
        <Icon name="sparkle" size={12} />
        <span className="max-w-[180px] truncate">{label}</span>
        <span className={cx('text-ink-muted transition-transform duration-fast ease-out', open ? '' : 'rotate-180')}>
          <Icon name="chevron-down" size={12} />
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="模型"
          className="absolute bottom-full left-0 z-20 mb-a8 w-[300px] rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
        >
          {candidates.length > 0 ? (
            candidates.map((candidate) => (
              <button
                key={candidate.ref}
                type="button"
                onClick={() => pick(candidate.ref)}
                className="flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a4 text-left font-ui text-caption text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="min-w-0 truncate">{candidate.label}</span>
                  <span className="min-w-0 truncate font-ui text-micro text-ink-muted">{candidate.ref}</span>
                </span>
                {model === candidate.ref && (
                  <span className="shrink-0 text-accent">
                    <Icon name="check" size={12} />
                  </span>
                )}
              </button>
            ))
          ) : (
            <p className="px-a8 py-a4 font-ui text-micro text-ink-muted">
              还没有自定义模型——在「设置 → 模型」里添加 BYOK 提供商后，这里就会出现候选。
            </p>
          )}
        </div>
      )}
    </div>
  )
}
