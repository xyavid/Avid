/**
 * 富文本正文渲染：把模型输出的 markdown 变成**结构元素**。
 *
 * 为什么加这一层（参考截图里正文有标题 / 列表 / 行内 code 底色）：
 * 助手回答天然是 markdown，按纯文本渲染时 `##`、`-`、`**` 会原样留在正文里，
 * 读者得自己在脑子里解析一遍。渲染成元素之后，层级靠字号 + 明度 + 发丝线表达，
 * 与纸本视觉一致。
 *
 * ## 安全边界（本文件最重要的一条，别改）
 * **`rehypePlugins` 必须保持为空**：不引 `rehype-raw`、不把原始 HTML 解析成元素。
 * 正文来自模型，而模型的输出里可能带用户粘贴的内容或工具回显；一旦允许原始 HTML，
 * `onerror` / `javascript:` 这类面就重新打开了——react-markdown 默认把 html 节点
 * 转义成字面量文本，这是现成的、也是最便宜的那道防线。单测里有一条专门钉它。
 * （链接仍要 `target="_blank" rel="noreferrer noopener"`：本地应用里点外链不该把
 * 当前页导航走，`noreferrer` 顺带不给对端 referrer。）
 *
 * ## 样式
 * 不用 `@tailwindcss/typography`：它会带进一套与 token 无关的字号与颜色，
 * 于是"同一份视觉"有了第二个来源，换肤必漏。这里逐元素指定 token 类名。
 *
 * ## 三条本仓样式陷阱的落法
 *   · 单边发丝线用**方向变体单独写**（`border-l-hair` / `border-t-hair` 自带线色），
 *     绝不叠 `border-hair`——那会把四边都设成 0.5px、画成一个整框；
 *   · `border-hair` 一条就同时给了 0.5px 线宽与线色（`borderWidth.hair` 与
 *     `colors.hair` 同名，两条规则各管一个属性），所以不写两遍；
 *   · 圆角只用 `xs|sm`（≤3px）、字重只用 `font-medium`（500），无 emoji、无闪烁光标。
 */

import type { ReactElement } from 'react'
// 包根只把 `Markdown` 作为 **default** 导出（`index.js` 里 `Markdown as default`），
// 没有具名导出；写成 `{ Markdown }` 会在运行期拿到 undefined，报"Element type is invalid"。
import MarkdownRenderer from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

export interface MarkdownProps {
  /** 原始 markdown 文本。 */
  source: string
  /** 流式中（末尾可能截断在半条语法里）：只影响根节点标记，不做语法修补。 */
  streaming?: boolean
  className?: string
}

/**
 * 元素 → token 类名的映射表。
 *
 * 每个组件都把 react-markdown 附加的 `node` 从 props 里摘掉再展开其余属性：
 * `node` 是 hast 节点，直接摊到 DOM 上会变成一个非法属性并进 React 的告警。
 *
 * 块级元素的纵向间距写在这里而不是外层 `gap`：`avid-prose` 只管行高与断词，
 * 若正文块之间没有自己的 margin，Tailwind 预检清零后的标题会直接贴在上一段上。
 * `first:mt-0 last:mb-0` 把首尾多余的空隙去掉，避免气泡上下边距翻倍。
 */
const components: Components = {
  h1: ({ node: _node, ...rest }) => (
    <h1 {...rest} className="mb-a8 mt-a16 text-title font-medium text-ink first:mt-0" />
  ),
  h2: ({ node: _node, ...rest }) => (
    <h2 {...rest} className="mb-a8 mt-a16 text-title text-ink first:mt-0" />
  ),
  h3: ({ node: _node, ...rest }) => (
    <h3 {...rest} className="mb-a6 mt-a12 text-body font-medium text-ink first:mt-0" />
  ),
  h4: ({ node: _node, ...rest }) => (
    <h4 {...rest} className="mb-a6 mt-a12 text-ui font-medium text-ink first:mt-0" />
  ),
  p: ({ node: _node, ...rest }) => (
    <p {...rest} className="my-a8 text-body text-ink first:mt-0 last:mb-0" />
  ),
  /*
   * 嵌套列表用 circle 与外层 disc 区分：中文正文里项目符号的层级靠形状最省事。
   * 注意这里用任意值 `list-[circle]`——Tailwind 默认主题的 `listStyleType` 只有
   * none / disc / decimal 三档，写 `list-circle` 会**静默生成不出任何 CSS**
   * （类名挂在 DOM 上、构建不报错、嵌套列表看起来还是 disc）。实测：
   * `grep list-circle dist/assets/*.css` 命中 0。
   * 这是枚举值不是色值 / 圆角 / 阴影，加进 tailwind.config.js 会动到共享配置
   * （别人的文件），所以就近用任意值并在此注明。
   */
  ul: ({ node: _node, ...rest }) => (
    <ul {...rest} className="my-a6 list-disc pl-a16 text-body text-ink [&_ul]:list-[circle]" />
  ),
  ol: ({ node: _node, ...rest }) => (
    <ol {...rest} className="my-a6 list-decimal pl-a16 text-body text-ink" />
  ),
  li: ({ node: _node, ...rest }) => <li {...rest} className="my-a2" />,
  /*
   * 行内 code 与代码块共用一个 `code` 渲染器：只能靠 `language-*` 类名区分。
   * 没有语言标注的围栏块因此会先被当成行内样式，再由 `pre` 上的 `[&>code]` 复位——
   * 这是本仓认可的做法（react-markdown 官方示例同样只能这样判），比在组件里
   * 猜 `node.position` 稳。
   */
  code: ({ node: _node, className, children, ...rest }) => {
    const fenced = typeof className === 'string' && className.includes('language-')
    if (fenced) {
      return (
        <code {...rest} className={className}>
          {children}
        </code>
      )
    }
    return (
      <code
        {...rest}
        className="rounded-xs bg-inset px-a4 py-[1px] font-mono text-hint text-ink"
      >
        {children}
      </code>
    )
  },
  pre: ({ node: _node, ...rest }) => (
    <pre
      {...rest}
      className="my-a8 overflow-x-auto rounded-sm bg-code-bg p-a10 text-caption [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-caption [&>code]:text-ink"
    />
  ),
  blockquote: ({ node: _node, ...rest }) => (
    <blockquote {...rest} className="my-a8 border-l-hair pl-a10 text-ink-muted italic" />
  ),
  a: ({ node: _node, ...rest }) => (
    // 外链一律新窗口 + noopener：本地应用里换走当前页会让用户丢掉正在看的会话。
    <a
      {...rest}
      target="_blank"
      rel="noreferrer noopener"
      className="text-accent-ink underline underline-offset-2"
    />
  ),
  table: ({ node: _node, ...rest }) => (
    <table {...rest} className="my-a8 w-full border-collapse text-ui" />
  ),
  th: ({ node: _node, ...rest }) => (
    <th {...rest} className="border-hair bg-inset px-a8 py-a4 text-left font-medium text-ink" />
  ),
  td: ({ node: _node, ...rest }) => <td {...rest} className="border-hair px-a8 py-a4" />,
  hr: ({ node: _node, ...rest }) => <hr {...rest} className="my-a16 border-t-hair" />,
  /* 字重上限 500：`font-bold`(700) 在本视觉里是黑名单，强调靠字重 + 墨色。 */
  strong: ({ node: _node, ...rest }) => (
    <strong {...rest} className="font-medium text-ink" />
  ),
  del: ({ node: _node, ...rest }) => (
    <del {...rest} className="text-ink-muted line-through" />
  ),
  /* 图片只约束宽度：不设 max-width 时一张大图会把正文列撑破。 */
  img: ({ node: _node, ...rest }) => (
    <img {...rest} className="my-a6 max-w-full rounded-sm" />
  ),
}

export function Markdown({ source, streaming = false, className }: MarkdownProps): ReactElement | null {
  // 空内容不渲染空 `<p>`：它会在时间线上留下一段没有内容的空隙。
  if (source.trim() === '') return null

  return (
    <div className={className} data-streaming={streaming ? 'true' : undefined}>
      {/*
        remarkPlugins 只给 gfm（表格 / 删除线 / 任务列表）；rehypePlugins **保持为空**，
        原因见文件头"安全边界"。这条不写插件的空是刻意的，不是漏了。
      */}
      <MarkdownRenderer remarkPlugins={[remarkGfm]} components={components}>
        {source}
      </MarkdownRenderer>
    </div>
  )
}
