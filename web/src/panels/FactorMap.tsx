import type { EChartsOption } from 'echarts'
import { useMemo } from 'react'

import { BASE_TEXT, EChart } from '../lib/echart'
import { PanelHeader, PanelState, S, num, str, usePanelQuery } from '../lib/panel'
import { useContextStore } from '../store/context'

/**
 * The scanned universe as a treemap, sized by conviction and coloured by
 * direction.
 *
 * Area is |composite_score| and colour is its sign, so the eye finds the
 * strongest signals first regardless of which way they point -- which is the
 * question a scan answers ("what is extreme right now"), not "what is up".
 * Clicking a tile sets the context symbol, same as the watchlist.
 */
export function FactorMap() {
  const setSymbol = useContextStore((s) => s.setSymbol)
  const q = usePanelQuery(['watchlist-map'], '/watchlist', { limit: 400 })

  const option = useMemo<EChartsOption>(() => {
    const rows = q.data?.rows ?? []
    return {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (params) => {
          const p = params as { name?: string; data?: { score?: number } }
          const score = p.data?.score ?? 0
          return `<b>${p.name ?? ''}</b><br/>composite ${score >= 0 ? '+' : ''}${score.toFixed(2)}`
        },
      },
      series: [{
        type: 'treemap',
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        label: { show: true, ...BASE_TEXT, color: '#e6edf7', overflow: 'truncate' },
        itemStyle: { borderColor: '#050d1a', borderWidth: 1, gapWidth: 1 },
        data: rows
          .map((r) => {
            const score = num(r.composite_score) ?? 0
            return {
              name: str(r.symbol),
              value: Math.abs(score) || 0.001,
              score,
              itemStyle: { color: score >= 0 ? '#2a78d6' : '#e34948' },
            }
          })
          .filter((d) => d.name),
      }],
    }
  }, [q.data])

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>
          factor map — area = |composite|, blue = long, red = short
        </span>
      </PanelHeader>
      <div style={S.body}>
        <PanelState query={q} empty="No factor scores computed yet.">
          {() => <EChart option={option} onSelect={(p) => p.name && setSymbol(p.name)} />}
        </PanelState>
      </div>
    </div>
  )
}
