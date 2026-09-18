import { useEffect, useState } from 'react'

import { useTranslation } from '../lib/i18n'
import { Button, Dialog, Input } from '../ui/primitives'

export interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: { key: string; label: string; onSelect: () => void }[]
}

/** ⌘K 命令面板：一级切换（会话 / 任务板 / 技能目录 / 设置）。 */
export function CommandPalette({ open, onOpenChange, items }: CommandPaletteProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (open) setQuery('')
  }, [open])

  const matched = items.filter((item) => item.label.includes(query) || item.key.includes(query))

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('common.nav.commandPalette')}
      description={t('common.search')}
      footer={
        <Button onClick={() => onOpenChange(false)}>{t('common.close')}</Button>
      }
    >
      <Input
        autoFocus
        aria-label={t('common.search')}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <ul className="mt-3 flex flex-col gap-1">
        {matched.map((item) => (
          <li key={item.key}>
            <Button
              className="w-full justify-start"
              onClick={() => {
                item.onSelect()
                onOpenChange(false)
              }}
            >
              {item.label}
            </Button>
          </li>
        ))}
      </ul>
    </Dialog>
  )
}
