/**
 * 右侧 dock 的界面域状态（localStorage 持久化——界面域状态归前端的约定，
 * appearance.ts 同一模式）。只持久化开合与激活面板：宽度、面板内容不进存储。
 *
 * 默认**收起**（阶段 54 用户裁定）：右列不是常驻栏，用的时候点顶栏那个按钮，
 * 打开先给**选择页**（面板列表）——「收起 → 点开 → 选」是它的三段式。
 * 由代码选中某个面板（点子智能体卡那种）是另一回事：那是明确的意图，直接进面板，
 * 不再多问一层。选择页在 `choosing` 里，和 `open` 分开——它不是一种面板。
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

const DEFAULTS: StoredDock = { open: false, active: 'files' }

function load(): StoredDock {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return DEFAULTS
    const parsed = JSON.parse(raw) as Partial<StoredDock>
    return {
      open: parsed.open === true,
      active: PANEL_IDS.includes(parsed.active as DockPanelId)
        ? (parsed.active as DockPanelId)
        : DEFAULTS.active,
    }
  } catch {
    return DEFAULTS
  }
}

export function useDock() {
  const [open, setOpen] = useState(() => load().open)
  const [active, setActive] = useState<DockPanelId>(() => load().active)
  // 选择页（面板列表）：默认收起时它是「打开的第一屏」，所以随 open 一起只在内存里
  const [choosing, setChoosing] = useState(false)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ open, active }))
  }, [open, active])

  const toggle = useCallback(() => {
    setOpen((v) => {
      if (!v) setChoosing(true) // 手动打开：先给选择页
      return !v
    })
  }, [])
  const close = useCallback(() => setOpen(false), [])
  const choose = useCallback((on: boolean) => setChoosing(on), [])
  const select = useCallback((id: DockPanelId) => {
    setActive(id)
    setChoosing(false) // 明确的意图：直接进面板
    setOpen(true)
  }, [])

  return { open, active, choosing, toggle, close, choose, select }
}
