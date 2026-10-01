/*
 * Dialog：模态外壳。
 *
 * 为什么不用原生 `<dialog>`：`showModal()` 给的是浏览器自带的遮罩与居中，
 * 它的 ::backdrop 无法用我们的 scrim token 分档，也无法复用 tokens.css 的圆角 /
 * 发丝线 / 入场曲线。要么用 `::backdrop` 打补丁，要么自己搭容器——
 * 后者的行为完全可预测，代价是焦点管理要自己写（见下）。
 *
 * 已知取舍：**不实现焦点陷阱**。本 UI 没有嵌套模态，同时打开的对话框最多一个，
 * 而陷阱要处理 Tab 环形、动态内容、Portal 边界等一堆情况，是这块最贵的部分。
 * 取这一取舍的前提是"同时只有一个模态"——一旦出现嵌套确认（比如模态里再开模态），
 * 必须回来补上，否则 Tab 会跑到遮罩后面的按钮上。
 *
 * 关闭键位：Esc 挂在 document 上而不是面板上。面板上的 keydown 只在焦点位于面板内时触发，
 * 而"焦点因为某种原因落在 body"时 Esc 会失效——用户会认为界面卡死了。
 * 焦点转移只做"开打时把焦点送进面板"这一次，**不做"每次渲染都 focus"**：
 * 后者会在父组件重渲染时把用户正在输入的焦点抢走。
 *
 * 遮罩只用 scrim token 压暗，**不用阴影**：scrim 与 shadow 混用在暗主题下会翻白（报告 §2.7）。
 */

import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { CloseIcon } from '../icons'
import { Button } from './Button'

export interface DialogProps {
  open: boolean
  title: string
  description?: string
  onClose: () => void
  children?: ReactNode
  footer?: ReactNode
  /** 宽度档：sm=380 md=520。默认 sm。 */
  width?: 'sm' | 'md'
}

const WIDTH: Record<NonNullable<DialogProps['width']>, string> = {
  // 两个档都写死像素而不是百分比：对话框宽度应当与内容长度无关，
  // 由"里面放什么"决定（一句话确认 vs 一个表单），随窗口拉伸会让阅读行长失控。
  sm: 'w-[380px]',
  md: 'w-[520px]',
}

export function Dialog({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  width = 'sm',
}: DialogProps): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement>(null)
  const wasOpenRef = useRef(false)
  const titleId = useId()
  const descId = useId()

  useEffect(() => {
    if (open && !wasOpenRef.current) panelRef.current?.focus()
    wasOpenRef.current = open
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-modal grid place-items-center p-a16">
      {/* 遮罩单独一层，不做面板的父节点：点击面板内部的事件不会冒泡到这里，省掉 stopPropagation。 */}
      <div
        className="absolute inset-0 bg-[var(--avid-scrim-30)] animate-fade-in"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description === undefined ? undefined : descId}
        tabIndex={-1}
        className={
          // `border-hair` 两次 = 线宽 + 线色两个轴（同名 token 分属 borderWidth 与 colors 两张表）。
          'relative max-h-full overflow-y-auto rounded-md border-hair border-hair bg-float shadow-3 ' +
          'animate-scale-in ' +
          WIDTH[width] +
          ' max-w-full'
        }
      >
        <div className="flex items-start gap-a12 px-a16 pt-a12 pb-a8">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-title font-medium text-ink">
              {title}
            </h2>
            {description === undefined ? null : (
              <p id={descId} className="mt-a4 text-ui text-ink-muted">
                {description}
              </p>
            )}
          </div>
          {/* 右上角关闭按钮：只有 Esc 与点遮罩的模态对触屏与鼠标用户都不够明显。 */}
          <Button
            size="icon"
            variant="ghost"
            icon={<CloseIcon />}
            aria-label="关闭"
            onClick={onClose}
          />
        </div>
        {children === undefined ? null : (
          <div className="px-a16 pb-a12 text-body text-ink-light">{children}</div>
        )}
        {footer === undefined ? null : (
          <div className="avid-hair-t flex items-center justify-end gap-a8 px-a16 py-a12">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
