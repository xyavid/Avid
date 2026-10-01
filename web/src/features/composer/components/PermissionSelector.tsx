/*
 * 权限选择器：三轴预设的入口。
 *
 * 为什么自己写下拉而不是用原生 <select>：每一档都要显示 label + 一句代价说明
 * （"关掉了什么" / "谁来裁决"），原生 option 里的第二行在跨浏览器上不可控。
 * 代价是键盘交互要自己写——这里只做到「Tab 可达 + Esc 关闭 + 点击外部关闭」，
 * 没做方向键漫游（选项本身就是 <button>，Tab 能走完全部）。
 *
 * 沙箱状态**按服务端实测的事实显示**，不按模式名反推：`GET /api/meta` 的
 * `capabilities.sandbox` 可能报 backend 不可用（bwrap 缺失 / Landlock ABI 不够），
 * 这时即便选了 `manual`，沙箱实际上也不在。反推会让界面撒一个用户查不出来的谎。
 *
 * 选 `full` 的显式确认**不在这里**：这一层只负责"用户点了哪一档"，
 * 确认弹窗属于 Composer 的编排（它同时持有草稿与提交动作）。
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'

import type { PermissionMode, SandboxState } from '../../../api/types'
import { AlertTriangleIcon, ShieldIcon } from '../../../ui/icons'
import { Button, Tooltip, cx } from '../../../ui/primitives'
import { PERMISSION_PRESETS, presetOf } from '../lib/permission'

export interface PermissionSelectorProps {
  value: PermissionMode
  onChange: (mode: PermissionMode) => void
  sandbox: SandboxState | null
  disabled?: boolean
}

export function PermissionSelector({
  value,
  onChange,
  sandbox,
  disabled = false,
}: PermissionSelectorProps): ReactElement {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const preset = presetOf(value)

  useEffect(() => {
    if (!open) return
    // 用 pointerdown 而不是 click：点击面板外时要在**按下**的瞬间就收起，
    // 否则被点到的元素会先收到 click，出现"点了别处却触发了那个按钮"的错觉。
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current
      if (root === null) return
      if (event.target instanceof Node && !root.contains(event.target)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  // 外部把选择器禁用时（比如运行开始、会话变为只读）要顺手收起已经展开的下拉：
  // 否则会留下"按钮点不动、菜单还开着"的死界面。
  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="sm"
        icon={<ShieldIcon size={14} />}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((previous) => !previous)}
      >
        {preset.label}
      </Button>

      {open ? (
        <div
          role="listbox"
          aria-label="权限模式"
          className={cx(
            'absolute bottom-full left-0 z-dropdown mb-a6 w-[264px] rounded-sm',
            'border-thin border-hair-strong bg-float p-a4 shadow-2 animate-fade-up',
            'max-h-[60vh] overflow-y-auto',
          )}
        >
          {PERMISSION_PRESETS.map((option) => (
            <button
              key={option.mode}
              type="button"
              role="option"
              aria-selected={option.mode === value}
              onClick={() => {
                setOpen(false)
                // 已经选中的档不重复通知：父层可能据此记账（比如 ack 计数）。
                if (option.mode !== value) onChange(option.mode)
              }}
              className={cx(
                'flex w-full flex-col items-start gap-a2 rounded-xs px-a8 py-a6 text-left',
                'transition-colors duration-fast ease-standard hover:bg-accent-soft',
                option.mode === value && 'bg-accent-soft',
              )}
            >
              <span
                className={cx(
                  'text-caption',
                  option.mode === value ? 'font-medium text-accent-ink' : 'text-ink',
                )}
              >
                {option.label}
              </span>
              <span className="text-hint text-ink-muted">{option.caveat}</span>
            </button>
          ))}

          {/*
            沙箱事实：非空才说，null 表示还没拿到 meta——此时不显示比说错好。
            `network` 的口径**不是**"这个档允不允许出网"，而是
            `policy/sandbox.probe_backend()` 的实测结论：后端能否强制网络隔离
            （`BackendProbe.network_isolation` → `summary()["network"]`）。
            所以 true = 出网可被限制；false = 后端做不到，出网实际不受限。
            按模式名反推（"选了 manual 就等于无出网"）正是这一段要避免的谎。
          */}
          {sandbox === null ? null : (
            <div className="avid-hair-t mt-a4 flex items-center gap-a6 px-a8 pt-a6 text-hint text-ink-muted">
              {sandbox.available ? (
                <Tooltip
                  label={
                    sandbox.landlock_abi === null
                      ? '后端实测可用（真跑过一次隔离命令）'
                      : `内核 Landlock ABI ${sandbox.landlock_abi}`
                  }
                >
                  <span>沙箱可用（{sandbox.backend}）</span>
                </Tooltip>
              ) : (
                <Tooltip label={sandbox.reason ?? '服务端没有给出原因'}>
                  <span className="inline-flex items-center gap-a4 text-warn">
                    <AlertTriangleIcon size={14} />
                    沙箱不可用
                  </span>
                </Tooltip>
              )}
              <span>· {sandbox.network ? '出网可被限制' : '无法限制出网'}</span>
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}
