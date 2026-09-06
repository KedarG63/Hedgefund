import type { ReactNode } from 'react'

import { Correlation } from './Correlation'
import { Credit } from './Credit'
import { EventTape } from './EventTape'
import { FactorMap } from './FactorMap'
import { FocusChart } from './FocusChart'
import { Flows } from './Flows'
import { Fundamentals } from './Fundamentals'
import { Macro } from './Macro'
import { Monsoon } from './Monsoon'
import { OptionChain } from './OptionChain'
import { SignalStack } from './SignalStack'
import { SupplyChain } from './SupplyChain'
import { Watchlist } from './Watchlist'

/**
 * The panel catalogue.
 *
 * `reads` is not decoration. It names the DuckDB views behind each panel, so a
 * reader can answer "where did this number come from" without leaving the
 * screen, and it is the anchor the provenance drill will hang off. A panel that
 * cannot say what it reads has no business showing a number to a trader.
 *
 * `scoped` marks panels that follow the context symbol -- used to label them in
 * the launcher so it is obvious which panels re-scope on a click and which are
 * market-wide.
 */
export interface PanelDef {
  id: string
  title: string
  render: () => ReactNode
  reads: string[]
  scoped: boolean
}

export const PANELS: PanelDef[] = [
  {
    id: 'watchlist', title: 'Watchlist', render: () => <Watchlist />, scoped: false,
    reads: ['derived_factor_model', 'derived_digest'],
  },
  {
    id: 'chart', title: 'Chart', render: () => <FocusChart />, scoped: true,
    reads: ['nse_bhavcopy', 'nse_bhavcopy_delivery'],
  },
  {
    id: 'signals', title: 'Signals', render: () => <SignalStack />, scoped: true,
    reads: ['derived_capm_beta', 'derived_momentum_zscore', 'derived_low_vol_factor',
            'derived_size_factor', 'derived_variance_ratio', 'derived_factor_model',
            'derived_multivariate_outliers'],
  },
  {
    id: 'events', title: 'Events', render: () => <EventTape />, scoped: true,
    reads: ['nse_announcements', 'nse_insider_trading', 'nse_bulk_deals', 'nse_block_deals'],
  },
  {
    id: 'fundamentals', title: 'Fundamentals', render: () => <Fundamentals />, scoped: true,
    reads: ['nse_xbrl_facts'],
  },
  {
    id: 'options', title: 'Options', render: () => <OptionChain />, scoped: true,
    reads: ['nse_optchain', 'derived_option_greeks'],
  },
  {
    id: 'factormap', title: 'Factor map', render: () => <FactorMap />, scoped: false,
    reads: ['derived_factor_model'],
  },
  {
    id: 'correlation', title: 'Correlation', render: () => <Correlation />, scoped: false,
    reads: ['derived_correlation'],
  },
  {
    id: 'macro', title: 'Macro', render: () => <Macro />, scoped: false,
    reads: ['rbi_key_indicators', 'derived_india_gold_premium', 'analytics_chokepoint_zscore'],
  },
  {
    id: 'flows', title: 'Flows', render: () => <Flows />, scoped: false,
    reads: ['nse_participant_oi', 'nse_fii_dii', 'derived_fii_source_divergence'],
  },
  {
    id: 'supply', title: 'Supply chain', render: () => <SupplyChain />, scoped: false,
    reads: ['analytics_current_regime', 'analytics_reroute_balance', 'analytics_regime_breaks'],
  },
  {
    id: 'monsoon', title: 'Monsoon', render: () => <Monsoon />, scoped: false,
    reads: ['analytics_monsoon_departure', 'analytics_monsoon_progress', 'analytics_enso_state'],
  },
  {
    id: 'credit', title: 'Credit', render: () => <Credit />, scoped: false,
    reads: ['crisil_rating_actions', 'icra_rating_actions', 'care_rating_actions'],
  },
]

export const PANEL_BY_ID = Object.fromEntries(PANELS.map((p) => [p.id, p]))

/** dockview's component map, derived so a new panel is one registry entry. */
export const DOCK_COMPONENTS = Object.fromEntries(
  PANELS.map((p) => [p.id, () => <>{p.render()}</>]),
)

/** The arrangement a fresh terminal opens with. */
export const DEFAULT_LAYOUT: Array<{ id: string; position?: Record<string, unknown> }> = [
  { id: 'watchlist' },
  { id: 'chart', position: { referencePanel: 'watchlist', direction: 'right' } },
  { id: 'signals', position: { referencePanel: 'chart', direction: 'right' } },
  { id: 'events', position: { referencePanel: 'signals', direction: 'below' } },
  { id: 'macro', position: { referencePanel: 'chart', direction: 'below' } },
]
