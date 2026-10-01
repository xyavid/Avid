/**
 * 组件墙（阶段 2 的验收对照物，常驻风格速查板）：`?gallery=1` 打开，
 * `&only=button` 只渲染一节——供逐组件截图。分组与文案对照 Hana 组件墙参考；
 * 本页随产物分发但没有任何界面入口指向它。
 * 纪律与本站一致：一切颜色/尺寸引用 token；组件内边距等离网值在组件文件里注明墙出处。
 */

import type { ReactNode } from 'react'

import { Badge } from '../Badge'
import { Button } from '../Button'
import { Input } from '../Input'
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

  return (
    <div className="min-h-dvh bg-paper px-[44px] py-[34px] font-ui text-ink">
      {only === null && (
        <>
          <h1 className="font-serif text-[26px] font-medium">Avid 组件墙</h1>
          <p className="mt-a4 font-ui text-caption text-ink-muted">按钮 · 输入 · 标签 · 徽章 —— 阶段 2（逐项对照 Hana 组件墙）</p>
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
    </div>
  )
}
