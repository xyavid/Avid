/**
 * 本次运行用哪个模型、哪个推理强度（阶段 54 起；强度是阶段 55 追加）：**都由用户在输入区自己选**，
 * 没有「跟随设置」那一档。
 *
 * 职责切分：设置负责**候选与档位列表**（`设置 → 模型` 里配的提供商、模型，以及每个模型认哪些档位），
 * 这里负责「这一轮用哪个、哪一档」。选过就记住（localStorage）——它是用户自己的选择，不是隐式的
 * 默认值；设置里把模型删掉、把档位从列表里去掉之后，记忆随即失效（回落成未选择），
 * 别让运行落到一个不存在的 ref 或没声明的档位上（内核那边也会按列表再校验一次）。
 *
 * 为什么不让「没选」落到服务端的 chat 绑定：那等于又给了一个看不见的默认模型。内核那边
 * 绑定仍然必须存在（命令行与无覆盖的运行要用它），但界面这条路永远显式带上模型；
 * 强度则是「不选就不带这个参数」（没有隐式默认档位）。
 */

import { useCallback, useEffect, useState } from 'react'

import type { ModelCandidate } from '../api/types'

const MODEL_KEY = 'avid.run.model'
const EFFORT_KEY = 'avid.run.effort'

function read(key: string): string | null {
  try {
    const raw = localStorage.getItem(key)
    return raw === null || raw === '' ? null : raw
  } catch {
    return null
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // 存不进去（隐私模式）只影响"记住"，不影响这一轮怎么跑
  }
}

/** 输入区那一对选择：模型 + 这一次的推理强度（强度可以为"不设"）。 */
export type RunChoice = {
  model: string | null
  effort: string | null
  chooseModel: (ref: string) => void
  chooseEffort: (level: string | null) => void
  /** 当前模型声明的档位（空 = 这个模型不提强度，界面就别显示选择器）。 */
  efforts: string[]
}

export function useRunChoice(candidates: ModelCandidate[]): RunChoice {
  const [model, setModel] = useState<string | null>(() => read(MODEL_KEY))
  const [effort, setEffort] = useState<string | null>(() => read(EFFORT_KEY))

  const current = candidates.find((candidate) => candidate.ref === model)
  const efforts = current?.reasoning_efforts ?? []

  // 候选还没到（meta 未落地）时不动记忆，否则一挂载就会把用户上一次的选择清掉
  useEffect(() => {
    if (candidates.length === 0) return
    setModel((now) => {
      if (now !== null && !candidates.some((candidate) => candidate.ref === now)) {
        write(MODEL_KEY, null)
        return null
      }
      return now
    })
  }, [candidates])

  // 档位跟着模型走：换了模型、或设置里把这一档去掉了，记忆就失效（内核那边也会按列表拦）
  useEffect(() => {
    if (effort === null || candidates.length === 0) return
    if (!efforts.includes(effort)) {
      write(EFFORT_KEY, null)
      setEffort(null)
    }
  }, [candidates.length, effort, efforts])

  const chooseModel = useCallback((ref: string) => {
    setModel(ref)
    write(MODEL_KEY, ref)
  }, [])

  const chooseEffort = useCallback((level: string | null) => {
    setEffort(level)
    write(EFFORT_KEY, level)
  }, [])

  return { model, effort, chooseModel, chooseEffort, efforts }
}
