import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * The one piece of state every panel reads.
 *
 * THIS IS THE CROSS-FILTER. A panel does not talk to another panel; it
 * subscribes here, and anything that changes the context re-scopes the whole
 * screen. That is the single behaviour Streamlit could not give us without a
 * full script rerun per click, and it is why the migration was worth doing.
 *
 * `asOf` is a plain ISO date string or null (= everything known now), matching
 * the API's ?as_of= contract exactly, so no conversion happens at the boundary.
 * Setting it rolls back prices AND derived signals together, because every
 * derived_* table is its own knowledge_date-stamped vintage.
 *
 * Persisted so a reload lands you back where you were -- a terminal that
 * forgets its symbol on refresh is a webpage, not a workstation. Version is
 * bumped when the shape changes so a stale payload is dropped rather than
 * half-applied.
 */
export interface TerminalContext {
  symbol: string
  asOf: string | null
  setSymbol: (symbol: string) => void
  setAsOf: (asOf: string | null) => void
}

export const useContextStore = create<TerminalContext>()(
  persist(
    (set) => ({
      symbol: 'RELIANCE',
      asOf: null,
      setSymbol: (symbol) => set({ symbol: symbol.toUpperCase() }),
      setAsOf: (asOf) => set({ asOf: asOf || null }),
    }),
    { name: 'qd.context', version: 1 },
  ),
)

/**
 * The query-key fragment every panel must include.
 *
 * Panels that forget the as-of date would keep serving cached live data after
 * the scrubber moves -- showing today's numbers under a historical banner,
 * which is the exact failure the banner exists to prevent. Deriving the key
 * here makes that hard to get wrong.
 */
export const contextKey = (c: Pick<TerminalContext, 'symbol' | 'asOf'>) =>
  [c.symbol, c.asOf ?? 'now'] as const
