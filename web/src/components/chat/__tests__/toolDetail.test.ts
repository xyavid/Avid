import { describe, expect, it } from 'vitest'

import { toolDetail } from '../toolDetail'

function args(value: unknown): string {
  return JSON.stringify(value)
}

describe('toolDetail：文件类工具的详情视图', () => {
  it('edit_file：旧文与新文都在参数里 → 差异视图，结果文本作注脚', () => {
    const detail = toolDetail(
      'edit_file',
      args({ path: 'src/a.py', old_string: 'x\ny', new_string: 'x\nz' }),
      '已替换 src/a.py 中的 1 处文本',
      'ok',
    )

    expect(detail).toEqual({
      kind: 'diff',
      lang: 'python',
      before: 'x\ny',
      after: 'x\nz',
      note: '已替换 src/a.py 中的 1 处文本',
    })
  })

  it('edit_file 还在跑：差异照给（模型正要做的事就是它），只是还没有注脚', () => {
    const detail = toolDetail(
      'edit_file',
      args({ path: 'a.md', old_string: '旧', new_string: '新' }),
      null,
      'running',
    )

    expect(detail).toEqual({ kind: 'diff', lang: 'markdown', before: '旧', after: '新', note: null })
  })

  it('失败的调用不给视图：文件没被改动，画出来的差异是假的', () => {
    const failed = toolDetail(
      'edit_file',
      args({ path: 'a.py', old_string: 'x', new_string: 'y' }),
      '错误：old_string 在文件中不存在，未做任何修改',
      'failed',
    )

    expect(failed).toBeNull()
  })

  it('edit_file 两段一模一样：没有改动可画，回落原文', () => {
    const same = toolDetail('edit_file', args({ path: 'a.py', old_string: 'x', new_string: 'x' }), '已替换 a.py 中的 1 处文本', 'ok')

    expect(same).toBeNull()
  })

  it('read_file：结果是内容本身，行号从 offset 起数（默认 1）', () => {
    const plain = toolDetail('read_file', args({ path: 'src/a.py' }), 'l1\nl2', 'ok')
    const offset = toolDetail('read_file', args({ path: 'src/a.py', offset: 100 }), 'l100', 'ok')

    expect(plain).toEqual({ kind: 'code', lang: 'python', text: 'l1\nl2', startLine: 1, note: null })
    expect(offset).toEqual({ kind: 'code', lang: 'python', text: 'l100', startLine: 100, note: null })
  })

  it('read_file 没有扩展名：语言标为 null（渲染层显示「文本」）', () => {
    const detail = toolDetail('read_file', args({ path: 'Makefile' }), 'all:', 'ok')

    expect(detail).toEqual({ kind: 'code', lang: null, text: 'all:', startLine: 1, note: null })
  })

  it('write_file 新建：整篇都是新增，按差异视图画', () => {
    const detail = toolDetail('write_file', args({ path: 'new.py', content: 'a\nb' }), '已新建 new.py（2 行，3 字符）', 'ok')

    expect(detail).toEqual({
      kind: 'diff',
      lang: 'python',
      before: '',
      after: 'a\nb',
      note: '已新建 new.py（2 行，3 字符）',
    })
  })

  it('write_file 覆盖：旧内容无从得知（它已经被写掉了），只画写入的新内容，不假装对比', () => {
    const detail = toolDetail('write_file', args({ path: 'a.py', content: 'a\nb' }), '已覆盖 a.py（2 行，3 字符）', 'ok')

    expect(detail).toEqual({
      kind: 'code',
      lang: 'python',
      text: 'a\nb',
      startLine: 1,
      note: '已覆盖 a.py（2 行，3 字符）',
    })
  })

  it('write_file 还在跑：新建还是覆盖还没结论，等结果再画', () => {
    expect(toolDetail('write_file', args({ path: 'a.py', content: 'x' }), null, 'running')).toBeNull()
  })

  it('认不出的结果措辞（内核改了文案）→ 不给视图，回落原文', () => {
    expect(toolDetail('write_file', args({ path: 'a.py', content: 'x' }), '写好了', 'ok')).toBeNull()
  })

  it('参数不是合法 JSON / 缺字段 → 不给视图', () => {
    expect(toolDetail('edit_file', '{"path": "a.py"', '已替换', 'ok')).toBeNull()
    expect(toolDetail('edit_file', args({ path: 'a.py', old_string: 'x' }), '已替换', 'ok')).toBeNull()
    expect(toolDetail('read_file', args({}), '内容', 'ok')).toBeNull()
  })

  it('其余工具不给详情（命令输出、清单、子任务…各有各的样子）', () => {
    expect(toolDetail('bash', args({ command: 'ls' }), 'a.py', 'ok')).toBeNull()
    expect(toolDetail('glob', args({ pattern: '*.py' }), 'a.py', 'ok')).toBeNull()
    expect(toolDetail('mcp__x__y', args({}), 'ok', 'ok')).toBeNull()
  })
})
