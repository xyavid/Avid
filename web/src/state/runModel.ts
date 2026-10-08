/**
 * 本次运行用哪个模型（阶段 54 追加）：**由用户在输入区自己选**，没有「跟随设置」那一档。
 *
 * 职责切分：设置负责**候选列表**（`设置 → 模型` 里配的 BYOK 提供商与模型），这里负责
 * 「这一轮用哪个」。选过就记住（localStorage）——它是用户自己的选择，不是隐式的默认值；
 * 设置里把那个模型删掉之后记忆随即失效（回落成「未选择」），别让运行落到一个不存在的 ref 上。
 *
 * 为什么不让「没选」落到服务端的 chat 绑定：那等于又给了一个看不见的默认模型。内核那边
 * 绑定仍然必须存在（命令行与无覆盖的运行要用它），但界面这条路永远显式带上模型。
 */

import { useCallback, useEffect, useState } from 'react'

import type { ModelCandidate } from '../api/types'

const STORAGE_KEY = 'avid.run.model'

function read(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw === null || raw === '' ? null : raw
  } catch {
    return null
  }
}

export function useRunModel(candidates: ModelCandidate[]): [string | null, (ref: string) => void] {
  const [chosen, setChosen] = useState<string | null>(read)

  // 候选还没到（meta 未落地）时不动记忆，否则一挂载就会把用户上一次的选择清掉
  useEffect(() => {
    if (candidates.length === 0) return
    setChosen((current) =>
      current !== null && !candidates.some((candidate) => candidate.ref === current) ? null : current,
    )
  }, [candidates])

  const choose = useCallback((ref: string) => {
    setChosen(ref)
    try {
      localStorage.setItem(STORAGE_KEY, ref)
    } catch {
      // 存不进去（隐私模式）只影响"记住"，不影响这一轮怎么跑
    }
  }, [])

  return [chosen, choose]
}
