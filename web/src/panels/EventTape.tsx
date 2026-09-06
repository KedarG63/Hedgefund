import { PanelHeader, PanelState, S, str, useSymbolQuery } from '../lib/panel'

/**
 * Announcements, insider filings and bulk/block deals as one reverse-
 * chronological tape.
 *
 * Unioned server-side into {ts, kind, headline, detail} so this stays a single
 * list rather than four tables the reader has to interleave by eye. The kind
 * stripe encodes the source in position and text, not colour alone.
 */

const KIND_COLOR: Record<string, string> = {
  announcement: 'var(--accent)',
  insider: 'var(--warn)',
  'bulk deal': 'var(--bullish)',
  'block deal': 'var(--bullish)',
}

export function EventTape() {
  const q = useSymbolQuery('events', (s) => `/instrument/${s}/events`, { limit: 200 })

  return (
    <div style={S.panel}>
      <PanelHeader>
        <b className="mono" style={{ color: 'var(--accent)' }}>{q.symbol}</b>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>
          {q.data?.rows.length ?? 0} events
        </span>
      </PanelHeader>
      <div style={S.scroll}>
        <PanelState query={q} empty={`No filings or deals recorded for ${q.symbol}.`}>
          {(frame) => (
            <div>
              {frame.rows.map((r, i) => {
                const kind = str(r.kind)
                return (
                  <div key={i} style={{
                    display: 'flex', gap: 10, padding: '7px 10px',
                    borderBottom: '1px solid var(--line-soft)',
                    borderLeft: `2px solid ${KIND_COLOR[kind] ?? 'var(--line)'}`,
                  }}>
                    <span className="mono" style={{
                      color: 'var(--ink-3)', fontSize: 11, width: 88, flexShrink: 0,
                    }}>
                      {str(r.ts).slice(0, 10)}
                    </span>
                    <span className="mono" style={{
                      color: KIND_COLOR[kind] ?? 'var(--ink-3)', fontSize: 10,
                      width: 82, flexShrink: 0, textTransform: 'uppercase',
                      letterSpacing: '.05em',
                    }}>
                      {kind}
                    </span>
                    <span style={{ fontSize: 12, minWidth: 0 }} title={str(r.detail)}>
                      {str(r.headline) || <i style={{ color: 'var(--ink-3)' }}>no headline</i>}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </PanelState>
      </div>
    </div>
  )
}
