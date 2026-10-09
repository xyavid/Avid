/**
 * toolLabel cases: one "verb + target" line per built-in tool. Relativization only happens
 * inside the workspace root — showing an outside absolute path as relative would lie.
 */

import { describe, expect, it } from 'vitest'

import { toolLabel } from '../toolLabel'

const ROOT = '/home/me/Avid'

describe('toolLabel：动作 + 目标', () => {
  it('读文件 → 读取 + 相对工作区根的路径', () => {
    expect(toolLabel('read_file', JSON.stringify({ path: `${ROOT}/tests/a.py` }), ROOT)).toEqual({
      icon: 'file-frame',
      verb: '读取',
      target: 'tests/a.py',
    })
  })

  it('工作区外的路径原样显示（相对化会指错文件）', () => {
    expect(toolLabel('read_file', JSON.stringify({ path: '/etc/hosts' }), ROOT).target).toBe('/etc/hosts')
  })

  it('没有工作区根时不做相对化', () => {
    expect(toolLabel('read_file', JSON.stringify({ path: `${ROOT}/a.py` }), null).target).toBe(`${ROOT}/a.py`)
  })

  it('写 / 改 / 查找 / 清单一句话各自成词', () => {
    expect(toolLabel('write_file', JSON.stringify({ path: 'a.py' }), ROOT)).toMatchObject({ verb: '写入', target: 'a.py' })
    expect(toolLabel('edit_file', JSON.stringify({ path: 'a.py' }), ROOT)).toMatchObject({ verb: '编辑', target: 'a.py' })
    expect(toolLabel('glob', JSON.stringify({ pattern: '**/*.tsx' }), ROOT)).toMatchObject({ verb: '查找', target: '**/*.tsx' })
    expect(toolLabel('grep_search', JSON.stringify({ pattern: '会话存储', path: 'src' }), ROOT)).toMatchObject({
      verb: '搜索',
      target: '会话存储',
    })
    expect(toolLabel('todo_write', JSON.stringify({ todos: [{}, {}, {}] }), ROOT)).toMatchObject({ verb: '更新清单', target: '3 项' })
    expect(toolLabel('load_skill', JSON.stringify({ name: 'review' }), ROOT)).toMatchObject({ verb: '加载技能', target: 'review' })
  })

  it('bash → 执行 + 命令首行（多行命令只看第一行）', () => {
    expect(toolLabel('bash', JSON.stringify({ command: 'uv run pytest -q \\\n  && echo done' }), ROOT)).toMatchObject({
      icon: 'terminal',
      verb: '执行',
      target: 'uv run pytest -q \\',
    })
  })

  it('subagent 有自己的图标（bot），不与「分支」共用 git-branch', () => {
    const one = JSON.stringify({ tasks: [{ description: '前端改造', prompt: '...' }] })
    const two = JSON.stringify({ tasks: [{ description: '前端改造', prompt: '...' }, { description: '后端收尾', prompt: '...' }] })

    expect(toolLabel('subagent', one, ROOT)).toMatchObject({ icon: 'bot', verb: '子智能体', target: '前端改造' })
    expect(toolLabel('subagent', two, ROOT)).toMatchObject({ verb: '子智能体', target: '2 个子任务' })
  })

  it('未登记的工具回落：动词 = 工具名，目标 = 压缩后的参数', () => {
    expect(toolLabel('mcp__fs__stat', JSON.stringify({ path: 'x' }), ROOT)).toMatchObject({
      icon: 'file-frame',
      verb: 'mcp__fs__stat',
      target: '{"path":"x"}',
    })
  })

  it('参数不是合法 JSON 时不抛异常，原样当目标', () => {
    expect(toolLabel('bash', '{oops', ROOT)).toMatchObject({ verb: '执行', target: '{oops' })
  })

  it('字段缺失（模型传了空参数）时目标为空串而不是 undefined', () => {
    expect(toolLabel('read_file', '{}', ROOT).target).toBe('')
  })
})
