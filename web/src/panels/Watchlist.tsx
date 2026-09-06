import type { ColDef } from 'ag-grid-community'
import { useMemo } from 'react'

import type { Row } from '../lib/api'
import { changePct, useLiveTicks } from '../lib/live'
import {
  Grid, PanelState, fixed, monoCol, num, scoreCol, signed, str, textCol, usePanelQuery,
} from '../lib/panel'
import { useContextStore } from '../store/context'

/**
 * Instruments ranked by composite factor score.
 *
 * The panel that drives the rest of the screen: clicking a row sets the
 * context symbol, which re-scopes every subscribed panel. No panel-to-panel
 * wiring exists -- that is the whole cross-filter mechanism.
 *
 * derived_digest only covers the shortlist the LLM phrased a flag for, so
 * `flag` and `tier` are frequently blank. The ranking itself is deterministic
 * and covers the full scanned universe.
 */
export function Watchlist() {
  const symbol = useContextStore((s) => s.symbol)
  const setSymbol = useContextStore((s) => s.setSymbol)
  const q = usePanelQuery(['watchlist'], '/watchlist', { limit: 500 })
  const asOf = useContextStore((s) => s.asOf)

  // Live prices are meaningless on a back-dated screen -- the whole point of
  // as-of is to show what was known then, and a ticking LTP from today would
  // contradict every other number in the row. Subscribe to nothing.
  const visible = useMemo(
    () => (asOf ? [] : (q.data?.rows ?? []).slice(0, 60).map((r) => str(r.symbol))),
    [q.data, asOf],
  )
  const live = useLiveTicks(visible)

  // Merge the tick onto the row so AG Grid's immutable diff sees a changed
  // value and can flash the cell. getRowId keeps row identity stable across
  // the merge, which is what makes the flash a flash rather than a re-render.
  const rows = useMemo(() => {
    const base = q.data?.rows ?? []
    if (!Object.keys(live.ticks).length) return base
    return base.map((r) => {
      const t = live.ticks[str(r.symbol)]
      return t ? { ...r, ltp: t.ltp, chg_pct: changePct(t) } : r
    })
  }, [q.data, live.ticks])

  const columns = useMemo<ColDef<Row>[]>(() => [
    {
      ...monoCol('symbol', 'Symbol', 120), pinned: 'left',
      cellStyle: (p) => (str(p.data?.symbol) === symbol
        ? { color: 'var(--accent)', fontWeight: 600 }
        : undefined),
    },
    {
      field: 'ltp', headerName: 'LTP', width: 96, type: 'numericColumn',
      cellClass: 'mono',
      // The flash IS the live indicator: a number that changes without moving
      // is easy to miss on a screen this dense.
      enableCellChangeFlash: true,
      valueFormatter: (p) => fixed(p.value),
    },
    {
      field: 'chg_pct', headerName: 'Chg %', width: 92, type: 'numericColumn',
      cellClass: 'mono', enableCellChangeFlash: true,
      valueFormatter: (p) => signed(p.value),
      cellStyle: (p) => {
        const n = num(p.value)
        return { color: n === null ? 'var(--ink-3)'
                        : n >= 0 ? 'var(--bullish)' : 'var(--bearish)' }
      },
    },
    scoreCol('composite_score', 'Composite'),
    scoreCol('momentum_zscore', 'Momentum z', 120),
    scoreCol('size_score', 'Size', 90),
    scoreCol('low_vol_score', 'Low vol', 100),
    monoCol('tier', 'Tier', 110),
    textCol('flag', 'Flag'),
    monoCol('as_of_date', 'As of', 105),
  ], [symbol])

  return (
    <PanelState query={q} empty="No factor scores computed yet — run `run_daily.py --job analytics_risk_model`.">
      {() => (
        <Grid rows={rows} columns={columns}
              onRowClick={(r) => setSymbol(str(r.symbol))}
              getRowId={(r) => str(r.symbol)} />
      )}
    </PanelState>
  )
}
