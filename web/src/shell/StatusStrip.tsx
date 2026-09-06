import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'

import { fetchFrame, fetchHealth, num, str } from '../lib/api'
import { useContextStore } from '../store/context'

/**
 * The always-visible bar: clocks, pipeline health, macro, and the as-of state.
 *
 * The health dot reads /api/ops/quality, which is served from a snapshot
 * refreshed every 10 minutes off the request path -- so this polls freely
 * without ever blocking a panel. When the snapshot is cold the API says so
 * (x-qd-computing) and the dot renders grey: "not measured yet" is a real
 * state and pretending it is green would be worse than saying nothing.
 */

const IST = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false,
})
const ET = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
})

/**
 * Wall clocks, ticking every 30s.
 *
 * A minute is the right resolution here and a second-ticking clock in the
 * corner is motion carrying no information -- on a screen meant to be watched
 * for hours, anything that moves should mean something.
 */
function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000)
    return () => window.clearInterval(id)
  }, [])
  return { ist: IST.format(now), et: ET.format(now) }
}

interface Health {
  tone: 'ok' | 'warn' | 'bad' | 'unknown'
  label: string
  title: string
}

function useHealth(): Health {
  const { data: live } = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval: 30_000,
  })

  const { data: quality } = useQuery({
    queryKey: ['quality'],
    queryFn: ({ signal }) => fetchFrame('/ops/quality', {}, signal),
    refetchInterval: 60_000,
  })

  if (!live) return { tone: 'bad', label: 'API DOWN', title: 'No response from the API.' }
  if (!quality) return { tone: 'unknown', label: 'checking', title: 'Loading quality snapshot.' }

  if (quality.meta.computing === '1') {
    return { tone: 'unknown', label: 'measuring', title: 'Quality snapshot is still computing.' }
  }
  if (quality.meta.error) {
    return { tone: 'bad', label: 'monitor failed', title: quality.meta.error }
  }

  const rows = quality.rows
  const count = (s: string) => rows.filter((r) => str(r.status) === s).length
  const stale = count('STALE')
  const collapsed = count('COLLAPSED')
  const age = num(quality.meta['age-seconds'])
  const ageLabel = age === null ? 'unknown age' : `${Math.round(age / 60)}m old`

  if (collapsed) {
    return { tone: 'bad', label: `${collapsed} collapsed`, title: `Row count collapsed (${ageLabel}).` }
  }
  if (stale) {
    return { tone: 'warn', label: `${stale} stale`, title: `${stale} datasets stale (${ageLabel}).` }
  }
  return { tone: 'ok', label: `${rows.length} datasets`, title: `All fresh (${ageLabel}).` }
}

const TONE: Record<Health['tone'], string> = {
  ok: 'var(--bullish)',
  warn: 'var(--warn)',
  bad: 'var(--bearish)',
  unknown: 'var(--ink-3)',
}

export function StatusStrip() {
  const { ist, et } = useClock()
  const health = useHealth()
  const asOf = useContextStore((s) => s.asOf)
  const symbol = useContextStore((s) => s.symbol)

  const { data: macro } = useQuery({
    queryKey: ['macro', asOf ?? 'now'],
    queryFn: ({ signal }) => fetchFrame('/macro/snapshot', { as_of: asOf }, signal),
    refetchInterval: 120_000,
  })

  const policy = macro?.rows.filter((r) => str(r.group) === 'policy') ?? []
  const repo = policy.find((r) => str(r.label).includes('Repo'))
  const chokepoints = macro?.rows.filter((r) => str(r.group) === 'chokepoint') ?? []

  return (
    <div style={S.strip}>
      <span style={S.item} className="mono" title="Focus symbol">
        <b style={{ color: 'var(--accent)' }}>{symbol}</b>
      </span>
      <Divider />
      <span style={S.item} className="mono" title="India Standard Time">IST {ist}</span>
      <span style={S.item} className="mono" title="US Eastern">ET {et}</span>
      <Divider />
      <span style={S.item} title={health.title}>
        <span style={{ ...S.dot, background: TONE[health.tone] }} />
        {/* The dot is never the only carrier -- the label states it in text. */}
        <span className="mono">{health.label}</span>
      </span>
      <Divider />
      {repo && (
        <span style={S.item} className="mono" title="RBI policy repo rate">
          repo {num(repo.value)?.toFixed(2)}%
        </span>
      )}
      {chokepoints.length > 0 && (
        <span style={{ ...S.item, color: 'var(--warn)' }} className="mono"
              title={chokepoints.map((c) => str(c.label)).join('\n')}>
          {chokepoints.length} chokepoint{chokepoints.length > 1 ? 's' : ''} stressed
        </span>
      )}
      <Divider />
      <FeedState />
      <span style={{ flex: 1 }} />
      <span style={S.item} className="mono"
            title={asOf ? 'Historical view' : 'Everything known now'}>
        {asOf ? (
          <span style={{ color: 'var(--warn)' }}>as of {asOf}</span>
        ) : (
          <span style={{ color: 'var(--ink-3)' }}>live</span>
        )}
      </span>
    </div>
  )
}

const Divider = () => <span style={S.divider} />

/**
 * Tick-feed state.
 *
 * Three facts that fail independently and must not be collapsed into one dot:
 * whether Redis is reachable, whether the broker stream is publishing, and how
 * long since the last tick. A healthy Redis with no ticks looks exactly like a
 * quiet market unless the age is shown -- and outside market hours "no ticks"
 * is correct, not broken, so this says `closed` rather than raising an alarm.
 */
function FeedState() {
  const { data } = useQuery({
    queryKey: ['live-status'],
    queryFn: async ({ signal }) => {
      const r = await fetch('/api/live/status', { signal })
      if (!r.ok) throw new Error(String(r.status))
      return (await r.json()) as {
        redis_reachable: boolean; feed_connected: boolean
        ticks_seen: number; last_tick_age_seconds: number | null
      }
    },
    // 5s, not the 15s the other strip items use. This endpoint touches no
    // warehouse data, and a slower poll left the strip reading "feed idle"
    // while prices were visibly flashing two panels away -- contradictory
    // information on screen costs more trust than a poll costs anything.
    refetchInterval: 5_000,
  })

  if (!data) return null

  const age = data.last_tick_age_seconds
  const flowing = age !== null && age < 60
  const tone = !data.redis_reachable ? 'var(--bearish)'
    : flowing ? 'var(--bullish)' : 'var(--ink-3)'
  const label = !data.redis_reachable ? 'no redis'
    : flowing ? `ticks ${data.ticks_seen}`
    : age === null ? 'feed idle' : `quiet ${Math.round(age / 60)}m`

  return (
    <span style={S.item} title={
      `Redis ${data.redis_reachable ? 'reachable' : 'unreachable'} · ` +
      `stream ${data.feed_connected ? 'subscribed' : 'not subscribed'} · ` +
      `${data.ticks_seen} ticks seen` +
      (age === null ? ' · no tick yet this session' : ` · last ${age}s ago`)
    }>
      <span style={{ ...S.dot, background: tone }} />
      <span className="mono">{label}</span>
    </span>
  )
}

const S: Record<string, CSSProperties> = {
  strip: {
    height: 'var(--strip-h)',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '0 10px',
    background: 'var(--panel)',
    borderBottom: '1px solid var(--line)',
    fontSize: 12,
    flexShrink: 0,
  },
  item: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '0 6px', whiteSpace: 'nowrap' },
  divider: { width: 1, height: 14, background: 'var(--line)', margin: '0 2px' },
  dot: { width: 7, height: 7, borderRadius: '50%', display: 'inline-block' },
}
