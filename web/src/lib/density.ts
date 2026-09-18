/**
 * 界面密度：纯 UI 偏好的**类型**。
 *
 * 放 L0/L1 而不是 `state/uiStore`：密度是 `ui/patterns` 的 props（卡片内边距与
 * 输出区最大高度），L1 不该 import 界面域。`uiStore` 仍然拥有它的**值**（并持久化）。
 */
export type Density = 'compact' | 'comfy'
