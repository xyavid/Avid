/**
 * 三个用户模式的取值、缺省回落与提交体组装：**纯函数**，与 UI 无关
 * （`__tests__/permission.test.ts`）。
 *
 * 缺省链是「这次会话视图里显式选过 → 当前会话所属工作区的默认权限 → manual」。
 * 最后那一跳不是摆设：工作区的 `default_permission` 在 DTO 里是可空字段
 * （`WorkspaceRef.default_permission: str | None`），而服务端在 `permission=None`
 * 时自己也会回落到 manual，两侧必须落到同一个值。
 *
 * `full` 的**显式授权**在这条链上只有一处：`needsFullAck` 判定"这个模式要不要 ack"，
 * `buildStartRunInput` 据此填 `full_access_ack`。选择器只负责让用户看见并确认，
 * 不做第二个判断点（两处判断就会漂移成"界面确认了但请求里没带"）。
 *
 * 模式集合在这里列一遍而不是从 `api/types.ts` 的联合类型推导：联合类型在运行时
 * 不存在，而选择器要拿一个可遍历的清单去渲染三档、测试要断言「恰好这三个」。
 */

import type { PermissionMode, StartRunInput } from '../../../api/types'

export const PERMISSION_MODES: readonly PermissionMode[] = ['manual', 'auto', 'full']

/** 两侧共同的兜底：工作区没登记默认权限时用它（= 服务端的 DEFAULT_MODE）。 */
export const DEFAULT_PERMISSION: PermissionMode = 'manual'

/** `full` 会关掉沙箱与网络边界，所以它必须带显式授权。 */
export function needsFullAck(mode: PermissionMode): boolean {
  return mode === 'full'
}

export function isPermissionMode(value: unknown): value is PermissionMode {
  return typeof value === 'string' && (PERMISSION_MODES as readonly string[]).includes(value)
}

/**
 * 这次运行用哪一档。
 *
 * `explicit` 是用户在本次会话视图里的选择（未选过为 null）；`workspaceDefault` 是
 * 会话详情里的 `workspace.default_permission`，可能是 null，也可能是服务端将来加了
 * 而前端还不认识的值——两者都回落，不把它当成「用户选了一个我们不认识的档」。
 */
export function resolvePermissionMode(
  explicit: PermissionMode | null | undefined,
  workspaceDefault: unknown,
): PermissionMode {
  if (isPermissionMode(explicit)) return explicit
  // 工作区默认值**不接受 full**（服务端也不会存它，见 `full 三重锁`）：万一手改的
  // 注册表里出现了它，这里按"不认识的值"回落，而不是把它当成一个合法的缺省档。
  if (isPermissionMode(workspaceDefault) && workspaceDefault !== 'full') return workspaceDefault
  return DEFAULT_PERMISSION
}

/** 提交体组装：composer 发起的每个运行都从这一处出，字段名只在契约类型里拼一次。 */
export function buildStartRunInput(input: {
  prompt: string
  branch: string
  autoApprove: boolean
  permission: PermissionMode
}): StartRunInput {
  return {
    prompt: input.prompt,
    auto_approve: input.autoApprove,
    branch: input.branch,
    permission: input.permission,
    full_access_ack: needsFullAck(input.permission),
  }
}
