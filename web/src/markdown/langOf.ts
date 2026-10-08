/**
 * 文件名 → 语言标（阶段 52 追加：从右列文件面板搬来，工具卡详情也要它）。
 *
 * 「语言标」同时也是 `highlight.ts` 认的别名，但两者口径**故意不同**：这里认的
 * 扩展名比高亮器多（markdown / toml / rust…），认不出的按扩展名原样标，不猜 ——
 * 语言标是给人看的坐标，高亮器认不出就整段 plain，各自的表各自维护。
 */

/** 扩展名 → 语言标；没见过的按扩展名原样标。 */
const LANGS: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  mjs: 'javascript',
  cjs: 'javascript',
  json: 'json',
  md: 'markdown',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'toml',
  ini: 'ini',
  cfg: 'ini',
  css: 'css',
  scss: 'scss',
  html: 'html',
  xml: 'xml',
  svg: 'svg',
  sql: 'sql',
  rs: 'rust',
  go: 'go',
  java: 'java',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  rb: 'ruby',
  php: 'php',
  lock: '文本',
  txt: '文本',
}

/** 从文件名取语言标；没有扩展名给 null（CodeBlock 于是显示「文本」）。 */
export function langOf(path: string): string | null {
  const name = path.split('/').pop() ?? path
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return null
  const ext = name.slice(dot + 1).toLowerCase()
  return LANGS[ext] ?? ext
}
