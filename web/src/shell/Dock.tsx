import { DockviewReact, type DockviewApi, type DockviewReadyEvent } from 'dockview'
import { useCallback, useRef } from 'react'

import { FocusChart } from '../panels/FocusChart'
import { Watchlist } from '../panels/Watchlist'

/**
 * The tiled workspace. This is the tabs -> control-panel fix.
 *
 * Panels are draggable, resizable and dockable, and the arrangement is saved,
 * so the terminal opens the way it was left. That persistence is what makes it
 * a workstation rather than a page: an analyst arranges the screen once for how
 * they work, not once per session.
 */

const LAYOUT_KEY = 'qd.layout.v1'

const components = {
  watchlist: () => <Watchlist />,
  chart: () => <FocusChart />,
}

export function Dock() {
  const api = useRef<DockviewApi | null>(null)

  const save = useCallback(() => {
    try {
      if (api.current) localStorage.setItem(LAYOUT_KEY, JSON.stringify(api.current.toJSON()))
    } catch {
      /* private mode / quota -- a lost layout is not worth an error dialog */
    }
  }, [])

  const onReady = useCallback((event: DockviewReadyEvent) => {
    api.current = event.api

    const saved = (() => {
      try {
        const raw = localStorage.getItem(LAYOUT_KEY)
        return raw ? JSON.parse(raw) : null
      } catch {
        return null
      }
    })()

    if (saved) {
      try {
        event.api.fromJSON(saved)
        // A saved layout from an older build can deserialise to nothing, which
        // would leave a blank terminal with no way back short of clearing
        // storage. Fall through to the default rather than trusting it.
        if (event.api.panels.length > 0) {
          event.api.onDidLayoutChange(save)
          return
        }
      } catch {
        /* fall through to the default arrangement */
      }
    }

    event.api.addPanel({ id: 'watchlist', component: 'watchlist', title: 'Watchlist' })
    event.api.addPanel({
      id: 'chart',
      component: 'chart',
      title: 'Chart',
      position: { referencePanel: 'watchlist', direction: 'right' },
    })
    event.api.onDidLayoutChange(save)
  }, [save])

  return (
    <div style={{ flex: 1, minHeight: 0 }}>
      <DockviewReact
        components={components}
        onReady={onReady}
        className="dockview-theme-abyss"
      />
    </div>
  )
}

export function resetLayout() {
  try {
    localStorage.removeItem(LAYOUT_KEY)
  } catch {
    /* nothing to do */
  }
  window.location.reload()
}
