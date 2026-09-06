import { useMemo, useState } from 'react'
import type { ColDef, ColGroupDef } from 'ag-grid-community'

import type { Row } from '../lib/api'
import {
  Grid, PanelHeader, PanelState, S, compact, fixed, num, str, useSymbolQuery,
} from '../lib/panel'

/**
 * The option ladder: calls left, strike centre, puts right.
 *
 * That layout is not decoration -- it is how skew is read. The API pivots
 * option_type into one row per strike so this stays a plain grid; column
 * groups give the conventional shape.
 *
 * Strikes nearest the underlying are what matter, so the grid opens scrolled
 * to the money rather than at the lowest strike.
 */
export function OptionChain() {
  const [expiry, setExpiry] = useState<string>('')
  const q = useSymbolQuery('options', (s) => `/instrument/${s}/options`)

  const expiries = useMemo(() => {
    const all = (q.data?.rows ?? []).map((r) => str(r.expiry_date)).filter(Boolean)
    return Array.from(new Set(all))
  }, [q.data])

  const active = expiry || expiries[0] || ''
  const rows = useMemo(
    () => (q.data?.rows ?? []).filter((r) => !active || str(r.expiry_date) === active),
    [q.data, active],
  )
  const underlying = num(rows[0]?.underlying)

  // Column GROUPS, not plain columns -- CALLS | STRIKE | PUTS is the layout the
  // ladder is read in, so the type has to admit ColGroupDef.
  const columns = useMemo<(ColDef<Row> | ColGroupDef<Row>)[]>(() => {
    const oi = (f: string, h: string): ColDef<Row> => ({
      field: f, headerName: h, width: 96, type: 'numericColumn', cellClass: 'mono',
      valueFormatter: (p) => compact(p.value),
    })
    const n = (f: string, h: string, dp = 2): ColDef<Row> => ({
      field: f, headerName: h, width: 84, type: 'numericColumn', cellClass: 'mono',
      valueFormatter: (p) => fixed(p.value, dp),
    })
    return [
      { headerName: 'CALLS', children: [oi('call_oi', 'OI'), oi('call_oi_chg', 'ΔOI'),
                                        n('call_ltp', 'LTP'), n('call_iv', 'IV', 1),
                                        n('call_delta', 'Δ', 3)] },
      {
        headerName: '', children: [{
          field: 'strike_price', headerName: 'STRIKE', width: 100, pinned: 'left',
          cellClass: 'mono', type: 'numericColumn',
          valueFormatter: (p) => fixed(p.value, 0),
          // The at-the-money row is the anchor the whole ladder is read from.
          cellStyle: (p) => {
            const s = num(p.value)
            if (underlying === null || s === null) return { fontWeight: 600 }
            const atm = Math.abs(s - underlying) < 50
            return {
              fontWeight: 600,
              color: atm ? 'var(--accent)' : 'var(--ink)',
              background: atm ? 'var(--accent-soft)' : undefined,
            }
          },
        }],
      },
      { headerName: 'PUTS', children: [n('put_delta', 'Δ', 3), n('put_iv', 'IV', 1),
                                       n('put_ltp', 'LTP'), oi('put_oi_chg', 'ΔOI'),
                                       oi('put_oi', 'OI')] },
    ]
  }, [underlying])

  return (
    <div style={S.panel}>
      <PanelHeader>
        <b className="mono" style={{ color: 'var(--accent)' }}>{q.symbol}</b>
        {expiries.map((e) => (
          <button key={e} onClick={() => setExpiry(e)} className="mono" style={{
            fontSize: 11, padding: '2px 7px', borderRadius: 3,
            color: e === active ? 'var(--ink)' : 'var(--ink-3)',
            background: e === active ? 'var(--accent-soft)' : 'transparent',
            border: `1px solid ${e === active ? 'var(--accent-line)' : 'transparent'}`,
          }}>{e}</button>
        ))}
        {underlying !== null && (
          <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 11 }}>
            spot {underlying.toFixed(2)}
          </span>
        )}
      </PanelHeader>
      <div style={S.body}>
        <PanelState query={q}
                    empty={`No option chain captured for ${q.symbol} — the job covers NIFTY and BANKNIFTY.`}>
          {() => <Grid rows={rows} columns={columns}
                       getRowId={(r) => `${str(r.expiry_date)}:${str(r.strike_price)}`} />}
        </PanelState>
      </div>
    </div>
  )
}
