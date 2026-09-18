import { useEffect } from 'react'

import { useUiStore } from './uiStore'

/**
 * 全局文本缩放：`html { font-size: calc(1rem * var(--avid-text-scale)) }`。
 * 缩放时硬阴影与形状偏移跟着一起缩（token 里用 `--avid-scale` 乘过），
 * 否则放大字号时层级感会变弱。
 */
export function useApplyTextScale(): void {
  const scale = useUiStore((state) => state.textScale)

  useEffect(() => {
    document.documentElement.style.setProperty('--avid-text-scale', String(scale / 100))
  }, [scale])
}
