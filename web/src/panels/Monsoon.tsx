import { PanelHeader, PanelState, S, fixed, num, signed, str, usePanelQuery } from '../lib/panel'

/**
 * Rainfall departure from the IMD normal, by region, against the ENSO prior.
 *
 * `basis` is shown on every row on purpose. IMD publishes its normals in
 * arrears, so the current season is observed by CPC and rescaled onto IMD's
 * basis -- a `cpc_uncorrected` number deserves less weight than a corrected
 * one, and hiding that distinction would make the panel more confident than
 * the data.
 *
 * `coverage_pct` travels with the cumulative for the same reason: a total
 * built from 12 of 90 elapsed days is not a small total, it is an early one.
 */
export function Monsoon() {
  const q = usePanelQuery(['monsoon'], '/climate/monsoon')
  const enso = usePanelQuery(['enso'], '/climate/enso')
  const e = enso.data?.rows[0]

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>monsoon</span>
        {e && (
          <span className="mono" style={{ fontSize: 11 }}>
            ENSO{' '}
            <b style={{ color: str(e.phase).toLowerCase().includes('nino')
              ? 'var(--bearish)' : str(e.phase).toLowerCase().includes('nina')
                ? 'var(--bullish)' : 'var(--ink-2)' }}>
              {str(e.phase)}
            </b>{' '}
            <span style={{ color: 'var(--ink-3)' }}>
              {signed(e.anomaly_c, 2)}°C · {str(e.trajectory)}
            </span>
          </span>
        )}
      </PanelHeader>
      <div style={S.scroll}>
        <PanelState query={q}
                    empty="Needs the IMD climatology, then `run_daily.py --job analytics_monsoon`.">
          {(frame) => (
            <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {frame.rows.map((r, i) => {
                const dep = num(r.departure_pct)
                const uncorrected = str(r.basis) === 'cpc_uncorrected'
                // Bar is signed around a centre line: deficit left, surplus
                // right, so sign is readable from position as well as colour.
                const pct = Math.max(-100, Math.min(100, dep ?? 0))
                return (
                  <div key={i} style={{
                    border: '1px solid var(--line)', borderRadius: 5,
                    padding: '7px 10px', background: 'var(--panel)',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <b style={{ fontSize: 12 }}>{str(r.region)}</b>
                      <span className="mono" style={{
                        fontSize: 13,
                        color: dep === null ? 'var(--ink-3)'
                             : dep < -10 ? 'var(--bearish)'
                             : dep > 10 ? 'var(--bullish)' : 'var(--ink-2)',
                      }}>
                        {signed(dep, 1)}%
                      </span>
                      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                        {fixed(r.observed_mm, 0)} / {fixed(r.normal_mm, 0)} mm
                      </span>
                      <span style={{ flex: 1 }} />
                      <span className="mono" style={{
                        fontSize: 10,
                        color: uncorrected ? 'var(--warn)' : 'var(--ink-3)',
                      }} title={uncorrected
                        ? 'Observed by CPC without the IMD bias correction applied.'
                        : 'Rescaled onto the IMD basis.'}>
                        {str(r.basis)}
                      </span>
                    </div>
                    <div style={{
                      position: 'relative', height: 6, marginTop: 6,
                      background: 'var(--panel-2)', borderRadius: 3,
                    }}>
                      <div style={{
                        position: 'absolute', left: '50%', top: -2, bottom: -2,
                        width: 1, background: 'var(--line)',
                      }} />
                      <div style={{
                        position: 'absolute', top: 0, bottom: 0, borderRadius: 3,
                        left: pct >= 0 ? '50%' : `${50 + pct / 2}%`,
                        width: `${Math.abs(pct) / 2}%`,
                        background: pct >= 0 ? 'var(--bullish)' : 'var(--bearish)',
                      }} />
                    </div>
                    <div className="mono" style={{
                      fontSize: 10, color: 'var(--ink-3)', marginTop: 4,
                    }}>
                      {fixed(r.coverage_pct, 0)}% of season observed
                      {' · '}{str(r.days_observed)} of {str(r.season_days_elapsed)} days
                    </div>
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
