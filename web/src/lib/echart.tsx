import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'

/**
 * A resizing ECharts host.
 *
 * Canvas, not SVG: a correlation heatmap over the watchlist is thousands of
 * cells and a treemap over the scanned universe is thousands of rects, which
 * is where SVG stops being viable. ResizeObserver rather than a window
 * listener because these live in dockview panels that resize independently of
 * the window.
 */
export function EChart({ option, onSelect }: {
  option: echarts.EChartsOption
  onSelect?: (params: { name?: string; data?: unknown }) => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const handler = useRef(onSelect)
  handler.current = onSelect

  useEffect(() => {
    if (!host.current) return
    const c = echarts.init(host.current, undefined, { renderer: 'canvas' })
    chart.current = c
    c.on('click', (p) => handler.current?.(p as { name?: string; data?: unknown }))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(host.current)
    return () => {
      ro.disconnect()
      c.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    // notMerge: series identity changes when the context symbol does, and a
    // merged update would leave the previous symbol's marks on the canvas.
    chart.current?.setOption(option, true)
  }, [option])

  return <div ref={host} style={{ position: 'absolute', inset: 0 }} />
}

/** The project's diverging pair, as an ECharts colour scale. */
export const DIVERGING = ['#e34948', '#0b1626', '#2a78d6']

export const BASE_TEXT = {
  color: '#a8bbd6',
  fontFamily: "'JetBrains Mono', Consolas, monospace",
  fontSize: 10,
}
