/**
 * 模型选择（阶段 13；阶段 54 去掉「跟随设置」；阶段 55 加推理强度）：输入区左侧的胶囊，
 * 决定**本次运行**用哪个模型、以及哪一档推理强度——都在候选里自己挑。
 *
 * 候选只有 BYOK（「设置 → 模型」里配的提供商与模型），内核不预置任何模型选项；
 * **档位列表也是配置来的**（每个模型声明自己认哪些），所以这段只在当前模型声明了档位时出现，
 * 选项就是那份列表 + 一个「不设」（不设 = 请求里不带这个参数）。
 * 没配模型时这里给一句去设置里添加的指引，发送钮同时保持禁用（没选模型不发车）。
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
  /** 本次运行的推理强度；null = 不设（请求里不带这个参数）。 */
  effort?: string | null
  onChangeEffort?: (effort: string | null) => void
  /** BYOK 候选（providerId/modelId ref + 展示名）；来自「设置 → 模型」的用户配置。 */
  candidates: ModelCandidate[]
}

export function ModelButton({
  model,
  onChange,
  effort = null,
  onChangeEffort,
  candidates,
}: ModelButtonProps) {
  const [open, setOpen] = useState(false)
  const pick = (next: string) => {
    onChange(next)
    setOpen(false)
  }
  const current = candidates.find((candidate) => candidate.ref === model)
  // 档位跟着候选走：每个模型自己声明认哪些（空 = 这个模型不提这件事，界面就不显示这一段）
  const efforts = current?.reasoning_efforts ?? []
  const label = current?.label ?? model ?? '选择模型'
  const pickEffort = (level: string | null) => {
    onChangeEffort?.(level)
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
          'inline-flex h-[26px] items-center gap-a6 rounded-sm border-hairline px-a8 font-ui text-caption transition-colors duration-fast ease-out hover:bg-overlay-light',
          // 还没选：强调色描边——发送钮此时是禁用的，原因要看得见（不写说明句）
          model === null ? 'border-accent text-accent' : 'border-hair text-ink',
        )}
      >
        <Icon name="sparkle" size={12} />
        <span className="max-w-[180px] truncate">{label}</span>
        {/* 强度是这一次的一个选择，胶囊里带出来（几档就写在后面，不设时不占位） */}
        {effort !== null && <span className="shrink-0 text-ink-muted">{`· ${effort}`}</span>}
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

          {/* 推理强度：档位来自所选模型的声明列表，随这一轮发出去 */}
          {efforts.length > 0 && onChangeEffort !== undefined && (
            <>
              <p className="mt-a6 border-t border-hair pt-a6 px-a8 font-ui text-micro text-ink-muted">
                推理强度
              </p>
              {[null, ...efforts].map((level) => (
                <button
                  key={level ?? 'unset'}
                  type="button"
                  onClick={() => pickEffort(level)}
                  className="flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a4 text-left font-ui text-caption text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
                >
                  <span className="min-w-0 truncate">{level ?? '不设'}</span>
                  {effort === level && (
                    <span className="shrink-0 text-accent">
                      <Icon name="check" size={12} />
                    </span>
                  )}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
