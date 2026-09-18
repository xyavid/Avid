/**
 * 上下文压缩提示：压缩后原文不可回看，所以只留事实——哪一步、砍了什么、字符数怎么变。
 *
 * 它同时出现在时间线（作为 notice chip）和检查器里，因此不接受任何布局参数：放在哪里
 * 由调用方的容器决定。
 */
import { useTranslation } from '../../lib/i18n'
import type { CompactionNote } from '../../lib/timeline'

export function CompactionNotice({ note }: { note: CompactionNote }) {
  const { t } = useTranslation()
  return (
    <div className="sketch-chip flex flex-wrap items-center gap-2 px-3 py-1 text-xs">
      <span className="font-sketch">{t('chat.notice.compaction')}</span>
      <span className="font-mono">{`${note.step} — ${note.detail}`}</span>
      <span className="font-mono text-ink/70">
        {t('tools.chars', { chars: note.before })} → {t('tools.chars', { chars: note.after })}
      </span>
    </div>
  )
}
