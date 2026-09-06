import type { ColDef } from 'ag-grid-community'
import { useMemo, useState } from 'react'

import type { Row } from '../lib/api'
import {
  Grid, PanelHeader, PanelState, S, compact, fixed, num, signed, str, usePanelQuery,
} from '../lib/panel'

/**
 * Institutional positioning.
 *
 * India publishes participant-wise open interest daily and free; no other major
 * market does. The US analogue is 13F, on a 45-day lag.
 *
 * The divergence tab is a DATA-QUALITY view, not a market view: NSE's
 * participant OI and Upstox's separately-polled feed should report the same
 * number, so a flagged row means one source is stale or wrong. Keeping it
 * beside the flows is deliberate -- it is the check you want in view while
 * reading them.
 */

const TABS = [
  { key: 'participants', label: 'Participant OI', path: '/flows/participants' },
  { key: 'fii-dii', label: 'FII / DII cash', path: '/flows/fii-dii' },
  { key: 'divergence', label: 'Source check', path: '/flows/divergence' },
] as const

export function Flows() {
  const [tab, setTab] = useState<(typeof TABS)[number]>(TABS[0])
  const q = usePanelQuery(['flows', tab.key], tab.path, { limit: 400 })

  const columns = useMemo<ColDef<Row>[]>(() => {
    if (tab.key === 'participants') {
      return [
        { field: 'trade_date', headerName: 'Date', width: 110, cellClass: 'mono' },
        { field: 'client_type', headerName: 'Client', width: 110 },
        { field: 'book', headerName: 'Book', width: 130, cellClass: 'mono' },
        { field: 'side', headerName: 'Side', width: 80, cellClass: 'mono',
          cellStyle: (p) => ({ color: str(p.value) === 'long' ? 'var(--bullish)' : 'var(--bearish)' }) },
        { field: 'contracts', headerName: 'Contracts', flex: 1, type: 'numericColumn',
          cellClass: 'mono', valueFormatter: (p) => compact(p.value) },
      ]
    }
    if (tab.key === 'fii-dii') {
      return [
        { field: 'date', headerName: 'Date', width: 120, cellClass: 'mono' },
        { field: 'category', headerName: 'Category', width: 110 },
        { field: 'buy_value', headerName: 'Buy (Cr)', width: 120, type: 'numericColumn',
          cellClass: 'mono', valueFormatter: (p) => fixed(p.value, 1) },
        { field: 'sell_value', headerName: 'Sell (Cr)', width: 120, type: 'numericColumn',
          cellClass: 'mono', valueFormatter: (p) => fixed(p.value, 1) },
        { field: 'net_value', headerName: 'Net (Cr)', flex: 1, type: 'numericColumn',
          cellClass: 'mono', valueFormatter: (p) => signed(p.value, 1),
          cellStyle: (p) => {
            const n = num(p.value)
            return { color: n === null ? 'var(--ink-3)' : n >= 0 ? 'var(--bullish)' : 'var(--bearish)',
                     fontWeight: 600 }
          } },
      ]
    }
    return [
      { field: 'metric', headerName: 'Metric', width: 240 },
      { field: 'nse_value', headerName: 'NSE', width: 130, type: 'numericColumn',
        cellClass: 'mono', valueFormatter: (p) => compact(p.value) },
      { field: 'upstox_value', headerName: 'Upstox', width: 130, type: 'numericColumn',
        cellClass: 'mono', valueFormatter: (p) => compact(p.value) },
      { field: 'pct_diff', headerName: 'Diff %', width: 110, type: 'numericColumn',
        cellClass: 'mono', valueFormatter: (p) => signed(p.value, 2) },
      { field: 'diverges', headerName: 'Flagged', flex: 1,
        // Text, not a colour swatch: "AGREE"/"DIVERGES" survives a screenshot,
        // a colourblind reader, and a printout.
        valueFormatter: (p) => (p.value ? 'DIVERGES' : 'agree'),
        cellStyle: (p) => ({ color: p.value ? 'var(--bearish)' : 'var(--ink-3)',
                             fontWeight: p.value ? 600 : 400 }) },
    ]
  }, [tab])

  return (
    <div style={S.panel}>
      <PanelHeader>
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t)} className="mono" style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 3,
            color: t.key === tab.key ? 'var(--ink)' : 'var(--ink-3)',
            background: t.key === tab.key ? 'var(--accent-soft)' : 'transparent',
            border: `1px solid ${t.key === tab.key ? 'var(--accent-line)' : 'transparent'}`,
          }}>{t.label}</button>
        ))}
      </PanelHeader>
      <div style={S.body}>
        <PanelState query={q} empty="Not ingested yet.">
          {(frame) => <Grid rows={frame.rows} columns={columns} />}
        </PanelState>
      </div>
    </div>
  )
}
