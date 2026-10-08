// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ModelCandidate } from '../../api/types'
import { useRunChoice } from '../runModel'

const KEY = 'avid.run.model'
const EFFORT_KEY = 'avid.run.effort'
const candidates: ModelCandidate[] = [
  { ref: 'stub/a', label: 'stub · a', reasoning_efforts: ['low', 'high', 'max'] },
  { ref: 'stub/b', label: 'stub · b' },
]

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe('useRunChoice（本次运行用哪个模型、哪档强度）', () => {
  it('没选过就是"未选择"——没有跟随设置这一档', () => {
    const { result } = renderHook(() => useRunChoice(candidates))

    expect(result.current.model).toBeNull()
    expect(result.current.effort).toBeNull()
  })

  it('选过就记住：下一轮打开还是它（用户自己的选择，不是隐式默认）', () => {
    const first = renderHook(() => useRunChoice(candidates))
    act(() => {
      first.result.current.chooseModel('stub/a')
      first.result.current.chooseEffort('max')
    })
    expect([first.result.current.model, first.result.current.effort]).toEqual(['stub/a', 'max'])

    const second = renderHook(() => useRunChoice(candidates))
    expect([second.result.current.model, second.result.current.effort]).toEqual(['stub/a', 'max'])
  })

  it('档位跟着模型走：换到没声明档位的模型上，记忆失效', () => {
    localStorage.setItem(KEY, 'stub/a')
    localStorage.setItem(EFFORT_KEY, 'high')
    const { result, rerender } = renderHook(({ list }) => useRunChoice(list), {
      initialProps: { list: candidates },
    })
    expect(result.current.effort).toBe('high')

    act(() => result.current.chooseModel('stub/b'))
    rerender({ list: candidates })

    expect(result.current.efforts).toEqual([])
    expect(result.current.effort).toBeNull()
    expect(localStorage.getItem(EFFORT_KEY)).toBeNull()
  })

  it('设置里把那一档去掉了：记忆失效（内核那边也会按列表拦）', () => {
    localStorage.setItem(KEY, 'stub/a')
    localStorage.setItem(EFFORT_KEY, 'max')
    const narrowed: ModelCandidate[] = [
      { ref: 'stub/a', label: 'stub · a', reasoning_efforts: ['low'] },
    ]
    const { result } = renderHook(() => useRunChoice(narrowed))

    expect(result.current.effort).toBeNull()
  })

  it('当前模型声明的档位原样给出来（界面按它列选项）', () => {
    const { result } = renderHook(() => useRunChoice(candidates))
    act(() => result.current.chooseModel('stub/a'))

    expect(result.current.efforts).toEqual(['low', 'high', 'max'])
  })

  it('设置里把那个模型删了：记忆失效，回落未选择，让用户重选', () => {
    localStorage.setItem(KEY, 'stub/gone')
    const { result } = renderHook(() => useRunChoice(candidates))

    expect(result.current.model).toBeNull()
  })

  it('候选还没到时不清记忆（meta 是异步来的）', () => {
    localStorage.setItem(KEY, 'stub/a')
    const { result, rerender } = renderHook(({ list }) => useRunChoice(list), {
      initialProps: { list: [] as ModelCandidate[] },
    })
    expect(result.current.model).toBe('stub/a')

    rerender({ list: candidates })
    expect(result.current.model).toBe('stub/a')
  })
})
