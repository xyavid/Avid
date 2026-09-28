// @vitest-environment jsdom
/**
 * A2：思维链在「思考中」卡片上的可见性。
 *
 * 产品规则：思维链是**过程**，不是回复。它只进这张卡片、不进时间线；卡片收起时只给
 * 一句「思考中」，展开后才是正文——否则几万字符的思考会把对话区顶下去。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ReactNode } from 'react'

import { LocaleProvider } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'
import { ProcessingCard } from '../components/ProcessingCard'

afterEach(cleanup)

function withLocale(node: ReactNode) {
  return render(<LocaleProvider>{node}</LocaleProvider>)
}

function card(reasoning: string) {
  return withLocale(
    <ProcessingCard
      phaseLabel="运行中"
      round={2}
      tokens={123}
      activeTool={null}
      reasoning={reasoning}
    />,
  )
}

describe('ProcessingCard 的思维链', () => {
  beforeEach(() => useUiStore.setState({ thinkingExpanded: false }))

  it('收起时不显示思考正文，只给一句「思考中」', () => {
    card('它在想一件很长的事')

    expect(screen.getByText('思考中')).toBeTruthy()
    expect(screen.queryByText('它在想一件很长的事')).toBeNull()
  })

  it('展开后显示思维链正文', () => {
    useUiStore.setState({ thinkingExpanded: true })
    card('它在想一件很长的事')

    expect(screen.getByText('它在想一件很长的事')).toBeTruthy()
  })

  it('没有思维链时一个字都不多说（旧行为不变）', () => {
    card('')

    expect(screen.queryByText('思考中')).toBeNull()
    expect(screen.getByText('运行中')).toBeTruthy()
  })
})