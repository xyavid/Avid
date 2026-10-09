/**
 * Code highlighting: source text in, token list out — a per-language regex table plus one
 * shared scanner, no third-party highlighter; colors are the `--syntax-*` tokens in tokens.css
 * (all ≥ 4.5 contrast on both themes). Hard invariant: concatenating token texts reproduces the
 * source verbatim — a code block is copied by the user, so never drop or add a character.
 */

export type TokenKind = 'plain' | 'comment' | 'keyword' | 'string' | 'number' | 'function'

export type Token = { kind: TokenKind; text: string }

/** One rule: a match consumes a span as `kind`; plain advances without coloring. */
type Rule = { re: RegExp; kind: TokenKind }

type LangSpec = { rules: Rule[] }

const PY_KEYWORDS =
  'def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|lambda|pass|break|continue|yield|global|nonlocal|assert|del|async|await|and|or|not|in|is|None|True|False|self'
const JS_KEYWORDS =
  'const|let|var|function|return|if|else|for|while|do|switch|case|default|break|continue|class|extends|new|typeof|instanceof|delete|in|of|import|export|from|as|async|await|try|catch|finally|throw|this|super|null|undefined|true|false|interface|type|enum|implements|public|private|protected|readonly|satisfies|void|never|any|string|number|boolean'
const BASH_KEYWORDS =
  'if|then|else|elif|fi|for|while|until|do|done|case|esac|function|return|export|local|source|alias|unset|set|trap|echo|cd|mkdir|rm|cp|mv|cat|grep|sed|awk|uv|npm|pnpm|node|python|git|ls'
const SQL_KEYWORDS =
  'select|from|where|group|by|order|having|limit|offset|insert|into|values|update|set|delete|join|left|right|inner|outer|on|as|and|or|not|null|is|in|like|between|distinct|count|sum|avg|min|max|create|table|index|drop|alter|primary|key|foreign|references|union|all'

/** Language alias → spec; an unknown language stays entirely plain (no guessing). */
const LANGS: Record<string, LangSpec> = {
  python: {
    rules: [
      { re: /#[^\n]*/, kind: 'comment' },
      { re: /(?:[rbfu]{0,2})?"""[\s\S]*?"""|(?:[rbfu]{0,2})?'''[\s\S]*?'''/, kind: 'string' },
      { re: /(?:[rbfu]{0,2})?"(?:\\[\s\S]|[^"\\\n])*"?|(?:[rbfu]{0,2})?'(?:\\[\s\S]|[^'\\\n])*'?/, kind: 'string' },
      { re: /@[A-Za-z_][\w.]*/, kind: 'function' },
      { re: new RegExp(`\\b(?:${PY_KEYWORDS})\\b`), kind: 'keyword' },
      { re: /\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, kind: 'number' },
      { re: /[A-Za-z_]\w*(?=\s*\()/, kind: 'function' },
    ],
  },
  javascript: {
    rules: [
      { re: /\/\/[^\n]*/, kind: 'comment' },
      { re: /\/\*[\s\S]*?\*\//, kind: 'comment' },
      { re: /`(?:\\[\s\S]|[^`\\])*`?/, kind: 'string' },
      { re: /"(?:\\[\s\S]|[^"\\\n])*"?|'(?:\\[\s\S]|[^'\\\n])*'?/, kind: 'string' },
      { re: new RegExp(`\\b(?:${JS_KEYWORDS})\\b`), kind: 'keyword' },
      { re: /\b(?:0[xXbBoO][\da-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b/, kind: 'number' },
      { re: /[A-Za-z_$][\w$]*(?=\s*\()/, kind: 'function' },
    ],
  },
  json: {
    rules: [
      { re: /"(?:\\[\s\S]|[^"\\])*"/, kind: 'string' },
      { re: /\b(?:true|false|null)\b/, kind: 'keyword' },
      { re: /-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, kind: 'number' },
    ],
  },
  bash: {
    rules: [
      { re: /(?<=^|\s)#[^\n]*/, kind: 'comment' },
      { re: /"(?:\\[\s\S]|[^"\\])*"?|'[^']*'?/, kind: 'string' },
      { re: new RegExp(`\\b(?:${BASH_KEYWORDS})\\b`), kind: 'keyword' },
      { re: /\$\{[^}]*\}|\$[A-Za-z_]\w*|\$\d/, kind: 'number' },
      { re: /\b\d+\b/, kind: 'number' },
    ],
  },
  css: {
    rules: [
      { re: /\/\*[\s\S]*?\*\//, kind: 'comment' },
      { re: /"(?:\\[\s\S]|[^"\\\n])*"?|'[^'\n]*'?/, kind: 'string' },
      { re: /@[\w-]+/, kind: 'keyword' },
      { re: /#[0-9a-fA-F]{3,8}\b/, kind: 'number' },
      { re: /-?\b\d+(?:\.\d+)?(?:px|rem|em|%|vh|vw|s|ms|deg|fr)?\b/, kind: 'number' },
    ],
  },
  html: {
    rules: [
      { re: /<!--[\s\S]*?-->/, kind: 'comment' },
      { re: /(?<=<\/?)[A-Za-z][\w:-]*/, kind: 'keyword' },
      { re: /"(?:[^"]*)"|'(?:[^']*)'/, kind: 'string' },
      { re: /&[a-zA-Z#\w]+;/, kind: 'number' },
    ],
  },
  yaml: {
    rules: [
      { re: /#[^\n]*/, kind: 'comment' },
      { re: /"[^"\n]*"?|'[^'\n]*'?/, kind: 'string' },
      { re: /^[ \t-]*[A-Za-z_][\w.-]*(?=\s*:)/, kind: 'keyword' },
      { re: /\b(?:true|false|null|yes|no)\b/, kind: 'keyword' },
      { re: /-?\b\d+(?:\.\d+)?\b/, kind: 'number' },
    ],
  },
  sql: {
    rules: [
      { re: /--[^\n]*|\/\*[\s\S]*?\*\//, kind: 'comment' },
      { re: /'(?:''|[^'])*'?/, kind: 'string' },
      { re: new RegExp(`\\b(?:${SQL_KEYWORDS})\\b`, 'i'), kind: 'keyword' },
      { re: /-?\b\d+(?:\.\d+)?\b/, kind: 'number' },
    ],
  },
}

/** Alias table: the spellings models actually write must resolve. */
const ALIAS: Record<string, keyof typeof LANGS> = {
  py: 'python',
  python: 'python',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'javascript',
  tsx: 'javascript',
  javascript: 'javascript',
  typescript: 'javascript',
  json: 'json',
  jsonc: 'json',
  bash: 'bash',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  console: 'bash',
  css: 'css',
  scss: 'css',
  html: 'html',
  xml: 'html',
  svg: 'html',
  yaml: 'yaml',
  yml: 'yaml',
  sql: 'sql',
}

/**
 * Scan left to right, first matching rule wins; rules are sticky (`y`, never `g`) so `lastIndex`
 * cannot leak between calls.
 */
export function highlight(code: string, lang: string | null): Token[] {
  if (code === '') return []
  const spec = lang ? LANGS[ALIAS[lang.trim().toLowerCase()] ?? ''] : undefined
  if (!spec) return [{ kind: 'plain', text: code }]

  const rules = spec.rules.map((r) => ({ kind: r.kind, re: new RegExp(r.re.source, r.re.flags.replace('g', '') + 'y') }))
  const tokens: Token[] = []
  let i = 0
  let plain = ''

  const flush = () => {
    if (plain !== '') {
      tokens.push({ kind: 'plain', text: plain })
      plain = ''
    }
  }

  while (i < code.length) {
    let matched = false
    for (const rule of rules) {
      rule.re.lastIndex = i
      const m = rule.re.exec(code)
      if (m && m[0] !== '') {
        flush()
        tokens.push({ kind: rule.kind, text: m[0] })
        i += m[0].length
        matched = true
        break
      }
    }
    if (!matched) {
      plain += code[i]
      i++
    }
  }
  flush()
  return tokens
}
