/**
 * 纯展示口径：把"可能缺失的值"变成界面上的字。
 *
 * 为什么单独成文件：**null ≠ 0** 是本仓贯穿前后端的一条约定
 * （见 `api/types.ts` 关于 usage 可空字段的注释）。渲染层各自写
 * `value ?? '—'` 看着无害，但"0 要显示成 0、空串要显示成 —"这两条一旦分散，
 * 总有人写成 `value || '—'`（0 被吞掉）或 `value ?? '—'`（空串显示成空白）。
 * 收在一个纯函数里，边界就只有一处可测。
 */

/** 缺失（null / undefined / 空白串）显示「—」；**0 是值，照实显示**。 */
export function orDash(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed === '' ? '—' : trimmed
  }
  return String(value)
}

/**
 * 路径末段，用作工作区的显示名回落。
 *
 * 根路径返回 `/` 而不是空串：空串在界面上会渲染成一片空白，读起来像渲染失败；
 * 而"这个工作区就是根目录"是一个需要说出来的事实。
 */
export function leafOf(root: string): string {
  const trimmed = root.replace(/\/+$/, '')
  if (trimmed === '') return '/'
  const segments = trimmed.split('/')
  return segments[segments.length - 1] ?? root
}

/**
 * 工作区显示名：`name` 有值就用它，否则回落到路径末段。
 *
 * `name` 可空是服务端 DTO 的事实（`WorkspaceOut` 继承 `WorkspaceRef`，见 `api/types.ts`），
 * 而登记时不给名字是完全正常的用法——所以这条回落不是"兜底"，是主路径之一。
 */
export function workspaceLabel(workspace: { name: string | null; root: string }): string {
  const name = workspace.name?.trim()
  if (name !== undefined && name !== '') return name
  return leafOf(workspace.root)
}

/**
 * 默认权限徽标文案。工作区的默认权限只可能是 `manual` / `auto`
 * （服务端 `Literal["manual", "auto"]`，见 `schemas.py:115`）；
 * 拿到别的值时返回 `null` = 不显示徽标——**不猜**，也不把 `full` 说成某一档。
 */
export function permissionLabel(permission: string | null): string | null {
  if (permission === 'manual') return '手动'
  if (permission === 'auto') return '自动'
  return null
}
