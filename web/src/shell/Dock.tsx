import { DockviewReact, type DockviewApi, type DockviewReadyEvent } from 'dockview'
import { useCallback, useRef } from 'react'

import { DEFAULT_LAYOUT, DOCK_COMPONENTS } from '../panels/registry'

/**
 * The tiled workspace. This is the tabs -> control-panel fix.
 *
 * Panels are draggable, resizable and dockable, and the arrangement is saved,
 * so the terminal opens the way it was left. That persistence is what makes it
 * a workstation rather than a page: an analyst arranges the screen once for how
 * they work, not once per session.
 *
 * The component map and the default arrangement both come from
 * panels/registry.tsx, so adding a panel is one registry entry rather than
 * edits scattered across the shell.
 */

const LAYOUT_KEY = 'qd.layout.v2'

let dockApi: DockviewApi | null = null

export function openPanel(id: string, title: string) {
  if (!dockApi) return
  const existing = dockApi.getPanel(id)
  if (existing) {
    existing.api.setActive()
    return
  }
  dockApi.addPanel({ id, component: id, title })
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
    dockApi = event.api

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
        // A layout saved by an older build can deserialise to nothing, leaving
        // a blank terminal with no way back short of clearing storage. Fall
        // through to the default rather than trusting it.
        if (event.api.panels.length > 0) {
          event.api.onDidLayoutChange(save)
          return
        }
      } catch {
        /* fall through to the default arrangement */
      }
    }

    for (const { id, position } of DEFAULT_LAYOUT) {
      const def = DOCK_COMPONENTS[id]
      if (!def) continue
      event.api.addPanel({
        id,
        component: id,
        title: id.charAt(0).toUpperCase() + id.slice(1),
        ...(position ? { position: position as never } : {}),
      })
    }
    event.api.onDidLayoutChange(save)
  }, [save])

  return (
    <div style={{ flex: 1, minHeight: 0 }}>
      <DockviewReact
        components={DOCK_COMPONENTS}
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
