import {
  PanelHeader, PanelState, S, diverging, fixed, num, signed, str, useSymbolQuery,
} from '../lib/panel'

/**
 * Every derived signal for the focus symbol, one row each.
 *
 * Long form from the API, so a new derived_* table appears here without a
 * frontend change. Each row shows the value, the context column that makes it
 * interpretable (r-squared behind a beta, realized vol behind a low-vol score),
 * and the computation date -- because a signal without its as-of date is a
 * number with no shelf life.
 */
export function SignalStack() {
  const q = useSymbolQuery('signals', (s) => `/instrument/${s}/signals`)

  return (
    <div style={S.panel}>
      <PanelHeader>
        <b className="mono" style={{ color: 'var(--accent)' }}>{q.symbol}</b>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>signals</span>
      </PanelHeader>
      <div style={S.scroll}>
        <PanelState query={q} empty={`No computed signals for ${q.symbol}.`}>
          {(frame) => (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <tbody>
                {frame.rows.map((r, i) => {
                  const v = num(r.value)
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid var(--line-soft)' }}>
                      <td style={{ padding: '7px 10px', color: 'var(--ink-2)' }}>
                        {str(r.signal)}
                      </td>
                      <td className="mono" style={{
                        padding: '7px 10px', textAlign: 'right', width: 90,
                        color: diverging(v), fontWeight: 500,
                      }}>
                        {/* sign in text, direction in colour -- never colour alone */}
                        {signed(v)}
                      </td>
                      <td className="mono" style={{
                        padding: '7px 10px', color: 'var(--ink-3)', fontSize: 11, width: 150,
                      }} title={`${str(r.context_name)} = ${str(r.context_value)}`}>
                        {str(r.context_name)} {fixed(r.context_value, 3) || str(r.context_value)}
                      </td>
                      <td className="mono" style={{
                        padding: '7px 10px', color: 'var(--ink-3)', fontSize: 11, width: 92,
                      }}>
                        {str(r.as_of_date)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </PanelState>
      </div>
    </div>
  )
}
