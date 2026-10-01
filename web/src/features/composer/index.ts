/*
 * 输入区（composer）的公开面。
 *
 * 组装层（页面 / 布局）只从这里 import：`Composer` 与 `ApprovalBar` 是它需要的两个组件，
 * 其余名字留给测试与将来的细粒度复用。纯函数（权限预设 / 用量格式化）一并转出，
 * 这样"什么算输入区的契约"有一个可数的清单。
 */

export { PERMISSION_PRESETS, buildStartRunInput, presetOf } from './lib/permission'
export type { PermissionPreset } from './lib/permission'
export { compactUsage, contextSegments } from './lib/usage'

export { ApprovalBar } from './components/ApprovalBar'
export type { ApprovalBarProps } from './components/ApprovalBar'
export { Composer } from './components/Composer'
export type { ComposerProps } from './components/Composer'
export { PermissionSelector } from './components/PermissionSelector'
export type { PermissionSelectorProps } from './components/PermissionSelector'
export { UsageMeter } from './components/UsageMeter'
export type { UsageMeterProps } from './components/UsageMeter'
