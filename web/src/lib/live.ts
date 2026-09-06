import { useEffect, useRef, useState } from 'react'

/**
 * The live tick stream.
 *
 * One WebSocket for the whole app, not one per panel: the server already
 * coalesces into ~100ms batches and fans out from a single Redis subscription,
 * and opening a socket per panel would undo that on the client side.
 *
 * State is held in a ref and mirrored into React on a timer rather than
 * setState-per-message. A busy open lands ~10 batches a second, and rendering
 * every one of them would spend the whole frame budget on reconciliation for
 * numbers a human cannot read that fast.
 */

export interface Tick {
  symbol: string
  ltp: number | null
  volume: number | null
  close_price: number | null
  ts: number | null
}

export type LiveState = 'connecting' | 'live' | 'stalled' | 'offline'

export interface Live {
  ticks: Record<string, Tick>
  state: LiveState
  /** Ticks received since the socket opened -- distinguishes "connected but
   *  silent" from "connected and flowing", which look identical otherwise. */
  received: number
}

const RENDER_MS = 250
const MAX_BACKOFF_MS = 15_000

export function useLiveTicks(symbols: string[]): Live {
  const [snapshot, setSnapshot] = useState<Live>({
    ticks: {}, state: 'connecting', received: 0,
  })
  const ticks = useRef<Record<string, Tick>>({})
  const received = useRef(0)
  const state = useRef<LiveState>('connecting')
  const key = symbols.join(',')

  useEffect(() => {
    if (!key) return
    let ws: WebSocket | null = null
    let closed = false
    let attempt = 0
    let retry: number | undefined

    const open = () => {
      if (closed) return
      state.current = 'connecting'
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${location.host}/ws/ticks?symbols=${encodeURIComponent(key)}`)

      ws.onopen = () => { attempt = 0 }

      ws.onmessage = (e) => {
        let msg: { type?: string; ticks?: Tick[]; feed_connected?: boolean }
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }
        if (msg.type === 'hello') {
          // Connected to US is not connected to the BROKER. Saying "live" when
          // the upstream feed is down would be the most misleading state on
          // the screen.
          state.current = msg.feed_connected ? 'live' : 'stalled'
          return
        }
        if (msg.type === 'ticks' || msg.type === 'snapshot') {
          state.current = 'live'
          for (const t of msg.ticks ?? []) {
            if (t.symbol) ticks.current[t.symbol] = t
          }
          received.current += (msg.ticks ?? []).length
        }
      }

      const reopen = () => {
        if (closed) return
        state.current = 'offline'
        // Exponential backoff, capped. A terminal left open overnight must not
        // hammer a down service thousands of times before morning.
        attempt += 1
        const wait = Math.min(500 * 2 ** (attempt - 1), MAX_BACKOFF_MS)
        retry = window.setTimeout(open, wait)
      }
      ws.onclose = reopen
      ws.onerror = () => ws?.close()
    }

    open()
    const timer = window.setInterval(() => {
      setSnapshot({ ticks: { ...ticks.current }, state: state.current, received: received.current })
    }, RENDER_MS)

    return () => {
      closed = true
      window.clearInterval(timer)
      if (retry) window.clearTimeout(retry)
      ws?.close()
    }
  }, [key])

  return snapshot
}

/** Percentage move against the previous close the feed itself reports. */
export function changePct(t: Tick | undefined): number | null {
  if (!t || t.ltp === null || !t.close_price) return null
  return ((t.ltp - t.close_price) / t.close_price) * 100
}
