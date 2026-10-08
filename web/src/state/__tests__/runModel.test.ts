// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ModelCandidate } from '../../api/types'
import { useRunModel } from '../runModel'

const KEY = 'avid.run.model'
const candidates: ModelCandidate[] = [
  { ref: 'stub/a', label: 'stub · a' },
  { ref: 'stub/b', label: 'stub · b' },
]

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe('useRunModel（本次运行用哪个模型）', () => {
  it('没选过就是"未选择"——没有跟随设置这一档', () => {
    const { result } = renderHook(() => useRunModel(candidates))

    expect(result.current[0]).toBeNull()
  })

  it('选过就记住：下一轮打开还是它（用户自己的选择，不是隐式默认）', () => {
    const first = renderHook(() => useRunModel(candidates))
    act(() => first.result.current[1]('stub/b'))
    expect(first.result.current[0]).toBe('stub/b')

    const second = renderHook(() => useRunModel(candidates))
    expect(second.result.current[0]).toBe('stub/b')
  })

  it('设置里把那个模型删了：记忆失效，回落未选择，让用户重选', () => {
    localStorage.setItem(KEY, 'stub/gone')
    const { result } = renderHook(() => useRunModel(candidates))

    expect(result.current[0]).toBeNull()
  })

  it('候选还没到时不清记忆（meta 是异步来的）', () => {
    localStorage.setItem(KEY, 'stub/a')
    const { result, rerender } = renderHook(({ list }) => useRunModel(list), {
      initialProps: { list: [] as ModelCandidate[] },
    })
    expect(result.current[0]).toBe('stub/a')

    rerender({ list: candidates })
    expect(result.current[0]).toBe('stub/a')
  })
})
