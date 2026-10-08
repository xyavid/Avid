/**
 * 组件墙（阶段 2 的验收对照物，常驻风格速查板）：`?gallery=1` 打开，
 * `&only=button` 只渲染一节——供逐组件截图。分组与文案对照 Hana 组件墙参考；
 * 本页随产物分发但没有任何界面入口指向它。
 * 纪律与本站一致：一切颜色/尺寸引用 token；组件内边距等离网值在组件文件里注明墙出处。
 */

import type { ReactNode } from 'react'

import { AssistantMessage, Composer, ContextRing, ToolCard, UserBubble } from '../../components/chat'
import { ProjectCard, SessionItem, SessionNav, SidebarFooter } from '../../components/session'
import type { SessionSummary, UsageReport } from '../../api/types'
import { Badge } from '../Badge'
import { Button } from '../Button'
import { Card } from '../Card'
import { ICON_NAMES, Icon } from '../Icon'
import { Input } from '../Input'
import { AvidMark } from '../Mark'
import { Tabs } from '../Tabs'
import { Tag } from '../Tag'

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section data-gallery={id} className="mb-a24">
      <h2 className="mb-a12 border-l-2 border-accent pl-[9px] font-serif text-title font-medium">
        {title}
      </h2>
      {children}
    </section>
  )
}

export function GalleryPage() {
  const only = new URLSearchParams(window.location.search).get('only')
  const show = (id: string) => only === null || only === id

  /* 演示数据：组合组件节的展示样本（不是真连接）——真实数据看主页。 */
  const demoSessions: SessionSummary[] = [
    { id: 'd1', name: '整理会议纪要', created_at: Date.now() - 2 * 60_000, storage_version: 1, parent_session_id: null, workspace: null, message_count: 4, active_run_id: 'r1', truncated_tail: false },
    { id: 'd2', name: '重构 archive 模块', created_at: Date.now() - 3_600_000, storage_version: 1, parent_session_id: null, workspace: null, message_count: 12, active_run_id: null, truncated_tail: false },
    { id: 'd3', name: '周报草稿', created_at: Date.now() - 86_400_000, storage_version: 1, parent_session_id: null, workspace: null, message_count: 6, active_run_id: null, truncated_tail: false },
  ]
  const demoUsage: UsageReport = {
    context: {
      tokens: 15_200,
      window: 200_000,
      utilization: 0.076,
      parts: { system: 900, tools: 1_100, messages: 13_200 },
    },
    cache: { read_tokens: 9_800, write_tokens: 1_200, hit_ratio: 0.808 },
    compaction: { count: 0, last_compaction_tokens: null, last_step: null },
  }

  return (
    <div className="min-h-dvh bg-paper px-[44px] py-[34px] font-ui text-ink">
      {only === null && (
        <>
          <h1 className="font-serif text-[26px] font-medium">Avid 组件墙</h1>
          <p className="mt-a4 font-ui text-caption text-ink-muted">
            标识 · 荷花 ｜ markdown ｜ 基础 · 按钮 输入 标签 徽章 图标 ｜ 组合 · 消息 会话 卡片 标签页 工具卡 会话列表 项目卡 上下文环 输入区 ｜ 实际页面看主页（不带 ?gallery=1）
          </p>
          <div className="mb-a16 mt-a16 border-t border-hair" />
        </>
      )}

      {show('button') && (
        <Section id="button" title="按钮">
          <div className="flex flex-wrap items-center gap-a12">
            <Button>描边按钮</Button>
            {/* hover 态摆拍：直接给 hover 的落点类，等价于 :hover 的稳定呈现 */}
            <Button className="bg-accent-light">hover 态</Button>
            <Button variant="primary">主行动（每屏 ≤1）</Button>
            <Button disabled>禁用</Button>
          </div>
        </Section>
      )}

      {show('input') && (
        <Section id="input" title="输入框">
          <div className="max-w-[640px]">
            <div className="mb-a8">
              <Input placeholder="常态 · 高度 34px" />
            </div>
            {/* focus 态摆拍用 autoFocus 走真实 :focus 路径：类名覆盖会被 tailwind
                的字母序输出静默吞掉（.border-accent 排在 .border-hair 之前），
                而 :focus 伪类带特异性加成，永远赢。 */}
            <Input autoFocus placeholder="focus · 边框转 accent + 2px 光晕" />
          </div>
        </Section>
      )}

      {show('tag') && (
        <Section id="tag" title="标签">
          <div className="flex flex-wrap items-center gap-a8">
            <Tag>accent 标签</Tag>
            <Tag variant="danger">danger</Tag>
            <Tag variant="neutral">中性</Tag>
          </div>
        </Section>
      )}

      {show('badge') && (
        <Section id="badge" title="徽章">
          <div className="flex flex-wrap items-center gap-a8">
            <Badge icon="check">已编译</Badge>
            <Badge variant="danger">danger</Badge>
          </div>
        </Section>
      )}

      {show('icons') && (
        <Section id="icons" title="图标 · 线性 SVG（stroke 1.5 / currentColor / 无实心）">
          <div className="flex flex-wrap gap-a12">
            {ICON_NAMES.map((name) => (
              <span
                key={name}
                className="flex w-[76px] flex-col items-center gap-a4 rounded-sm border-hairline border-hair bg-card px-a8 py-a8 text-ink-light"
              >
                <Icon name={name} size={16} />
                <span className="font-mono text-micro text-ink-muted">{name}</span>
              </span>
            ))}
          </div>
        </Section>
      )}

      {show('mark') && (
        <Section id="mark" title="标识 · 荷花（自带配色的插画，与线性图标集是两类）">
          {/* 标识不做圆托、不垫色板：直接贴在页面上、背景透明（使用者的要求）。
              两套主题并排——给子树挂 data-theme 即可换掉里面所有 token，
              标识自己不变色（它有自己的配色），只是周边底色跟着主题走。 */}
          <div className="flex flex-wrap gap-a24">
            <div className="rounded-md border-hairline border-hair bg-paper px-a16 py-a12">
              <p className="mb-a8 font-ui text-hint text-ink-muted">暖纸 · 纸面直放</p>
              <div className="flex items-end gap-a12">
                {[16, 20, 24, 32, 48, 64].map((s) => (
                  <span key={s} className="flex flex-col items-center gap-a4">
                    <AvidMark size={s} />
                    <span className="font-mono text-micro text-ink-muted">{s}</span>
                  </span>
                ))}
              </div>
            </div>
            <div
              data-theme="midnight"
              className="rounded-md border-hairline border-hair bg-paper px-a16 py-a12"
            >
              <p className="mb-a8 font-ui text-hint text-ink-muted">青夜 · 纸面直放</p>
              <div className="flex items-end gap-a12">
                {[16, 20, 24, 32, 48, 64].map((s) => (
                  <span key={s} className="flex flex-col items-center gap-a4">
                    <AvidMark size={s} />
                    <span className="font-mono text-micro text-ink-muted">{s}</span>
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="mt-a16 flex flex-wrap items-end gap-a24">
            <div>
              <p className="mb-a8 font-ui text-hint text-ink-muted">
                欢迎态尺寸（96px，直放不托底）
              </p>
              <AvidMark size={96} />
            </div>
            <div>
              <p className="mb-a8 font-ui text-hint text-ink-muted">
                与字标的锁定组合（EB Garamond 500，标记 22px）
              </p>
              <span className="flex items-center gap-a8 text-ink">
                <AvidMark size={22} />
                <span className="font-serif text-title tracking-[0.01em]">Avid</span>
              </span>
            </div>
          </div>
        </Section>
      )}

      {show('markdown') && (
        <Section id="markdown" title="markdown 渲染（助手正文：模型输出直接进 DOM，全程不注入 HTML）">
          <div className="max-w-chat rounded-md border-hairline border-hair bg-card px-a16 py-a12">
            {/* 样本覆盖：标题 / 段落（含行内码·粗体·链接）/ 有序与无序列表（含嵌套）/
                引用 / 表格 / 围栏代码 / 分隔线 / svg 围栏（渲染成图，不内联） */}
            <AssistantMessage>{`## 结论

读完了 \`pyproject.toml\`，项目名是 **avid**，要求 Python >= 3.12。参考 [标识一节](#markdown)。

---

### 要点

1. 依赖只有 \`httpx\`
2. 开发依赖用 \`uv\` 管理
   - \`uv sync\` 装内核依赖
   - \`uv sync --extra web\` 追加 Web 依赖


| 项 | 值 | 备注 |
| :--- | ---: | :--- |
| 包名 | avid | 与项目名一致 |
| 入口 | avid.cli | \`python -m avid\` 亦可 |

\`\`\`python
def add(a, b):
    """两数之和"""
    total = a + b  # 求和
    return total * 2
\`\`\`

---

\`\`\`svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40"><rect width="120" height="40" rx="6" fill="#537D96"/><text x="12" y="26" font-family="serif" font-size="16" fill="#F8F4ED">Avid</text></svg>
\`\`\``}</AssistantMessage>
          </div>
        </Section>
      )}

      {show('bubble') && (
        <Section id="bubble" title="消息 · 用户气泡与助手回复">
          <div className="max-w-chat">
            <UserBubble>帮我看下这段：

```py
print("hi")
```

顺便说下 `--extra web` 是干嘛的。</UserBubble>
            <div className="mt-a16">
              <AssistantMessage>好。我把它拆成三层：全局 token、主题调色板、组件规范。地基是那套 4px 网格和六档字号。</AssistantMessage>
            </div>
            <div className="mt-a16">
              <AssistantMessage streaming />
            </div>
          </div>
        </Section>
      )}

      {show('session') && (
        <Section id="session" title="会话列表项（行内动作：重命名 / 删除）">
          <div className="w-sidebar">
            <SessionItem title="常态会话标题" meta="09:44 · 昨天" />
            {/* hover 摆拍：用任意变体选择器把「隐藏直到需要」的按钮直接点亮；
                给了回调才有按钮——只读形态（上面的常态行）一个都不渲染 */}
            <SessionItem
              title="hover 态 · 操作按钮淡入"
              meta="11:20"
              onRename={() => {}}
              onDelete={() => {}}
              className="bg-accent-light [&_[data-testid=session-actions]]:opacity-100"
            />
            <SessionItem title="active 会话 · 标题转 accent" meta="进行中" active />
            <SessionItem title="流式生成中的会话" meta="流式生成中…" streaming />
          </div>
        </Section>
      )}

      {show('card') && (
        <Section id="card" title="卡片">
          <div className="max-w-[420px]">
            <Card title="纸本卡片">抬升面比主面更亮，用发丝线而非阴影分层。圆角 5px，内边距 13–15px。</Card>
          </div>
        </Section>
      )}

      {show('tabs') && (
        <Section id="tabs" title="标签页（sliding pill）">
          <Tabs items={['对话', '频道', '便笺', '文件']} value="对话" onChange={() => {}} />
        </Section>
      )}

      {show('toolcard') && (
        <Section id="toolcard" title="工具卡（折叠行 ↔ 展开卡；动作 + 目标 + 耗时）">
          <div className="flex flex-col gap-a8">
            <ToolCard icon="terminal" verb="执行" target="uv run pytest -q" status="ok" durationMs={8400}>
              uv run pytest -q
            </ToolCard>
            <ToolCard icon="file-frame" verb="读取" target="styles/tokens.css" status="ok" durationMs={12} defaultExpanded>
              --space-2 … --space-40
              <br />
              --duration-instant / fast / slow
            </ToolCard>
            <ToolCard
              icon="git-branch"
              verb="子智能体"
              target="2 个子任务"
              status="running"
              defaultExpanded
              steps={[
                { task: '前端时间线', callId: 's1', name: 'edit_file', args: '{"path":"web/src/components/chat/Timeline.tsx"}', status: 'ok' },
                { task: '前端时间线', callId: 's2', name: 'bash', args: '{"command":"pnpm -C web run verify"}', status: 'running' },
                { task: '内核核对', callId: 's3', name: 'read_file', args: '{"path":"avid/agent/events.py"}', status: 'ok' },
              ]}
            >
              2 个 subagent 已并行运行
            </ToolCard>
          </div>
        </Section>
      )}

      {show('nav') && (
        <Section id="nav" title="会话列表（侧栏 240px 实宽）+ 侧栏底栏">
          <div className="flex h-[420px] w-sidebar flex-col rounded-md border-hairline border-hair bg-sidebar p-a12">
            <SessionNav sessions={demoSessions} selectedId="d1" onSelect={() => {}} />
            {/* 底栏单开一栏：顶发丝线 + mt-auto 贴住栏底，设置入口在会话列表之后 */}
            <SidebarFooter onOpenSettings={() => {}} />
          </div>
        </Section>
      )}

      {show('projects') && (
        <Section id="projects" title="项目卡（侧栏 · 可收回；行尾「更多」悬停现形，按下出删除）">
          {/* 摆拍：项目行的「更多」平时是 opacity-0，这里把它强制点亮，速查板上才看得见 */}
          <div className="w-sidebar rounded-md border-hairline border-hair bg-sidebar p-a12 [&_[data-testid=project-more]]:opacity-100">
            <ProjectCard
              workspaces={demoSessions
                .filter((s) => s.workspace)
                .map((s, i) => ({
                  id: `pw${i}`,
                  root: `/home/fishy/${s.name}`,
                  name: s.name,
                  created_at: 0,
                  last_used_at: 0,
                  is_default: false,
                }))}
              activeWorkspaceId="pw0"
              sessionWorkspaceId="pw0"
              pickerAvailable
              onSelectWorkspace={() => {}}
              onDeleteWorkspace={() => {}}
            />
          </div>
        </Section>
      )}

      {show('context') && (
        <Section id="context" title="上下文容量环（输入区左簇；点开是明细）">
          <div className="pb-[280px]">
            <ContextRing usage={demoUsage} />
          </div>
        </Section>
      )}

      {show('composer') && (
        <Section id="composer" title="输入区（16px 圆角壳；权限胶囊可交互，发送阶段 5 接线）">
          <div className="max-w-chat-input border-hairline border-hair bg-sidebar p-a12">
            <Composer
              full={false}
              onToggleFull={() => {}}
              onSend={() => {}}
              onStop={() => {}}
            />
          </div>
        </Section>
      )}
    </div>
  )
}
