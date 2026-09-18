import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'
import { useState } from 'react'

import { LocaleProvider } from './lib/i18n'
import { ToastProvider } from './ui/primitives'
import { createQueryClient } from './state/queryClient'
import { useApplyTextScale } from './state/useApplyTextScale'
import { router } from './router'

/** 三域在这里各就各位：权威域（QueryClient）、界面域（uiStore）、活动域（runStore）。 */
export function App() {
  const [client] = useState(createQueryClient)
  useApplyTextScale()

  return (
    <QueryClientProvider client={client}>
      <LocaleProvider>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </LocaleProvider>
    </QueryClientProvider>
  )
}
