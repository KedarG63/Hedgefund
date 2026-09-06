import type { CSSProperties } from 'react'

import { AsOfScrubber } from './shell/AsOfScrubber'
import { CommandBar } from './shell/CommandBar'
import { Dock, resetLayout } from './shell/Dock'
import { StatusStrip } from './shell/StatusStrip'
import { useContextStore } from './store/context'

export function App() {
  const asOf = useContextStore((s) => s.asOf)

  return (
    <div style={S.app}>
      <div style={S.bar}>
        <span style={S.mark} className="mono">quantdata</span>
        <CommandBar />
        <span style={{ flex: 1 }} />
        <AsOfScrubber />
        <button style={S.reset} className="mono" onClick={resetLayout}
                title="Restore the default panel arrangement">
          reset layout
        </button>
      </div>

      <StatusStrip />

      {/*
        Historical mode is announced twice -- amber rule here, amber date in the
        strip and scrubber. A back-dated screen that reads as live is the
        expensive mistake, so it is worth being redundant about.
      */}
      {asOf && <div style={S.historical} className="mono">
        HISTORICAL — showing only what the warehouse knew on {asOf}
      </div>}

      <Dock />
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  app: { height: '100%', display: 'flex', flexDirection: 'column' },
  bar: {
    height: 'var(--bar-h)', display: 'flex', alignItems: 'center', gap: 14,
    padding: '0 12px', background: 'var(--panel)',
    borderBottom: '1px solid var(--line)', flexShrink: 0,
  },
  mark: { color: 'var(--ink-2)', fontSize: 12, letterSpacing: '.06em', whiteSpace: 'nowrap' },
  reset: {
    fontSize: 11, color: 'var(--ink-3)', border: '1px solid var(--line)',
    borderRadius: 4, padding: '3px 8px', whiteSpace: 'nowrap',
  },
  historical: {
    background: 'rgba(217,160,43,.12)', borderBottom: '1px solid var(--warn)',
    color: 'var(--warn)', fontSize: 11, padding: '4px 12px',
    letterSpacing: '.04em', flexShrink: 0,
  },
}
