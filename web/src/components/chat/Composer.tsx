/**
 * 输入区（参考图主界面）：16px 圆角抬升面壳（--radius-chat-surface）+
 * 裸输入框（bare Input）+ 左侧 [权限按钮·附加·附件] + 右侧 accent 发送钮。
 * 输入列取 --chat-input-column-width（比对话列略宽，报告 §6）。
 * 权限按钮反映实际三态（手动/自动/完全），点卡片可改——它不改后端，
 * 改的是「下次发送用什么模式」，发送时随 StartRunInput 提交（阶段 5）。
 * 发送与流式在阶段 5 接线——当前整体禁用并在 title 注明，不装可用。
 */

import type { PermissionMode } from '../../api/types'
import { IconButton } from '../../ui/IconButton'
import { Input } from '../../ui/Input'
import { PermissionButton } from './PermissionButton'

export type ComposerProps = {
  permission: PermissionMode
  onChangePermission: (mode: PermissionMode) => void
}

export function Composer({ permission, onChangePermission }: ComposerProps) {
  return (
    <div className="px-a16 pb-a16">
      <div className="relative mx-auto max-w-chat-input rounded-chat-surface border-hairline border-hair bg-card px-a16 py-a12 shadow-soft">
        <Input bare placeholder="给 Avid 发消息…" aria-label="消息输入" disabled />
        <div className="mt-a8 flex items-center justify-between">
          <div className="flex items-center gap-a8">
            <PermissionButton mode={permission} onChange={onChangePermission} />
            <IconButton icon="plus" label="附加" disabled />
            <IconButton icon="paperclip" label="附件" disabled />
          </div>
          <IconButton icon="send" label="发送" variant="primary" disabled title="阶段 5 接线：发送与流式" />
        </div>
      </div>
    </div>
  )
}
