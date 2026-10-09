/**
 * Filename → language label. The label is also an alias `highlight.ts` accepts, but the two
 * sets differ on purpose: this table knows more extensions, unknown ones are labeled by their
 * extension, and the highlighter falls back to plain when it does not recognize a label.
 */

/** Extension → label; an unseen extension is labeled as-is. */
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

/** Label for a path; null without an extension (CodeBlock then shows its text label). */
export function langOf(path: string): string | null {
  const name = path.split('/').pop() ?? path
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return null
  const ext = name.slice(dot + 1).toLowerCase()
  return LANGS[ext] ?? ext
}
