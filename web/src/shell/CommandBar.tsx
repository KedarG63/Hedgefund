import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'

import { fetchFrame, str } from '../lib/api'
import { useContextStore } from '../store/context'

/**
 * Keyboard-first navigation: "/" from anywhere, type, Enter.
 *
 * A terminal is operated, not browsed. The whole point of this over a
 * selectbox is that the hands never leave the keyboard, so every affordance
 * here is a key: "/" focuses, Esc clears and blurs, arrows move, Enter commits.
 *
 * Commands share the input with symbols rather than living in a separate menu,
 * because "RELIANCE" and "asof 2026-06-30" are the same gesture -- change what
 * the screen is showing.
 */

interface Suggestion {
  kind: 'symbol' | 'command'
  value: string
  label: string
  hint: string
  run: () => void
}

const HELP = [
  { cmd: 'asof <date>', hint: 'roll the terminal back to a knowledge date' },
  { cmd: 'asof clear', hint: 'return to everything known now' },
]

export function CommandBar() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const setSymbol = useContextStore((s) => s.setSymbol)
  const setAsOf = useContextStore((s) => s.setAsOf)
  const asOf = useContextStore((s) => s.asOf)

  // The universe is the fuzzy index. It only contains symbols that actually
  // have price history in this warehouse -- a command bar offering a symbol
  // with no data is worse than one that omits it.
  const { data: universe } = useQuery({
    queryKey: ['universe', asOf ?? 'now'],
    queryFn: ({ signal }) => fetchFrame('/universe', { as_of: asOf }, signal),
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
      if (e.key === '/' && !typing) {
        e.preventDefault()
        setOpen(true)
        window.setTimeout(() => inputRef.current?.focus(), 0)
      } else if (e.key === 'Escape') {
        setOpen(false)
        setQ('')
        inputRef.current?.blur()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const suggestions = useMemo<Suggestion[]>(() => {
    const term = q.trim()
    if (!term) return []

    const lower = term.toLowerCase()

    if (lower.startsWith('asof')) {
      const arg = term.slice(4).trim()
      if (arg === 'clear' || arg === 'now') {
        return [{
          kind: 'command', value: 'asof clear', label: 'asof clear',
          hint: 'show everything known now',
          run: () => setAsOf(null),
        }]
      }
      const valid = /^\d{4}-\d{2}-\d{2}$/.test(arg)
      return [{
        kind: 'command', value: `asof ${arg}`,
        label: `asof ${arg || '<YYYY-MM-DD>'}`,
        hint: valid ? 'roll the whole terminal back to this date'
                    : 'needs a date like 2026-06-30, or "asof clear"',
        run: () => { if (valid) setAsOf(arg) },
      }]
    }

    const rows = universe?.rows ?? []
    const upper = term.toUpperCase()
    const scored = rows
      .map((r) => ({ symbol: str(r.symbol), name: str(r.name), industry: str(r.industry) }))
      .filter((r) => r.symbol.includes(upper) || r.name.toUpperCase().includes(upper))
      // Prefix beats substring, then shortest -- typing "REL" should surface
      // RELIANCE, not a longer name that merely contains the letters.
      .sort((a, b) => {
        const ap = a.symbol.startsWith(upper) ? 0 : 1
        const bp = b.symbol.startsWith(upper) ? 0 : 1
        return ap - bp || a.symbol.length - b.symbol.length || a.symbol.localeCompare(b.symbol)
      })
      .slice(0, 8)

    return scored.map((r) => ({
      kind: 'symbol' as const,
      value: r.symbol,
      label: r.symbol,
      hint: r.industry ? `${r.name} · ${r.industry}` : r.name,
      run: () => setSymbol(r.symbol),
    }))
  }, [q, universe, setSymbol, setAsOf])

  useEffect(() => setCursor(0), [q])

  const commit = (s: Suggestion | undefined) => {
    if (!s) return
    s.run()
    setOpen(false)
    setQ('')
    inputRef.current?.blur()
  }

  return (
    <div style={S.bar}>
      <span style={S.prompt} className="mono">&gt;</span>
      <input
        ref={inputRef}
        style={S.input}
        className="mono"
        value={q}
        placeholder={open ? 'symbol, or "asof 2026-06-30"' : 'press / to search'}
        onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, suggestions.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
          else if (e.key === 'Enter') { e.preventDefault(); commit(suggestions[cursor]) }
        }}
        aria-label="Command bar"
      />
      {open && (
        <div style={S.menu}>
          {suggestions.length === 0 && (
            <div style={S.empty}>
              {q.trim()
                ? 'no match'
                : HELP.map((h) => (
                    <div key={h.cmd} style={S.helpRow}>
                      <span className="mono" style={{ color: 'var(--accent)' }}>{h.cmd}</span>
                      <span style={{ color: 'var(--ink-3)' }}>{h.hint}</span>
                    </div>
                  ))}
            </div>
          )}
          {suggestions.map((s, i) => (
            <button
              key={s.kind + s.value}
              style={{ ...S.row, ...(i === cursor ? S.rowActive : null) }}
              onMouseEnter={() => setCursor(i)}
              onMouseDown={(e) => { e.preventDefault(); commit(s) }}
            >
              <span className="mono" style={{ color: 'var(--accent)', minWidth: 110 }}>{s.label}</span>
              <span style={{ color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {s.hint}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  bar: { position: 'relative', display: 'flex', alignItems: 'center', gap: 8, flex: 1, maxWidth: 520 },
  prompt: { color: 'var(--accent)', opacity: 0.8 },
  input: {
    flex: 1, background: 'var(--panel-2)', border: '1px solid var(--line)',
    borderRadius: 4, padding: '5px 9px', fontSize: 13, outline: 'none',
  },
  menu: {
    position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 4, zIndex: 50,
    background: 'var(--panel)', border: '1px solid var(--accent-line)', borderRadius: 6,
    boxShadow: '0 8px 24px rgba(0,0,0,.5)', overflow: 'hidden',
  },
  row: {
    display: 'flex', gap: 10, alignItems: 'baseline', width: '100%',
    padding: '6px 10px', textAlign: 'left', fontSize: 12,
  },
  rowActive: { background: 'var(--accent-soft)' },
  empty: { padding: '8px 10px', color: 'var(--ink-3)', fontSize: 12 },
  helpRow: { display: 'flex', gap: 10, padding: '2px 0' },
}
