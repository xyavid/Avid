/**
 * 权限三轴预设的**单点**展开。
 *
 * 为什么要有这个文件：`manual` / `auto` / `full` 是**三轴预设**（approval × sandbox ×
 * network），不是同一条信任边界上的三个刻度。这三个轴的取值散在 UI、请求体、确认弹窗三处
 * 就会出现"选择器说没沙箱、请求体其实还开着沙箱"这类漂移——所以表只写一份，
 * 选择器、确认弹窗与 `buildStartRunInput` 全部从 `PERMISSION_PRESETS` 派生。
 *
 * 表以 `docs/guide/web-ui.md` §2 的「permission 是三轴预设」表为准，单测逐条钉住。
 *
 * 安全关键的一条：`full` 会关掉沙箱与出网限制，服务端要求请求体带
 * `full_access_ack: true`，缺了直接 422。ack 的"有意识动作"由两件事保证——
 * 界面上的显式确认（Composer）与这里的 `buildStartRunInput`：**只有 full 档、且调用方
 * 明确确认过才写 ack**，任何调用点都不必各自记得这条规则。
 */

import type { PermissionMode, StartRunInput } from '../../../api/types'

export interface PermissionPreset {
  mode: PermissionMode
  label: string
  approval: 'user' | 'classifier' | 'none'
  sandbox: 'workspace' | 'disabled'
  network: 'restricted' | 'open'
  /** full 必须显式授权：请求体要带 full_access_ack: true，否则服务端 422。 */
  needsFullAck: boolean
  /** 一句话说明"这个档关掉了什么"，用于选择器与确认弹窗。 */
  caveat: string
}

/**
 * 只有 `full` 关掉了东西，所以只有它带 caveat 式的"代价"；
 * 另外两档的 caveat 描述的是"谁来裁决"，不是"关掉了什么"——这比留空更好用：
 * 选择器里每一行都有第二行说明，用户不必先记住三个档的名字。
 */
const PRESET_BY_MODE: Record<PermissionMode, PermissionPreset> = {
  manual: {
    mode: 'manual',
    label: '手动',
    approval: 'user',
    sandbox: 'workspace',
    network: 'restricted',
    needsFullAck: false,
    caveat: '越界、危险命令与 .env 之类会停下来问你',
  },
  auto: {
    mode: 'auto',
    label: '自动',
    approval: 'classifier',
    sandbox: 'workspace',
    network: 'restricted',
    needsFullAck: false,
    caveat: '确定性分类器代你裁决，判不准即拒',
  },
  full: {
    mode: 'full',
    label: '完全访问',
    approval: 'none',
    sandbox: 'disabled',
    network: 'open',
    needsFullAck: true,
    caveat: '关掉沙箱与出网限制，命令以 Avid 进程的权限执行',
  },
}

/** 顺序即选择器里的展示顺序：从最保守到最放开，最后一个需要确认。 */
export const PERMISSION_PRESETS: readonly PermissionPreset[] = [
  PRESET_BY_MODE.manual,
  PRESET_BY_MODE.auto,
  PRESET_BY_MODE.full,
]

export function presetOf(mode: PermissionMode): PermissionPreset {
  // 用 Record 而不是 find：mode 是封闭联合，编译器保证查表命中；
  // 服务端将来加了新模式会先在类型上炸掉，而不是在运行时静默拿到 undefined。
  return PRESET_BY_MODE[mode]
}

/**
 * 组装起运行请求：按模式统一决定要不要带 `full_access_ack`。
 *
 * 只在 full 档且 `fullAck === true` 时写这个字段，其余情况**整个键不出现**——
 * 服务端把"缺 ack"当成 422 的判据，写 `false` 与不写没有区别，少一个键就少一处分歧。
 */
export function buildStartRunInput(input: {
  prompt: string
  mode: PermissionMode
  branch?: string
  autoApprove?: boolean
  fullAck?: boolean
}): StartRunInput {
  const preset = presetOf(input.mode)
  const out: StartRunInput = {
    prompt: input.prompt,
    permission: input.mode,
  }
  if (input.branch !== undefined) out.branch = input.branch
  if (input.autoApprove !== undefined) out.auto_approve = input.autoApprove
  if (preset.needsFullAck && input.fullAck === true) out.full_access_ack = true
  return out
}
