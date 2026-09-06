import type { CSSProperties } from 'react'

import { useContextStore } from '../store/context'

/**
 * The knowledge-date control.
 *
 * Empty means "everything known now". Set it and every panel re-queries with
 * ?as_of=, which rolls back prices AND the derived signals computed from them,
 * because each derived_* table carries its own knowledge_date vintage.
 *
 * A back-dated screen that looks identical to a live one is an expensive
 * mistake, so historical mode is loud: the input turns amber and the shell
 * draws a banner. That redundancy is deliberate -- the same rule the palette
 * follows, that no state is signalled by colour alone.
 */
export function AsOfScrubber() {
  const asOf = useContextStore((s) => s.asOf)
  const setAsOf = useContextStore((s) => s.setAsOf)
  const historical = Boolean(asOf)

  return (
    <div style={S.wrap}>
      <label htmlFor="asof" style={S.label} className="mono">
        knowledge date
      </label>
      <input
        id="asof"
        type="date"
        className="mono"
        value={asOf ?? ''}
        max={new Date().toISOString().slice(0, 10)}
        onChange={(e) => setAsOf(e.target.value || null)}
        style={{ ...S.input, ...(historical ? S.inputHistorical : null) }}
        title="Show the warehouse as it was known on this date. Empty = now."
      />
      {historical && (
        <button style={S.clear} className="mono" onClick={() => setAsOf(null)}
                title="Return to everything known now">
          clear
        </button>
      )}
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  wrap: { display: 'flex', alignItems: 'center', gap: 8 },
  label: { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-3)' },
  input: {
    background: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 4,
    padding: '4px 8px', fontSize: 12, colorScheme: 'dark',
  },
  inputHistorical: { borderColor: 'var(--warn)', color: 'var(--warn)' },
  clear: {
    fontSize: 11, color: 'var(--ink-3)', border: '1px solid var(--line)',
    borderRadius: 4, padding: '3px 7px',
  },
}
