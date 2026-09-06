import { PanelHeader, PanelState, S, fixed, num, signed, str, usePanelQuery } from '../lib/panel'

/**
 * The macro strip in full: policy corridor, gold premium, chokepoint stress.
 *
 * The API returns these long ({group, label, value, unit, as_of}) because they
 * are heterogeneous by nature -- a repo rate, a percentage premium and a
 * z-score do not belong in one numeric column with one format. Grouping is
 * done here so each block can carry its own unit and its own as-of date, which
 * differ per indicator: RBI publishes CPI, CRR and WACR on completely
 * different cadences, and a single "as of" for the panel would be a lie about
 * most of the rows.
 */

const GROUPS: Record<string, string> = {
  policy: 'RBI policy corridor',
  gold: 'Gold',
  chokepoint: 'Chokepoint stress',
}

export function Macro() {
  const q = usePanelQuery(['macro'], '/macro/snapshot')

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>macro snapshot</span>
      </PanelHeader>
      <div style={S.scroll}>
        <PanelState query={q} empty="No macro series ingested yet.">
          {(frame) => {
            const groups = Array.from(new Set(frame.rows.map((r) => str(r.group))))
            return (
              <div style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 14 }}>
                {groups.map((g) => (
                  <div key={g}>
                    <div className="mono" style={{
                      fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase',
                      color: 'var(--ink-3)', marginBottom: 6,
                    }}>
                      {GROUPS[g] ?? g}
                    </div>
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 8,
                    }}>
                      {frame.rows.filter((r) => str(r.group) === g).map((r, i) => {
                        const v = num(r.value)
                        const isZ = str(r.unit) === 'z'
                        return (
                          <div key={i} style={{
                            border: '1px solid var(--line)', borderRadius: 5,
                            padding: '8px 10px', background: 'var(--panel)',
                          }}>
                            <div style={{
                              fontSize: 11, color: 'var(--ink-2)', marginBottom: 3,
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                            }} title={str(r.label)}>
                              {str(r.label)}
                            </div>
                            <div className="mono" style={{
                              fontSize: 17,
                              color: isZ
                                ? (v !== null && Math.abs(v) >= 2 ? 'var(--warn)' : 'var(--ink)')
                                : 'var(--ink)',
                            }}>
                              {isZ ? signed(v) : fixed(v, 2)}
                              <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 3 }}>
                                {str(r.unit) === 'z' ? 'σ' : str(r.unit)}
                              </span>
                            </div>
                            {/* Per-indicator as-of: these move on different cadences. */}
                            <div className="mono" style={{
                              fontSize: 10, color: 'var(--ink-3)', marginTop: 2,
                            }}>
                              {str(r.as_of).slice(0, 10)}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )
          }}
        </PanelState>
      </div>
    </div>
  )
}
