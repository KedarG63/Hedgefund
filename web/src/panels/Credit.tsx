import { useState } from 'react'

import { Grid, PanelHeader, PanelState, S, monoCol, textCol, usePanelQuery } from '../lib/panel'
import type { Row } from '../lib/api'

/**
 * Rating actions across CRISIL, ICRA and CARE.
 *
 * The reason this earns a panel: rating agencies cover PRIVATE issuers, which
 * is the one routine window this warehouse has into balance sheets that never
 * file with an exchange -- exactly where a listed parent's consolidated
 * accounts can hide something. A downgrade at an unlisted subsidiary and one at
 * a listed parent arrive in the same undifferentiated feed.
 */
export function Credit() {
  const [q, setQ] = useState('')
  const [committed, setCommitted] = useState('')
  const query = usePanelQuery(['credit', committed], '/credit/actions',
                              { q: committed || undefined, limit: 400 })

  const columns = [
    { ...monoCol('agency', 'Agency', 80) },
    { ...monoCol('rating_date', 'Date', 100) },
    { ...textCol('company_name', 'Company', 260) },
    { ...monoCol('action_type', 'Action', 130) },
    { ...textCol('heading', 'Rationale') },
    { ...monoCol('category', 'Category', 150) },
  ]

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>credit actions</span>
        <input
          className="mono"
          value={q}
          placeholder="company contains…"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setCommitted(q.trim())}
          onBlur={() => setCommitted(q.trim())}
          style={{
            background: 'var(--panel-2)', border: '1px solid var(--line)',
            borderRadius: 3, padding: '2px 7px', fontSize: 11, width: 200,
          }}
        />
        <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 11 }}>
          {query.data?.rows.length ?? 0} actions
        </span>
      </PanelHeader>
      <div style={S.body}>
        <PanelState query={query}
                    empty={committed ? `No rating actions matching "${committed}".`
                                     : 'No rating actions ingested yet.'}>
          {(frame) => (
            <Grid rows={frame.rows} columns={columns}
                  getRowId={(r: Row) => `${r.agency}:${r.action_id}`} />
          )}
        </PanelState>
      </div>
    </div>
  )
}
