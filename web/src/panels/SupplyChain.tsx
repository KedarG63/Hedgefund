import type { ColDef } from 'ag-grid-community'
import { useMemo } from 'react'

import type { Row } from '../lib/api'
import {
  Grid, PanelHeader, PanelState, S, fixed, num, signed, str, usePanelQuery,
} from '../lib/panel'

/**
 * Maritime chokepoints: where each corridor stands, and whether the traffic
 * rerouted or vanished.
 *
 * Two columns that must not be collapsed. `z vs regime` asks whether today is
 * unusual FOR THE CURRENT REGIME; `vs baseline` asks how far the route has
 * moved from the pre-break world. A closed strait is perfectly normal for
 * itself -- Hormuz reads ~0 on the first and about -93% on the second, and
 * only the second describes the disruption.
 *
 * `interpretation` is the column that decides the trade: rerouted cargo is a
 * freight-rate story, destroyed cargo is a crude-availability story, and the
 * z-score alone cannot tell them apart.
 */

const READING: Record<string, { color: string; note: string }> = {
  rerouted: { color: 'var(--bullish)', note: 'absorbed by a substitute route — freight-rate story' },
  no_substitute_route: { color: 'var(--bearish)', note: 'no alternative exists — availability story' },
  traffic_destroyed: { color: 'var(--bearish)', note: 'traffic gone, not moved' },
}

export function SupplyChain() {
  const q = usePanelQuery(['supply-regimes'], '/supply-chain/regimes')

  const columns = useMemo<ColDef<Row>[]>(() => [
    { field: 'portname', headerName: 'Corridor', width: 190, pinned: 'left' },
    { field: 'metric', headerName: 'Metric', width: 110, cellClass: 'mono' },
    {
      field: 'z_vs_regime', headerName: 'z vs regime', width: 110,
      type: 'numericColumn', cellClass: 'mono',
      valueFormatter: (p) => signed(p.value),
      headerTooltip: 'Unusual for the CURRENT regime. A closed strait reads ~0 here.',
    },
    {
      field: 'vs_baseline_pct', headerName: 'vs baseline %', width: 125,
      type: 'numericColumn', cellClass: 'mono',
      valueFormatter: (p) => signed(p.value, 1),
      cellStyle: (p) => {
        const n = num(p.value)
        return { color: n === null ? 'var(--ink-3)' : n < -20 ? 'var(--bearish)'
                        : n > 20 ? 'var(--bullish)' : 'var(--ink-2)' }
      },
      headerTooltip: 'Distance from the pre-break world. This is the column that describes a disruption.',
    },
    {
      field: 'interpretation', headerName: 'Reading', width: 180,
      cellClass: 'mono',
      cellStyle: (p) => ({ color: READING[str(p.value)]?.color ?? 'var(--ink-3)' }),
      tooltipValueGetter: (p) => READING[str(p.value)]?.note ?? '',
    },
    {
      field: 'absorbed_pct', headerName: 'Absorbed %', width: 110,
      type: 'numericColumn', cellClass: 'mono',
      valueFormatter: (p) => fixed(p.value, 1),
    },
    { field: 'substitute', headerName: 'Substitute', flex: 1, minWidth: 160 },
    { field: 'days_in_regime', headerName: 'Days', width: 80, cellClass: 'mono',
      type: 'numericColumn' },
  ], [])

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>
          chokepoints — reroute vs supply loss
        </span>
      </PanelHeader>
      <div style={S.body}>
        <PanelState query={q}
                    empty="Run `python run_daily.py --job analytics_supply_chain`.">
          {(frame) => <Grid rows={frame.rows} columns={columns}
                            getRowId={(r) => `${str(r.portname)}:${str(r.metric)}`} />}
        </PanelState>
      </div>
    </div>
  )
}
