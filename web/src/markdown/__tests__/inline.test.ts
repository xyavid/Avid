import { describe, expect, it } from 'vitest'

import { parseInline } from '../inline'

/** 行内解析：只认安全的那几种写法，并且**不产生 HTML 字符串**（全程是节点）。 */
describe('markdown 行内解析', () => {
  it('纯文本原样返回', () => {
    expect(parseInline('甲与乙')).toEqual([{ kind: 'text', text: '甲与乙' }])
  })

  it('行内代码：里面的 * 与 [ 不再解析', () => {
    expect(parseInline('`a *b* [c]`')).toEqual([{ kind: 'code', text: 'a *b* [c]' }])
  })

  it('粗体、斜体、删除线', () => {
    expect(parseInline('**粗**')).toEqual([{ kind: 'strong', children: [{ kind: 'text', text: '粗' }] }])
    expect(parseInline('*斜*')).toEqual([{ kind: 'em', children: [{ kind: 'text', text: '斜' }] }])
    expect(parseInline('__粗__')).toEqual([{ kind: 'strong', children: [{ kind: 'text', text: '粗' }] }])
    expect(parseInline('~~删~~')).toEqual([{ kind: 'del', children: [{ kind: 'text', text: '删' }] }])
  })

  it('粗体里可以套行内代码', () => {
    expect(parseInline('**看 `a` 这里**')).toEqual([
      { kind: 'strong', children: [{ kind: 'text', text: '看 ' }, { kind: 'code', text: 'a' }, { kind: 'text', text: ' 这里' }] },
    ])
  })

  it('链接：文本 + 地址，http / https / mailto / 相对锚点都放行', () => {
    expect(parseInline('[文档](https://example.com/a)')).toEqual([
      { kind: 'link', href: 'https://example.com/a', children: [{ kind: 'text', text: '文档' }] },
    ])
    expect(parseInline('[信](mailto:a@b.c)')[0]?.kind).toBe('link')
    expect(parseInline('[锚](#sec)')[0]?.kind).toBe('link')
  })

  it('危险协议不生成链接（javascript: / data:），按纯文本留着给人看', () => {
    expect(parseInline('[点我](javascript:alert(1))')).toEqual([{ kind: 'text', text: '[点我](javascript:alert(1))' }])
    expect(parseInline('[点我](data:text/html;base64,PHNjcmlwdD4=)')).toEqual([
      { kind: 'text', text: '[点我](data:text/html;base64,PHNjcmlwdD4=)' },
    ])
  })

  it('反斜杠转义：\\* 不当标记', () => {
    expect(parseInline('\\*不是斜体\\*')).toEqual([{ kind: 'text', text: '*不是斜体*' }])
  })

  it('没闭合的标记按字面留着（流式里常见）', () => {
    expect(parseInline('**还没写完')).toEqual([{ kind: 'text', text: '**还没写完' }])
    expect(parseInline('`半截代码')).toEqual([{ kind: 'text', text: '`半截代码' }])
    expect(parseInline('[半截](http')).toEqual([{ kind: 'text', text: '[半截](http' }])
  })

  it('原文里的 HTML 一律当文本（渲染层不碰 innerHTML）', () => {
    expect(parseInline('<img src=x onerror=alert(1)>')).toEqual([
      { kind: 'text', text: '<img src=x onerror=alert(1)>' },
    ])
  })
})
