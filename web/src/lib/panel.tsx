import { useQuery } from '@tanstack/react-query'
import {
  AllCommunityModule, ModuleRegistry, type ColDef, type ColGroupDef,
} from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import type { CSSProperties, ReactNode } from 'react'

import { fetchFrame, num, str, type Frame, type Row } from './api'
import { useContextStore } from '../store/context'

// AG Grid 34 ships nothing by default. Registering here, in the module every
// grid-bearing panel imports, means no panel has to remember to do it.
ModuleRegistry.registerModules([AllCommunityModule])

/**
 * The shared panel toolkit.
 *
 * The point of a thin contract is that adding a panel should be a small file,
 * not a new set of decisions. Everything a panel repeats -- binding to the
 * context, threading ?as_of=, the four states of a query, grid defaults, and
 * the sign-plus-colour convention -- lives here once.
 */

/** Bind a query to the context so as_of is threaded and cached per date. */
export function usePanelQuery(
  key: readonly unknown[],
  path: string,
  params: Record<string, string | number | undefined | null> = {},
  opts: { enabled?: boolean } = {},
) {
  const asOf = useContextStore((s) => s.asOf)
  return useQuery({
    // as_of is part of the key, always. A panel that forgets it keeps serving
    // live rows under a historical banner -- the exact failure the banner
    // exists to prevent.
    queryKey: [...key, asOf ?? 'now'],
    queryFn: ({ signal }) => fetchFrame(path, { ...params, as_of: asOf }, signal),
    enabled: opts.enabled ?? true,
  })
}

/** Bind a query to the context symbol as well. */
export function useSymbolQuery(
  key: string,
  path: (symbol: string) => string,
  params: Record<string, string | number | undefined | null> = {},
) {
  const symbol = useContextStore((s) => s.symbol)
  const q = usePanelQuery([key, symbol], path(symbol), params, { enabled: Boolean(symbol) })
  return { ...q, symbol }
}

export function PanelMessage({ children, tone }: { children: ReactNode; tone?: 'bad' }) {
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

/**
 * Render the four states a panel query can be in.
 *
 * `empty` is separated from `error` deliberately: "the job that writes this
 * has not run" and "the request failed" look identical to a user otherwise,
 * and they need completely different actions.
 */
export function PanelState({
  query, empty, children,
}: {
  query: { isLoading: boolean; error: unknown; data?: Frame }
  empty?: ReactNode
  children: (frame: Frame) => ReactNode
}) {
  const asOf = useContextStore((s) => s.asOf)
  if (query.error) return <PanelMessage tone="bad">{(query.error as Error).message}</PanelMessage>
  if (query.isLoading) return <PanelMessage>loading…</PanelMessage>
  if (!query.data?.rows.length) {
    return (
      <PanelMessage>
        {empty ?? 'Nothing here yet.'}
        {asOf && <div style={{ marginTop: 6, color: 'var(--warn)' }}>as of {asOf}</div>}
      </PanelMessage>
    )
  }
  return <>{children(query.data)}</>
}

/** A grid with the terminal's defaults. Panels supply columns and rows only. */
export function Grid({
  rows, columns, onRowClick, getRowId,
}: {
  rows: Row[]
  // ColGroupDef too: the option ladder needs CALLS | STRIKE | PUTS groups.
  columns: (ColDef<Row> | ColGroupDef<Row>)[]
  onRowClick?: (row: Row) => void
  getRowId?: (row: Row) => string
}) {
  return (
    <div className="ag-theme-quartz-dark" style={{ height: '100%', width: '100%' }}>
      <AgGridReact<Row>
        rowData={rows}
        columnDefs={columns}
        rowHeight={24}
        headerHeight={28}
        defaultColDef={{ resizable: true, sortable: true }}
        onRowClicked={(e) => e.data && onRowClick?.(e.data)}
        getRowId={getRowId ? (p) => getRowId(p.data) : undefined}
      />
    </div>
  )
}

/* ---------------------------------------------------------------- formatting
 * Colour never carries a signal alone -- inherited from dashboard/theme.py and
 * enforced by always pairing `diverging()` with `signed()`, which prints the
 * sign in text. The pair is blue<->red, not red<->green: red-green is the most
 * common colourblindness and would make the most important column unreadable.
 */

export function signed(v: unknown, dp = 2): string {
  const n = num(v)
  return n === null ? '' : `${n >= 0 ? '+' : ''}${n.toFixed(dp)}`
}

export function fixed(v: unknown, dp = 2): string {
  const n = num(v)
  return n === null ? '' : n.toFixed(dp)
}

export function compact(v: unknown): string {
  const n = num(v)
  if (n === null) return ''
  const a = Math.abs(n)
  if (a >= 1e7) return `${(n / 1e7).toFixed(2)}Cr`
  if (a >= 1e5) return `${(n / 1e5).toFixed(2)}L`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return n.toFixed(0)
}

export function diverging(v: unknown, dead = 0.05): string {
  const n = num(v)
  if (n === null) return 'var(--ink-3)'
  if (n > dead) return 'var(--bullish)'
  if (n < -dead) return 'var(--bearish)'
  return 'var(--ink-2)'
}

/** A numeric column carrying the sign in text and the direction in colour. */
export function scoreCol(field: string, headerName: string, width = 110, dp = 2): ColDef<Row> {
  return {
    field, headerName, width, type: 'numericColumn', cellClass: 'mono',
    valueFormatter: (p) => signed(p.value, dp),
    cellStyle: (p) => ({ color: diverging(p.value) }),
  }
}

export function textCol(field: string, headerName: string, width?: number): ColDef<Row> {
  return { field, headerName, width, flex: width ? undefined : 1, tooltipField: field }
}

export function monoCol(field: string, headerName: string, width = 110): ColDef<Row> {
  return { field, headerName, width, cellClass: 'mono' }
}

export const S: Record<string, CSSProperties> = {
  panel: { height: '100%', display: 'flex', flexDirection: 'column' },
  header: {
    display: 'flex', alignItems: 'baseline', gap: 10, padding: '5px 10px',
    borderBottom: '1px solid var(--line-soft)', fontSize: 12, flexShrink: 0,
  },
  body: { flex: 1, minHeight: 0, position: 'relative' },
  scroll: { flex: 1, minHeight: 0, overflow: 'auto' },
}

/** A panel header that names the symbol and any caveat the data carries. */
export function PanelHeader({ children }: { children: ReactNode }) {
  return <div style={S.header}>{children}</div>
}

export { num, str }
