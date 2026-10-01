/**
 * 某个会话的分支列表。
 *
 * 为什么分支是**每次会话一份**的局部状态、而不放进应用级 store：分支只被
 * `ConversationHeader` 的选择器使用（`is_default` / `entry_count` / 分支级 usage），
 * 别的组件不读它。放进全局会让"换会话要清空分支"变成又一处必须记得的联动。
 *
 * 调用方用 `key={sessionId}` 挂这个 hook 的载体组件，于是换会话时状态自然重建——
 * 与 `useRunStream` 的整页重建是同一手法，见那里的注释。
 */

import { useCallback, useEffect, useState } from 'react'

import { fetchBranches } from '../api/sessions'
import type { Branch } from '../api/types'

export interface UseBranchesResult {
  branches: Branch[]
  /** 拉取失败时的说明；null = 没有错误（不代表"没有分支"）。 */
  error: string | null
  reload: () => Promise<void>
}

export function useBranches(sessionId: string | null): UseBranchesResult {
  const [branches, setBranches] = useState<Branch[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (): Promise<void> => {
    if (sessionId === null) {
      setBranches([])
      return
    }
    try {
      const list = await fetchBranches(sessionId)
      setBranches(list.branches)
      setError(null)
    } catch (caught) {
      /*
       * 分支列表拉失败**不该挡住主流程**：条目、输入区、事件流都与它无关，
       * 只是头部少一个选择器。所以这里只记错误，不抛。
       */
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [sessionId])

  useEffect(() => {
    void load()
  }, [load])

  return { branches, error, reload: load }
}
