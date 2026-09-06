import type { ColDef } from 'ag-grid-community'
import { useMemo } from 'react'

import type { Row } from '../lib/api'
import {
  Grid, PanelState, monoCol, scoreCol, str, textCol, usePanelQuery,
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

  const columns = useMemo<ColDef<Row>[]>(() => [
    {
      ...monoCol('symbol', 'Symbol', 120), pinned: 'left',
      cellStyle: (p) => (str(p.data?.symbol) === symbol
        ? { color: 'var(--accent)', fontWeight: 600 }
        : undefined),
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
      {(frame) => (
        <Grid rows={frame.rows} columns={columns}
              onRowClick={(r) => setSymbol(str(r.symbol))}
              getRowId={(r) => str(r.symbol)} />
      )}
    </PanelState>
  )
}
