/**
 * TanStack Query 配置：**数据获取层只留一套**（LibreChat 与 LobeChat 的双轨并存
 * 是这条约束的由来）。staleTime 与 retry 都写在这里，组件不再各自决定。
 */

import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '../api/client'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 权威域是本地文件，不是网络服务：一次失败通常是内核没起，重试一次就够。
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            return false
          }
          return failureCount < 1
        },
        staleTime: 5_000,
        refetchOnWindowFocus: false,
        // 面板开关不该触发重新取数。
        refetchOnMount: false,
      },
      mutations: { retry: 0 },
    },
  })
}
