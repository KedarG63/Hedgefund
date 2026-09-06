import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'

import 'dockview/dist/styles/dockview.css'
import './lib/tokens.css'

/**
 * TanStack Query is doing real work here, not just caching.
 *
 * Panels share context keys, so six panels on one symbol would otherwise fire
 * six identical DuckDB queries on every context change. Deduplication happens
 * here, once, instead of each panel hand-rolling it.
 *
 * No refetch on window focus: alt-tabbing back to a terminal should not silently
 * re-query the warehouse, and on a back-dated screen it would be pure waste --
 * a historical answer cannot change.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 10 * 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
