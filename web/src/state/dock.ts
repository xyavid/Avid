/**
 * 右侧 dock 的界面域状态（localStorage 持久化——界面域状态归前端的约定，
 * appearance.ts 同一模式）。只持久化开合与激活面板：宽度、面板内容不进存储。
 *
 * 默认**开着**：右列是常驻面板（占位而非浮层），关掉它是「给对话腾地方」的例外动作，
 * 不是默认状态。存储键跟着换了名字：旧值记的是浮层时代的「露不露出来」，
 * 语义不同，照读会让升级后的第一屏凭空是收起态。
 */

import { useCallback, useEffect, useState } from 'react'

// 阶段 53：删掉「进程」与「审查」——审批在输入区上方那条常驻（权威展示），
// 进程读数在时间线上逐段可见，右列不必再摆一份。旧存储值由下面的白名单回落默认面板。
// 阶段 54：加「临时对话」——它是一次性的（离开面板即销毁），所以不进存储的语义问题：
// 存的是一个面板名，不是那个会话。
export type DockPanelId = 'files' | 'subagents' | 'scratch' | 'terminal'

const STORAGE_KEY = 'avid.dock.column'
const PANEL_IDS: DockPanelId[] = ['files', 'subagents', 'scratch', 'terminal']

type StoredDock = { open: boolean; active: DockPanelId }

function load(): StoredDock {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return { open: true, active: 'files' }
    const parsed = JSON.parse(raw) as Partial<StoredDock>
    return {
      open: parsed.open !== false,
      active: PANEL_IDS.includes(parsed.active as DockPanelId)
        ? (parsed.active as DockPanelId)
        : 'files',
    }
  } catch {
    return { open: true, active: 'files' }
  }
}

export function useDock() {
  const [open, setOpen] = useState(() => load().open)
  const [active, setActive] = useState<DockPanelId>(() => load().active)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ open, active }))
  }, [open, active])

  const toggle = useCallback(() => setOpen((v) => !v), [])
  const close = useCallback(() => setOpen(false), [])
  const select = useCallback((id: DockPanelId) => {
    setActive(id)
    setOpen(true) // 点面板图标即展开：收起态下选面板是最自然的展开方式
  }, [])

  return { open, active, toggle, close, select }
}
