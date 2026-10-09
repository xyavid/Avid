import { describe, expect, it } from 'vitest'

import { highlight } from '../highlight'

/** Texts of the tokens of one kind, for assertions. */
function ofKind(code: string, lang: string | null, kind: string): string[] {
  return highlight(code, lang)
    .filter((t) => t.kind === kind)
    .map((t) => t.text)
}

describe('代码高亮', () => {
  it('最重要的不变量：token 拼回去逐字等于原文（高亮不许吞字、不许加字）', () => {
    const samples: Array<[string | null, string]> = [
      ['python', 'def f(x):\n    """doc"""\n    return f"v={x}"  # 注释\n'],
      ['ts', 'const a: number = 0x1f\n// 行注释\nconst t = `hi ${a}`\n'],
      ['json', '{"a": [1, 2.5, true, null], "b": "x"}'],
      ['bash', 'if [ -f "$PWD/x" ]; then echo \'ok\' > f; fi  # 注释\n'],
      ['css', '.a { color: #fff; margin: 0 4px; } /* c */'],
      ['html', '<div class="x" data-y=\'1\'>文字 &amp; 更多</div>'],
      ['svg', '<svg xmlns="http://www.w3.org/2000/svg"><rect width="4"/></svg>'],
      ['yaml', 'a: 1\nb:\n  - "x"  # 注释\n'],
      ['sql', "SELECT id FROM t WHERE name = 'a' -- c\n"],
      [null, '纯文本 no lang'],
      ['未知语言', 'whatever ** not code'],
    ]
    for (const [lang, code] of samples) {
      expect(highlight(code, lang).map((t) => t.text).join(''), `${lang} 拼接`).toBe(code)
    }
  })

  it('python：关键字、函数名、字符串、数字、注释各归各', () => {
    const code = 'def add(a, b):\n    return a + 1  # 求和'
    expect(ofKind(code, 'python', 'keyword')).toContain('def')
    expect(ofKind(code, 'python', 'keyword')).toContain('return')
    expect(ofKind(code, 'python', 'function')).toContain('add')
    expect(ofKind(code, 'python', 'number')).toContain('1')
    expect(ofKind(code, 'python', 'comment')).toEqual(['# 求和'])
  })

  it('python：三引号与 f-string 整段算字符串，里面的 # 不是注释', () => {
    const code = '"""a # b"""\nx = f"{v} # not comment"'
    expect(ofKind(code, 'python', 'comment')).toEqual([])
    expect(ofKind(code, 'python', 'string')).toEqual(['"""a # b"""', 'f"{v} # not comment"'])
  })

  it('ts：行注释、模板串（含 ${}）、关键字与数字', () => {
    const code = 'const n = 12 // 说明\nconst s = `a${n}b`'
    expect(ofKind(code, 'ts', 'keyword')).toContain('const')
    expect(ofKind(code, 'ts', 'number')).toContain('12')
    expect(ofKind(code, 'ts', 'comment')).toEqual(['// 说明'])
    expect(ofKind(code, 'ts', 'string')).toEqual(['`a${n}b`'])
  })

  it('json：键与值都是字符串，true/null 是关键字', () => {
    const code = '{"a": true, "n": 2}'
    expect(ofKind(code, 'json', 'string')).toEqual(['"a"', '"n"'])
    expect(ofKind(code, 'json', 'keyword')).toEqual(['true'])
    expect(ofKind(code, 'json', 'number')).toEqual(['2'])
  })

  it('bash：注释、命令与字符串分开；$ 变量不误判成注释', () => {
    const code = 'echo "$HOME/x"  # 注释'
    expect(ofKind(code, 'bash', 'comment')).toEqual(['# 注释'])
    expect(ofKind(code, 'bash', 'string')).toEqual(['"$HOME/x"'])
    expect(ofKind(code, 'bash', 'keyword')).toContain('echo')
  })

  it('html/svg：标签名是关键字，属性值是字符串，注释整段吃掉', () => {
    const code = '<rect width="4"/> <!-- 注 -->'
    expect(ofKind(code, 'html', 'keyword')).toContain('rect')
    expect(ofKind(code, 'html', 'string')).toEqual(['"4"'])
    expect(ofKind(code, 'html', 'comment')).toEqual(['<!-- 注 -->'])
  })

  it('没闭合的字符串不会吃掉整段（流式里常见），且不卡死', () => {
    const code = 'x = "没闭合\n下一行 = 1'
    const tokens = highlight(code, 'python')
    expect(tokens.map((t) => t.text).join('')).toBe(code)
    expect(ofKind(code, 'python', 'number')).toContain('1')
  })

  it('空输入与未知语言都不炸', () => {
    expect(highlight('', 'python')).toEqual([])
    expect(highlight('随便什么', 'brainfuck').map((t) => t.kind)).toContain('plain')
  })
})
