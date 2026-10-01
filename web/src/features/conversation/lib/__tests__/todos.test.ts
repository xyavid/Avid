import { describe, expect, it } from 'vitest'

import { latestTodos } from '../todos'
import type { ToolRun } from '../../../../events/reducer'

let seq = 0

/** 造一条工具运行记录；默认是"成功的 todo_write"。 */
function toolRun(extra: Partial<ToolRun> = {}): ToolRun {
  seq += 1
  return {
    toolCallId: `c${seq}`,
    tool: 'todo_write',
    args: {},
    status: 'ok',
    resultText: '',
    durationMs: 12,
    startedAt: 1_700_000_000_000 + seq,
    ...extra,
  }
}

const LIST_ARGS = {
  todos: [
    { content: '读 pyproject.toml', status: 'completed' },
    { content: '写测试', status: 'in_progress' },
    { content: '跑门禁', status: 'pending' },
  ],
}

describe('latestTodos', () => {
  it('从最近一次 todo_write 的参数里解析清单', () => {
    const todos = latestTodos([toolRun({ args: LIST_ARGS })])

    expect(todos).toEqual([
      { id: 'todo-0', content: '读 pyproject.toml', status: 'completed' },
      { id: 'todo-1', content: '写测试', status: 'in_progress' },
      { id: 'todo-2', content: '跑门禁', status: 'pending' },
    ])
  })

  it('内核里没有 id，用序号合成稳定 id', () => {
    const todos = latestTodos([toolRun({ args: { todos: [{ content: 'a', status: 'pending' }] } })])

    expect(todos?.[0]?.id).toBe('todo-0')
  })

  it('空清单是"已清空"，返回空数组而不是 null', () => {
    expect(latestTodos([toolRun({ args: { todos: [] } })])).toEqual([])
  })

  it('没有调用过 todo_write 时返回 null', () => {
    expect(latestTodos([])).toBeNull()
    expect(latestTodos([toolRun({ tool: 'read_file', args: { path: 'a.py' } })])).toBeNull()
  })

  it('解析失败一律返回 null，不抛', () => {
    // todos 不是数组
    expect(latestTodos([toolRun({ args: { todos: 'read it' } })])).toBeNull()
    // 缺 todos 键
    expect(latestTodos([toolRun({ args: { items: [] } })])).toBeNull()
    // status 不在内核词表里（policy/todo.py 只认 pending/in_progress/completed）
    expect(latestTodos([toolRun({ args: { todos: [{ content: 'a', status: 'done' }] } })])).toBeNull()
    // 缺 content
    expect(latestTodos([toolRun({ args: { todos: [{ status: 'pending' }] } })])).toBeNull()
    // 项不是对象
    expect(latestTodos([toolRun({ args: { todos: ['a'] } })])).toBeNull()
  })

  it('参数以 JSON 字符串到达时先解开再解析', () => {
    const todos = latestTodos([toolRun({ args: { todos: JSON.stringify(LIST_ARGS.todos) } })])

    expect(todos).toHaveLength(3)
    expect(latestTodos([toolRun({ args: { todos: '{ 坏 json' } })])).toBeNull()
  })

  it('多工具多条 todo_write 时取最近的一条', () => {
    const older = toolRun({
      args: { todos: [{ content: '旧清单', status: 'pending' }] },
      startedAt: 1,
    })
    const newer = toolRun({
      args: { todos: [{ content: '新清单', status: 'in_progress' }] },
      startedAt: 2,
    })

    const todos = latestTodos([older, toolRun({ tool: 'read_file' }), newer])

    expect(todos).toEqual([{ id: 'todo-0', content: '新清单', status: 'in_progress' }])
  })

  it('最近一次调用失败时回落到上一个成功的清单', () => {
    // 失败的 todo_write 没有改动内核里的清单，拿它的参数当"当前状态"会显示一份并不存在的清单。
    const ok = toolRun({ args: { todos: [{ content: '生效的', status: 'pending' }] }, startedAt: 1 })
    const failed = toolRun({ args: { todos: [] }, status: 'error', startedAt: 2 })
    const denied = toolRun({ args: { todos: [] }, status: 'denied', startedAt: 3 })

    expect(latestTodos([ok, failed, denied])).toEqual([
      { id: 'todo-0', content: '生效的', status: 'pending' },
    ])
  })
})
