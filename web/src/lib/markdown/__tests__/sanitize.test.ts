/**
 * URL 白名单的单测（S-8 / T-6：这条防线以前零覆盖，而它是 XSS 与出网信标的唯一看守）。
 *
 * payload 取自审查时实测过的一组绕过尝试：大小写伪协议、引用式链接、前后空白、
 * 协议相对地址。
 */

import { describe, expect, it } from 'vitest'

import { sanitizeUrl } from '../sanitize'

describe('sanitizeUrl：放行与拒绝', () => {
  it('放行 http(s) 与相对路径（含锚点、查询串）', () => {
    for (const url of [
      'https://example.com/a',
      'http://example.com',
      'docs/readme.md',
      '#anchor',
      '/settings',
      '?q=1',
    ]) {
      expect(sanitizeUrl(url), url).toBe(url.trim())
    }
  })

  it('拒绝伪协议（含大小写与空白变体）', () => {
    for (const url of [
      'javascript:alert(1)',
      'JaVaScRiPt:alert(1)',
      '  javascript:alert(1)',
      'data:text/html;base64,PHNjcmlwdD4=',
      'vbscript:msgbox(1)',
      'file:///etc/passwd',
      'mailto:x@y.z',
    ]) {
      expect(sanitizeUrl(url), url).toBeNull()
    }
  })

  it('拒绝协议相对地址：渲染即向外部主机发请求', () => {
    // `![x](//attacker/x)` 渲染时直接出网（IP/UA 外泄）；`\\host\x` 是浏览器会当
    // 斜杠处理的等价写法。
    for (const url of ['//attacker.example/x', '\\\\attacker.example\\x', '//']) {
      expect(sanitizeUrl(url), url).toBeNull()
    }
  })

  it('空串与纯空白拒绝', () => {
    expect(sanitizeUrl('')).toBeNull()
    expect(sanitizeUrl('   ')).toBeNull()
  })
})
