import { useState } from 'react'
import type { CSSProperties } from 'react'

import { PANELS } from '../panels/registry'
import { openPanel } from './Dock'

/**
 * Summon a panel into the workspace.
 *
 * Each entry names the DuckDB views it reads. That is the point rather than a
 * detail: on a screen where every number is one query away from a trade, being
 * able to see where a panel's data comes from without leaving it is what keeps
 * the terminal honest. It is also the anchor the provenance drill hangs off.
 */
export function PanelLauncher() {
  const [open, setOpen] = useState(false)

  return (
    <div style={{ position: 'relative' }}>
      <button
        className="mono"
        style={S.trigger}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        + panel
      </button>
      {open && (
        <>
          <div style={S.scrim} onClick={() => setOpen(false)} />
          <div style={S.menu}>
            {PANELS.map((p) => (
              <button
                key={p.id}
                style={S.row}
                onClick={() => { openPanel(p.id, p.title); setOpen(false) }}
              >
                <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span className="mono" style={{ color: 'var(--accent)', minWidth: 108 }}>
                    {p.title}
                  </span>
                  {p.scoped && (
                    <span className="mono" style={S.scoped} title="Follows the focus symbol">
                      symbol
                    </span>
                  )}
                </span>
                <span className="mono" style={S.reads} title={p.reads.join('\n')}>
                  {p.reads.length === 1 ? p.reads[0] : `${p.reads.length} views`}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  trigger: {
    fontSize: 11, color: 'var(--ink-3)', border: '1px solid var(--line)',
    borderRadius: 4, padding: '3px 9px', whiteSpace: 'nowrap',
  },
  scrim: { position: 'fixed', inset: 0, zIndex: 40 },
  menu: {
    position: 'absolute', top: '100%', right: 0, marginTop: 5, zIndex: 50,
    minWidth: 320, background: 'var(--panel)', border: '1px solid var(--accent-line)',
    borderRadius: 6, boxShadow: '0 8px 24px rgba(0,0,0,.5)', overflow: 'hidden',
    maxHeight: '70vh', overflowY: 'auto',
  },
  row: {
    display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
    gap: 12, width: '100%', padding: '6px 11px', textAlign: 'left', fontSize: 12,
  },
  scoped: {
    fontSize: 9, letterSpacing: '.08em', textTransform: 'uppercase',
    color: 'var(--warn)', border: '1px solid var(--warn)', borderRadius: 3,
    padding: '0 4px', opacity: 0.75,
  },
  reads: { fontSize: 10, color: 'var(--ink-3)' },
}
