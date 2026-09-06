import { useState } from 'react'

import { PanelHeader, PanelState, S, num, str, useSymbolQuery } from '../lib/panel'

/**
 * Income statement / balance sheet / cash flow, assembled server-side.
 *
 * The API returns line items as rows and periods as columns, already merged
 * and ordered by api/serializers/fundamentals.py -- the same code the Streamlit
 * dashboard calls. None of the concept mapping or fiscal-year labelling is
 * reimplemented here, which was the entire point of extracting it in Phase 0.
 *
 * The balance sheet's check row is part of the statement, not a flourish: it
 * should read ~0, and a non-zero value means the merge picked up two different
 * period definitions under one label.
 */

const TABS = [
  { key: 'income', label: 'Quarterly P&L' },
  { key: 'balance', label: 'Balance sheet' },
  { key: 'cash', label: 'Cash flow' },
] as const

export function Fundamentals() {
  const [statement, setStatement] = useState<(typeof TABS)[number]['key']>('income')
  const q = useSymbolQuery('fundamentals', (s) => `/instrument/${s}/fundamentals`, { statement })

  return (
    <div style={S.panel}>
      <PanelHeader>
        <b className="mono" style={{ color: 'var(--accent)' }}>{q.symbol}</b>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setStatement(t.key)}
            className="mono"
            style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 3,
              color: statement === t.key ? 'var(--ink)' : 'var(--ink-3)',
              background: statement === t.key ? 'var(--accent-soft)' : 'transparent',
              border: `1px solid ${statement === t.key ? 'var(--accent-line)' : 'transparent'}`,
            }}
          >
            {t.label}
          </button>
        ))}
        <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 11 }}>Rs Cr</span>
      </PanelHeader>
      <div style={S.scroll}>
        <PanelState query={q} empty={`No XBRL filings ingested for ${q.symbol}.`}>
          {(frame) => {
            const periods = Object.keys(frame.rows[0] ?? {}).filter((k) => k !== 'line_item')
            return (
              <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ ...TH, textAlign: 'left', position: 'sticky', left: 0,
                                 background: 'var(--panel-2)', zIndex: 2 }}>
                      Line item
                    </th>
                    {periods.map((p) => (
                      <th key={p} style={TH} className="mono">{p}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {frame.rows.map((r, i) => {
                    const label = str(r.line_item)
                    const isCheck = label.startsWith('Balance check')
                    return (
                      <tr key={i} style={{
                        borderBottom: '1px solid var(--line-soft)',
                        background: isCheck ? 'var(--accent-soft)' : undefined,
                      }}>
                        <td style={{
                          padding: '5px 10px', position: 'sticky', left: 0, zIndex: 1,
                          background: isCheck ? 'var(--panel-2)' : 'var(--panel)',
                          color: isCheck ? 'var(--ink-2)' : 'var(--ink)',
                          whiteSpace: 'nowrap',
                        }}>
                          {label}
                        </td>
                        {periods.map((p) => {
                          const v = num(r[p])
                          return (
                            <td key={p} className="mono" style={{
                              padding: '5px 10px', textAlign: 'right', whiteSpace: 'nowrap',
                              // The check row is the one place a non-zero is the
                              // alarm, so it is coloured against zero, not sign.
                              color: isCheck
                                ? (v !== null && Math.abs(v) > 1 ? 'var(--bearish)' : 'var(--ink-3)')
                                : 'var(--ink)',
                            }}>
                              {v === null ? '' : v.toLocaleString('en-IN', {
                                minimumFractionDigits: 2, maximumFractionDigits: 2,
                              })}
                            </td>
                          )
                        })}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )
          }}
        </PanelState>
      </div>
    </div>
  )
}

const TH: React.CSSProperties = {
  padding: '6px 10px', textAlign: 'right', whiteSpace: 'nowrap',
  fontSize: 10, letterSpacing: '.08em', textTransform: 'uppercase',
  color: 'var(--ink-3)', background: 'var(--panel-2)',
  borderBottom: '1px solid var(--line)', position: 'sticky', top: 0,
}
