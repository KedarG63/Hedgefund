import type { EChartsOption } from 'echarts'
import { useMemo } from 'react'

import { BASE_TEXT, DIVERGING, EChart } from '../lib/echart'
import { PanelHeader, PanelState, S, num, str, usePanelQuery } from '../lib/panel'
import { useContextStore } from '../store/context'

/**
 * Pairwise correlation across the focus symbol and the top of the watchlist.
 *
 * derived_correlation is 9M rows, so the symbol set is chosen here and filtered
 * in DuckDB -- never fetched and subset in the browser.
 *
 * A missing pair renders as a GAP, not as zero. Zero correlation and "we have
 * not computed this pair" are completely different claims, and painting the
 * second as the first is how a heatmap starts lying.
 */
export function Correlation() {
  const symbol = useContextStore((s) => s.symbol)

  // The focus symbol plus the strongest-conviction names, so the matrix always
  // includes what the rest of the screen is about.
  const wl = usePanelQuery(['watchlist-corr'], '/watchlist', { limit: 11 })
  const symbols = useMemo(() => {
    const top = (wl.data?.rows ?? []).map((r) => str(r.symbol)).filter(Boolean)
    return Array.from(new Set([symbol, ...top])).slice(0, 12)
  }, [wl.data, symbol])

  const q = usePanelQuery(['correlation', symbols.join(',')], '/correlation',
                          { symbols: symbols.join(',') })

  // Typed as EChartsOption so the literals ('category', 'heatmap') narrow
  // instead of widening to string, which is what ECharts' discriminated unions
  // need to accept them.
  const option = useMemo<EChartsOption>(() => {
    const rows = q.data?.rows ?? []
    const names = symbols
    const idx = new Map(names.map((n, i) => [n, i]))
    const cells: [number, number, number][] = []
    for (const r of rows) {
      const a = idx.get(str(r.symbol_a))
      const b = idx.get(str(r.symbol_b))
      const v = num(r.correlation)
      if (a === undefined || b === undefined || v === null) continue
      cells.push([a, b, v])
      if (a !== b) cells.push([b, a, v])
    }
    for (let i = 0; i < names.length; i++) cells.push([i, i, 1])

    return {
      backgroundColor: 'transparent',
      grid: { left: 88, right: 16, top: 12, bottom: 74 },
      tooltip: {
        formatter: (params) => {
          const cell = (params as { data?: [number, number, number] }).data
          if (!cell) return ''
          return `${names[cell[0]]} · ${names[cell[1]]}<br/><b>${cell[2].toFixed(3)}</b>`
        },
      },
      xAxis: {
        type: 'category', data: names, axisLabel: { ...BASE_TEXT, rotate: 60 },
        axisLine: { lineStyle: { color: '#1e3050' } }, splitArea: { show: false },
      },
      yAxis: {
        type: 'category', data: names, axisLabel: BASE_TEXT,
        axisLine: { lineStyle: { color: '#1e3050' } },
      },
      visualMap: {
        min: -1, max: 1, calculable: true, orient: 'horizontal',
        left: 'center', bottom: 4, itemHeight: 70,
        textStyle: BASE_TEXT, inRange: { color: DIVERGING },
      },
      series: [{
        type: 'heatmap', data: cells,
        itemStyle: { borderColor: '#050d1a', borderWidth: 1 },
        emphasis: { itemStyle: { borderColor: '#4d9eff', borderWidth: 2 } },
      }],
    }
  }, [q.data, symbols])

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>
          correlation — {symbols.length} names, blank = not computed
        </span>
      </PanelHeader>
      <div style={S.body}>
        <PanelState query={q}
                    empty="No stored correlations for these names yet — needs several weeks of daily bhavcopy history.">
          {() => <EChart option={option} />}
        </PanelState>
      </div>
    </div>
  )
}
