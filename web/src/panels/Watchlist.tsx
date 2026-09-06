import { useQuery } from '@tanstack/react-query'
import { AllCommunityModule, ModuleRegistry, type ColDef } from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import { useMemo } from 'react'

import { fetchFrame, num, str, type Row } from '../lib/api'
import { useContextStore } from '../store/context'

// AG Grid 34 ships nothing by default; the community bundle must be registered
// once before any grid mounts.
ModuleRegistry.registerModules([AllCommunityModule])

/**
 * Instruments ranked by composite factor score.
 *
 * The left rail, and the panel that drives the rest of the screen: clicking a
 * row sets the context symbol, which re-scopes every subscribed panel. That is
 * the whole cross-filter mechanism -- no panel-to-panel wiring exists.
 *
 * Scores are coloured on the project's blue<->red diverging pair AND printed
 * with an explicit sign, because colour never carries a signal alone here.
 */
export function Watchlist() {
  const symbol = useContextStore((s) => s.symbol)
  const setSymbol = useContextStore((s) => s.setSymbol)
  const asOf = useContextStore((s) => s.asOf)

  const { data, isLoading, error } = useQuery({
    queryKey: ['watchlist', asOf ?? 'now'],
    queryFn: ({ signal }) => fetchFrame('/watchlist', { limit: 500, as_of: asOf }, signal),
  })

  const columns = useMemo<ColDef<Row>[]>(() => [
    {
      field: 'symbol', headerName: 'Symbol', pinned: 'left', width: 120,
      cellClass: 'mono',
      cellStyle: (p) => (str(p.data?.symbol) === symbol
        ? { color: 'var(--accent)', fontWeight: 600 }
        : undefined),
    },
    {
      field: 'composite_score', headerName: 'Composite', width: 110, type: 'numericColumn',
      cellClass: 'mono',
      valueFormatter: (p) => signed(p.value),
      cellStyle: (p) => ({ color: diverging(num(p.value)) }),
    },
    {
      field: 'momentum_zscore', headerName: 'Momentum z', width: 120, type: 'numericColumn',
      cellClass: 'mono', valueFormatter: (p) => signed(p.value),
      cellStyle: (p) => ({ color: diverging(num(p.value)) }),
    },
    { field: 'size_score', headerName: 'Size', width: 90, type: 'numericColumn',
      cellClass: 'mono', valueFormatter: (p) => signed(p.value) },
    { field: 'low_vol_score', headerName: 'Low vol', width: 100, type: 'numericColumn',
      cellClass: 'mono', valueFormatter: (p) => signed(p.value) },
    { field: 'tier', headerName: 'Tier', width: 110 },
    { field: 'flag', headerName: 'Flag', flex: 1, minWidth: 220, tooltipField: 'flag' },
    { field: 'as_of_date', headerName: 'As of', width: 110, cellClass: 'mono' },
  ], [symbol])

  if (error) return <PanelMessage tone="bad">{(error as Error).message}</PanelMessage>
  if (isLoading) return <PanelMessage>loading…</PanelMessage>
  if (!data?.rows.length) {
    return (
      <PanelMessage>
        No factor scores {asOf ? `as of ${asOf}` : 'yet'}.
        {asOf && ' Try clearing the knowledge date.'}
      </PanelMessage>
    )
  }

  return (
    <div className="ag-theme-quartz-dark" style={{ height: '100%', width: '100%' }}>
      <AgGridReact<Row>
        rowData={data.rows}
        columnDefs={columns}
        rowHeight={24}
        headerHeight={28}
        rowSelection={{ mode: 'singleRow', checkboxes: false, enableClickSelection: true }}
        onRowClicked={(e) => e.data && setSymbol(str(e.data.symbol))}
        getRowId={(p) => str(p.data.symbol)}
        suppressCellFocus={false}
      />
    </div>
  )
}

/** Signed to two places -- the sign is the signal, not the colour. */
function signed(v: unknown): string {
  const n = num(v)
  return n === null ? '' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}`
}

/** dashboard/theme.py's pair, deliberately blue<->red rather than red<->green. */
function diverging(n: number | null): string {
  if (n === null) return 'var(--ink-3)'
  if (n > 0.05) return 'var(--bullish)'
  if (n < -0.05) return 'var(--bearish)'
  return 'var(--ink-2)'
}

export function PanelMessage({ children, tone }: { children: React.ReactNode; tone?: 'bad' }) {
  return (
    <div style={{
      height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 20, textAlign: 'center', fontSize: 12,
      color: tone === 'bad' ? 'var(--bearish)' : 'var(--ink-3)',
    }}>
      {children}
    </div>
  )
}
