/**
 * 错误码 → 文案的边界用例。
 *
 * 为什么值得单独测：**界面在这里做分支，而分支的依据是 `code` 不是人话**
 * （client.ts 的注释把这条写成纪律）。文案本身会反复改，所以断言只抓
 * "不同码必须给出可区分的话"，不把整句话钉死——否则改一个措辞就要改测试。
 */

import { describe, expect, it } from 'vitest'

import { ApiError } from '../../../../api/client'
import { describeWorkspaceError } from '../workspaceError'

describe('describeWorkspaceError', () => {
  it('workspace_invalid 提示路径不存在 / 不是目录，且要用户去核对路径', () => {
    const text = describeWorkspaceError(new ApiError(400, 'workspace_invalid', '无效路径'))
    expect(text).toContain('路径')
  })

  it('workspace_exists 指向已有的那一个（detail 里带 id / name）', () => {
    const text = describeWorkspaceError(
      new ApiError(409, 'workspace_exists', '已登记', { id: 'ws-1', name: 'Avid' }),
    )
    expect(text).toContain('Avid')
    expect(text).toContain('已')
  })

  it('workspace_exists 没有 detail 时也要给出可读的话，不显示 undefined', () => {
    const text = describeWorkspaceError(new ApiError(409, 'workspace_exists', '已登记'))
    expect(text).not.toContain('undefined')
    expect(text.length).toBeGreaterThan(0)
  })

  it('picker_unavailable / 503 提示改用命令行登记（服务端消息里也给这条）', () => {
    const text = describeWorkspaceError(
      new ApiError(503, 'picker_unavailable', '本机没有可用的文件夹选择器'),
    )
    expect(text).toContain('avid workspace add')
  })

  it('服务端若用 workspace_picker_unavailable 这个码也要认（不在同一个命名前缀上）', () => {
    const text = describeWorkspaceError(
      new ApiError(503, 'workspace_picker_unavailable', '没有选择器'),
    )
    expect(text).toContain('avid workspace add')
  })

  it('picker_busy：已经开着一个对话框，不要把用户的选择当成失败', () => {
    const text = describeWorkspaceError(new ApiError(409, 'picker_busy', '对话框已打开'))
    expect(text).toContain('已经')
  })

  it('timeout 与 network_error 分开说：一个该重试，一个是没连上', () => {
    const timeout = describeWorkspaceError(new ApiError(0, 'timeout', '超时'))
    const network = describeWorkspaceError(new ApiError(0, 'network_error', '断网'))
    expect(timeout).not.toBe(network)
    expect(timeout).toContain('重试')
  })

  it('未知错误回落到 error.message，不吞成空串', () => {
    expect(describeWorkspaceError(new Error('服务端崩了'))).toContain('服务端崩了')
    expect(describeWorkspaceError('字符串错误')).toContain('字符串错误')
  })
})
