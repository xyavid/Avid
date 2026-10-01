/**
 * 组件墙（阶段 2 的验收对照物，常驻风格速查板）：`?gallery=1` 打开，
 * `&only=button` 只渲染一节——供逐组件截图。分组与文案对照 Hana 组件墙参考；
 * 本页随产物分发但没有任何界面入口指向它。
 * 纪律与本站一致：一切颜色/尺寸引用 token；组件内边距等离网值在组件文件里注明墙出处。
 */

import type { ReactNode } from 'react'

import { AssistantMessage, Composer, ToolCard, UserBubble } from '../../components/chat'
import { ContextRail } from '../../components/rail'
import { ProjectCard, SessionItem, SessionNav } from '../../components/session'
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
    context: { tokens: 15_200, window: 200_000, utilization: 0.076, parts: null },
    cache: { read_tokens: 9_800, write_tokens: 1_200, hit_ratio: 0.808 },
    compaction: { count: 0, last_compaction_tokens: null, last_step: null },
  }

  return (
    <div className="min-h-dvh bg-paper px-[44px] py-[34px] font-ui text-ink">
      {only === null && (
        <>
          <h1 className="font-serif text-[26px] font-medium">Avid 组件墙</h1>
          <p className="mt-a4 font-ui text-caption text-ink-muted">
            标识 · 焰 ｜ 基础 · 按钮 输入 标签 徽章 图标 ｜ 组合 · 消息 会话 卡片 标签页 工具卡 会话列表 项目卡 右栏 输入区 ｜ 实际页面看主页（不带 ?gallery=1）
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
        <Section id="mark" title="标识 · 焰（48 视框实心单色，与线性图标集是两类）">
          {/* 两套主题并排：给子树挂 data-theme 即可让里面的 token 全部换掉——
              换肤机制（只覆盖变量）在这里顺带当了演示，不需要任何裸色值。 */}
          <div className="flex flex-wrap gap-a24">
            <div className="rounded-md border-hairline border-hair bg-paper px-a16 py-a12">
              <p className="mb-a8 font-ui text-hint text-ink-muted">暖纸 · 墨（text）</p>
              <div className="flex items-end gap-a12 text-ink">
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
              <p className="mb-a8 font-ui text-hint text-ink-muted">青夜 · 雪（text）</p>
              <div className="flex items-end gap-a12 text-ink">
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
                圆内落位 · 小尺寸取 accent（与流式光标同色），大尺寸取墨
              </p>
              <div className="flex items-end gap-a16">
                <span className="flex h-6 w-6 items-center justify-center rounded-full border-hairline border-hair bg-card text-accent">
                  <AvidMark size={13} />
                </span>
                <span className="flex h-[100px] w-[100px] items-center justify-center rounded-full border-hairline border-hair bg-card text-ink">
                  <AvidMark size={64} />
                </span>
              </div>
            </div>
            <div>
              <p className="mb-a8 font-ui text-hint text-ink-muted">
                与字标的锁定组合（EB Garamond 500，标记 20px）
              </p>
              <span className="flex items-center gap-a8 text-ink">
                <AvidMark size={20} />
                <span className="font-serif text-title tracking-[0.01em]">Avid</span>
              </span>
            </div>
          </div>
        </Section>
      )}

      {show('bubble') && (
        <Section id="bubble" title="消息 · 用户气泡与助手回复">
          <div className="max-w-chat">
            <UserBubble>帮我把这套界面整理成一份可以交给别的 agent 的参考。</UserBubble>
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
        <Section id="session" title="会话列表项">
          <div className="w-sidebar">
            <SessionItem title="常态会话标题" meta="09:44 · 昨天" />
            {/* hover 摆拍：用任意变体选择器把「隐藏直到需要」的按钮直接点亮 */}
            <SessionItem
              title="hover 态 · 操作按钮淡入"
              meta="11:20"
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
        <Section id="toolcard" title="工具卡（折叠行 ↔ 展开卡；连续调用聚组）">
          <div className="flex flex-col gap-a8">
            <ToolCard icon="terminal" title="bash" preview="uv run pytest -q" status="ok">
              uv run pytest -q
            </ToolCard>
            <ToolCard icon="file-frame" title="读取 styles.css" badge="9 档间距" defaultExpanded>
              --space-2 … --space-40
              <br />
              --duration-instant / fast / slow
            </ToolCard>
          </div>
        </Section>
      )}

      {show('nav') && (
        <Section id="nav" title="会话列表（侧栏 240px 实宽）">
          <div className="w-sidebar rounded-md border-hairline border-hair bg-sidebar p-a12">
            <SessionNav sessions={demoSessions} selectedId="d1" onSelect={() => {}} />
          </div>
        </Section>
      )}

      {show('projects') && (
        <Section id="projects" title="项目卡（侧栏 · 可收回，一个项目 = 一个选定的工作区）">
          <div className="w-sidebar rounded-md border-hairline border-hair bg-sidebar p-a12">
            <ProjectCard
              workspaces={demoSessions
                .filter((s) => s.workspace)
                .map((s, i) => ({
                  id: `pw${i}`,
                  root: `/home/fishy/${s.name}`,
                  name: s.name,
                  created_at: 0,
                  last_used_at: 0,
                  default_permission: 'manual' as const,
                  is_default: false,
                }))}
              activeWorkspaceId="pw0"
              sessionWorkspaceId="pw0"
              pickerAvailable
              onSelectWorkspace={() => {}}
            />
          </div>
        </Section>
      )}

      {show('rail') && (
        <Section id="rail" title="右栏 · 上下文卡（280px 实宽）">
          <div className="w-rail">
            <ContextRail usage={demoUsage} />
          </div>
        </Section>
      )}

      {show('composer') && (
        <Section id="composer" title="输入区（16px 圆角壳；权限胶囊可交互，发送阶段 5 接线）">
          <div className="max-w-chat-input border-hairline border-hair bg-sidebar p-a12">
            <Composer
              permission="manual"
              onChangePermission={() => {}}
              onSend={() => {}}
              onStop={() => {}}
            />
          </div>
        </Section>
      )}
    </div>
  )
}
