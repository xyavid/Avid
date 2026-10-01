/**
 * 工作区域错误码 → 中文文案的**单点**。
 *
 * 为什么按 `code` 分支而不是按 `message`：`client.ts` 把"界面按服务端的稳定错误码
 * 分支"写成了纪律，文案是给用户看的、随时会改，错误码是对外的接口、不会随文案改。
 *
 * 为什么集中在这里：同一个码会经两条路径到达界面——"选择文件夹"失败与"登记"失败。
 * 分散写必然出现"一边说清楚了、另一边只有一句失败"。
 *
 * 各码的来源（`src/avid/svc/errors.py` / `svc/workspaces.py`）：
 *   · `workspace_invalid`  400  路径不存在 / 不是目录 / 权限档非法
 *   · `workspace_exists`   409  已登记，`detail` 带 `{id, name, root}`
 *   · `picker_busy`        409  已有一个选择器对话框开着
 *   · `picker_unavailable` 503  本机没有可用的系统选择器
 *   · `picker_failed`      500  选择器起来了但失败了
 *   · `workspace_bound`    409  不许移除进程绑定的那个（理论上界面已提前禁用按钮）
 */

import { ApiError } from '../../../api/client'

/** `detail` 是任意 JSON，取值前必须收窄——服务端形状变了不该让界面显示 `undefined`。 */
function stringField(detail: Record<string, unknown>, key: string): string | null {
  const value = detail[key]
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

export function describeWorkspaceError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'workspace_invalid':
        return '这个路径用不了：确认目录存在、可访问，并且是文件夹而不是文件。'
      case 'workspace_exists': {
        const label = stringField(error.detail, 'name') ?? stringField(error.detail, 'root')
        return label === null
          ? '这个文件夹已经登记过了，在列表里找它即可，不用再加一次。'
          : `这个文件夹已经登记过了：「${label}」，在列表里找它即可。`
      }
      case 'picker_unavailable':
      // 任务描述里出现过 `workspace_picker_unavailable` 这个写法，而服务端用的是
      // `picker_unavailable`。两个都认：认错码的代价是这里退回一句看不出所以然的话。
      case 'workspace_picker_unavailable':
        return '这台机器没有可用的系统文件夹选择器。请在下方手动填写绝对路径，或在终端运行 `avid workspace add <路径>`。'
      case 'picker_busy':
        return '已经有一个文件夹选择对话框开着，先处理它再试一次。'
      case 'picker_failed':
        return '文件夹选择器启动后失败了，改用手动填写路径更稳。'
      case 'workspace_bound':
        return '这是当前进程绑定的工作区，不能从候选列表里移除。'
      case 'timeout':
        return '请求超时：本地服务没有在预算内响应，可以重试一次。'
      case 'network_error':
        return '没能连上本地服务：确认 `avid web` 还在运行。'
      case 'aborted':
        return '请求已取消。'
      default:
        return error.message
    }
  }
  // 非 ApiError 的来源：组件的同步校验、以及将来可能出现的编程错误。
  // **不吞**：原文照说，比"操作失败"这种什么都说明不了的句子有用。
  if (error instanceof Error) return error.message
  return String(error)
}
