/**
 * 待办清单从会话条目里推导（纯函数，脱离 DOM 断言）。
 *
 * 这份清单的**权威副本**是 transcript 里 `todo_write` 那次调用的 `arguments.todos`——
 * 工具每次都**全量提交**整个列表，所以"最后一次调用"就是"当前计划"。于是前端不需要新接口
 * 也不需要新存储，只要一个可靠的取法。这个文件把取法的边界钉死：
 *
 *   1. 从没调过 → `null`（面板据此整块不渲染，"没有计划"和"计划是空的"是两件事）；
 *   2. 调过多次 → 取**最后一次合法**的（见第 4 条）；
 *   3. 条目里的畸形项丢掉、不牵连整个列表（工具侧会拒收非法清单，历史里可能留着被拒的调用）；
 *   4. `todos` 不是数组的调用**跳过**（那次调用没被服务端接受，"当前计划"仍是上一次合法的），
 *      全部都不合法时才是 `null`；
 *   5. 只认 `todo_write`，别的工具的参数里恰好有 `todos` 不算。
 */

import { describe, expect, it } from 'vitest'

import type { ToolRun } from '../../../../lib/timeline'
import { latestTodos } from '../todos'

function run(tool: string, args: Record<string, unknown>, seq = 1): ToolRun {
  return {
    toolCallId: `call-${tool}-${seq}`,
    tool,
    arguments: args,
    status: 'ok',
    truncated: false,
    contentChars: 0,
    durationMs: 0,
    seq,
    at: seq,
  }
}

const todoRun = (todos: unknown, seq = 1) => run('todo_write', { todos }, seq)

describe('latestTodos', () => {
  it('从没调过 todo_write：null（面板不渲染）', () => {
    expect(latestTodos([])).toBeNull()
    expect(latestTodos([run('bash', { command: 'ls' })])).toBeNull()
  })

  it('一次调用：原样取出 content 与 status，顺序保持', () => {
    expect(
      latestTodos([
        todoRun([
          { content: '读代码', status: 'completed' },
          { content: '改样式', status: 'in_progress' },
          { content: '写测试', status: 'pending' },
        ]),
      ]),
    ).toEqual([
      { content: '读代码', status: 'completed' },
      { content: '改样式', status: 'in_progress' },
      { content: '写测试', status: 'pending' },
    ])
  })

  it('多次调用：取最后一次（列表变短也算更新）', () => {
    expect(
      latestTodos([
        todoRun([{ content: '一', status: 'pending' }, { content: '二', status: 'pending' }]),
        todoRun([{ content: '一', status: 'completed' }], 2),
      ]),
    ).toEqual([{ content: '一', status: 'completed' }])
  })

  it('最后一次的 todos 不是数组：跳过它，回落到上一次合法的', () => {
    expect(
      latestTodos([
        todoRun([{ content: '还在计划里', status: 'pending' }]),
        todoRun('不是数组', 2),
      ]),
    ).toEqual([{ content: '还在计划里', status: 'pending' }])
  })

  it('全部调用的 todos 都不合法：null', () => {
    expect(latestTodos([todoRun(undefined), todoRun({ content: 'x' }, 2)])).toBeNull()
  })

  it('畸形项丢掉、其余保留（缺 content / 非法 status / 不是对象）', () => {
    expect(
      latestTodos([
        todoRun([
          { content: '好的', status: 'pending' },
          { content: '缺 status' },
          { content: '错状态', status: 'doing' },
          { status: 'pending' },
          null,
          '字符串',
          { content: '也是好的', status: 'completed' },
        ]),
      ]),
    ).toEqual([
      { content: '好的', status: 'pending' },
      { content: '也是好的', status: 'completed' },
    ])
  })

  it('空清单：返回 []（调用过、但计划是空的）', () => {
    expect(latestTodos([todoRun([])])).toEqual([])
  })

  it('其它工具的参数里有 todos 不算数', () => {
    expect(latestTodos([run('read_file', { todos: [{ content: 'x', status: 'pending' }] })])).toBeNull()
  })
})
