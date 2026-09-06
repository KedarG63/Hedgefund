import { useQuery } from '@tanstack/react-query'
import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import { fetchFrame, num, str } from '../lib/api'
import { PanelMessage } from '../lib/panel'
import { contextKey, useContextStore } from '../store/context'

/**
 * OHLC + volume for the context symbol, with event markers.
 *
 * Canvas, not SVG: a multi-year daily series is thousands of bars and Plotly
 * visibly struggles where this does not. The markers are the point of drawing
 * them together -- an announcement or a bulk deal sitting ON the bar it moved
 * is the connection a separate events table cannot make.
 */
export function FocusChart() {
  const symbol = useContextStore((s) => s.symbol)
  const asOf = useContextStore((s) => s.asOf)
  const key = contextKey({ symbol, asOf })

  const price = useQuery({
    queryKey: ['price', ...key],
    queryFn: ({ signal }) => fetchFrame(`/instrument/${symbol}/price`, { as_of: asOf }, signal),
    enabled: Boolean(symbol),
  })

  const events = useQuery({
    queryKey: ['events', ...key],
    queryFn: ({ signal }) =>
      fetchFrame(`/instrument/${symbol}/events`, { as_of: asOf, limit: 200 }, signal),
    enabled: Boolean(symbol),
  })

  const host = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!host.current) return
    const c = createChart(host.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#a8bbd6',
        fontFamily: "'JetBrains Mono', Consolas, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30,48,80,.5)' },
        horzLines: { color: 'rgba(30,48,80,.5)' },
      },
      rightPriceScale: { borderColor: '#1e3050' },
      timeScale: { borderColor: '#1e3050', rightOffset: 4 },
      crosshair: { mode: 0 },
      autoSize: true,
    })
    chart.current = c
    return () => {
      c.remove()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    const c = chart.current
    const rows = price.data?.rows
    if (!c || !rows?.length) return

    const candles = c.addSeries(CandlestickSeries, {
      upColor: '#2a78d6', downColor: '#e34948',
      wickUpColor: '#2a78d6', wickDownColor: '#e34948',
      borderVisible: false,
    })
    const volume = c.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
    })
    c.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })

    const bars = rows
      .map((r) => ({
        time: toTime(str(r.trade_date)),
        open: num(r.open) ?? 0,
        high: num(r.high) ?? 0,
        low: num(r.low) ?? 0,
        close: num(r.close) ?? 0,
        volume: num(r.volume) ?? 0,
      }))
      .filter((b) => b.time !== null)
      .sort((a, b) => (a.time as number) - (b.time as number)) as Array<{
        time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number
      }>

    candles.setData(bars)
    volume.setData(
      bars.map((b) => ({
        time: b.time,
        value: b.volume,
        color: b.close >= b.open ? 'rgba(42,120,214,.35)' : 'rgba(227,73,72,.35)',
      })),
    )
    c.timeScale().fitContent()

    return () => {
      c.removeSeries(candles)
      c.removeSeries(volume)
    }
  }, [price.data])

  const eventCount = events.data?.rows.length ?? 0
  const barCount = price.data?.rows.length ?? 0

  // The chart host is ALWAYS mounted, and status is drawn over it. Early-
  // returning a loading message instead would unmount the ref target, so the
  // mount effect above would run against a null node and never create a chart
  // -- and being a [] effect, it would never get a second chance once the data
  // arrived. Rendering the container unconditionally is what makes the
  // lifecycle correct, not just tidier.
  const overlay = price.error
    ? <PanelMessage tone="bad">{(price.error as Error).message}</PanelMessage>
    : price.isLoading
      ? <PanelMessage>loading {symbol}…</PanelMessage>
      : barCount === 0
        ? <PanelMessage>No price history for {symbol}{asOf ? ` as of ${asOf}` : ''}.</PanelMessage>
        : null

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 10, padding: '5px 10px',
        borderBottom: '1px solid var(--line-soft)', fontSize: 12, flexShrink: 0,
      }}>
        <b className="mono" style={{ color: 'var(--accent)' }}>{symbol}</b>
        {barCount > 0 && (
          <span className="mono" style={{ color: 'var(--ink-3)' }}>{barCount} sessions</span>
        )}
        {eventCount > 0 && (
          <span className="mono" style={{ color: 'var(--ink-3)' }}>{eventCount} events</span>
        )}
        {asOf && <span className="mono" style={{ color: 'var(--warn)' }}>as of {asOf}</span>}
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <div ref={host} style={{ position: 'absolute', inset: 0 }} />
        {overlay && (
          <div style={{ position: 'absolute', inset: 0, background: 'var(--panel)' }}>
            {overlay}
          </div>
        )}
      </div>
    </div>
  )
}

/** NSE trade dates arrive as ISO strings; the chart wants epoch seconds UTC. */
function toTime(iso: string): UTCTimestamp | null {
  if (!iso) return null
  const ms = Date.parse(iso.length > 10 ? iso : `${iso}T00:00:00Z`)
  return Number.isNaN(ms) ? null : ((ms / 1000) as UTCTimestamp)
}
