import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { CSSProperties } from 'react'

import { PanelHeader, PanelMessage, S, str } from '../lib/panel'
import { useContextStore } from '../store/context'

/**
 * Where a number came from.
 *
 * The chain runs view -> upstream views -> the archived bytes a server actually
 * returned, with the fetch timestamp and sha256 from the sidecar. Rule 1 paid
 * for this: because every fetch is written unchanged before anything parses
 * it, a figure on screen can be walked back to its origin instead of being
 * taken on trust.
 *
 * Nothing here is inferred at request time. The edges are declared in
 * core/provenance.py, read out of the connectors' own save_raw()/write_table()
 * literals -- a wrong provenance link would point a reader at bytes that did
 * not produce their number, which is worse than admitting the link is unknown.
 */

interface TraceNode {
  view: string
  depth: number
  computed: boolean
  approximate: boolean
  repeated: boolean
  truncated: boolean
  vintages: { file: string; bytes: number }[]
  raw: RawFile[]
  upstream: TraceNode[]
}

interface RawFile {
  raw_dataset: string
  path: string
  bytes: number
  fetched_at_utc: string | null
  sha256: string | null
  source_url?: string | null
}

export function Provenance() {
  const provView = useContextStore((s) => s.provenanceView)
  const setProvView = useContextStore((s) => s.setProvenanceView)
  const [preview, setPreview] = useState<RawFile | null>(null)

  const views = useQuery({
    queryKey: ['ops-views'],
    queryFn: async ({ signal }) => {
      const { fetchFrame } = await import('../lib/api')
      return fetchFrame('/ops/views', {}, signal)
    },
    staleTime: 5 * 60_000,
  })

  const trace = useQuery({
    queryKey: ['provenance', provView],
    queryFn: async ({ signal }) => {
      const r = await fetch(`/api/provenance/${provView}`, { signal })
      if (!r.ok) throw new Error(`${r.status}`)
      return (await r.json()) as {
        view: string; natural_key: string[] | null
        trace: TraceNode; reaches_raw_bytes: boolean; note: string | null
      }
    },
    enabled: Boolean(provView),
  })

  const bytes = useQuery({
    queryKey: ['raw-preview', preview?.path],
    queryFn: async ({ signal }) => {
      const r = await fetch(
        `/api/provenance/raw/preview?path=${encodeURIComponent(preview!.path)}&max_bytes=2048`,
        { signal },
      )
      if (!r.ok) throw new Error(`${r.status}`)
      return (await r.json()) as {
        path: string; bytes: number; binary: boolean; truncated: boolean; preview: string
      }
    },
    enabled: Boolean(preview),
  })

  const options = (views.data?.rows ?? [])
    .filter((r) => r.point_in_time)
    .map((r) => str(r.view))

  return (
    <div style={S.panel}>
      <PanelHeader>
        <span className="mono" style={{ color: 'var(--ink-3)' }}>provenance</span>
        <select
          className="mono"
          value={provView}
          onChange={(e) => { setProvView(e.target.value); setPreview(null) }}
          style={SS.select}
        >
          {options.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        {trace.data?.natural_key && (
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
            key: {trace.data.natural_key.join(', ')}
          </span>
        )}
      </PanelHeader>

      <div style={S.scroll}>
        {trace.isLoading && <PanelMessage>tracing…</PanelMessage>}
        {trace.error && <PanelMessage tone="bad">{(trace.error as Error).message}</PanelMessage>}
        {trace.data && (
          <div style={{ padding: 10 }}>
            {trace.data.note && (
              // Said out loud rather than shown as an empty list: "we have not
              // established this link" and "there are no bytes" are different
              // claims, and conflating them would misrepresent the archive.
              <div style={SS.note}>{trace.data.note}</div>
            )}
            <Node node={trace.data.trace} onPick={setPreview} picked={preview} />
          </div>
        )}
      </div>

      {preview && (
        <div style={SS.bytesPane}>
          <div style={SS.bytesHead}>
            <span className="mono" style={{ color: 'var(--accent)' }}>{preview.path}</span>
            <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 10 }}>
              {preview.bytes.toLocaleString()} bytes
              {preview.fetched_at_utc && ` · fetched ${preview.fetched_at_utc.slice(0, 19)}Z`}
            </span>
            <span style={{ flex: 1 }} />
            <button className="mono" style={SS.close} onClick={() => setPreview(null)}>close</button>
          </div>
          {preview.sha256 && (
            <div className="mono" style={SS.sha}>sha256 {preview.sha256}</div>
          )}
          <pre style={SS.bytes}>
            {bytes.isLoading ? 'reading…'
              : bytes.error ? String((bytes.error as Error).message)
              : (bytes.data?.binary
                  ? `[binary — first ${(bytes.data.preview.length / 2) | 0} bytes as hex]\n\n${wrap(bytes.data.preview)}`
                  : bytes.data?.preview) + (bytes.data?.truncated ? '\n\n… truncated' : '')}
          </pre>
        </div>
      )}
    </div>
  )
}

function Node({ node, onPick, picked }: {
  node: TraceNode
  onPick: (f: RawFile) => void
  picked: RawFile | null
}) {
  if (node.repeated) {
    return (
      <div style={{ ...SS.node, borderLeftColor: 'var(--line)' }}>
        <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 11 }}>
          {node.view} — shown above
        </span>
      </div>
    )
  }

  return (
    <div style={{ ...SS.node, borderLeftColor: node.computed ? 'var(--accent-line)' : 'var(--bullish)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="mono" style={{ color: 'var(--ink)', fontSize: 12 }}>{node.view}</span>
        <span className="mono" style={SS.tag}>
          {node.computed ? 'computed' : 'collected'}
        </span>
        {node.approximate && (
          <span className="mono" style={{ ...SS.tag, color: 'var(--warn)', borderColor: 'var(--warn)' }}
                title="This module writes several tables from a shared set of inputs, so the upstream list is the module's, not this table's. Over-inclusive, never under.">
            approx
          </span>
        )}
        {node.vintages.length > 0 && (
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
            {node.vintages.length} vintage{node.vintages.length > 1 ? 's' : ''}
          </span>
        )}
      </div>

      {node.raw.map((f) => (
        <button key={f.path} onClick={() => onPick(f)}
                style={{ ...SS.file, ...(picked?.path === f.path ? SS.fileActive : null) }}>
          <span className="mono" style={{ color: 'var(--accent)', fontSize: 11 }}>{f.path}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
            {f.bytes.toLocaleString()} b
            {f.fetched_at_utc ? ` · ${f.fetched_at_utc.slice(0, 10)}` : ''}
          </span>
        </button>
      ))}

      {node.upstream.map((u) => (
        <Node key={u.view + u.depth} node={u} onPick={onPick} picked={picked} />
      ))}
    </div>
  )
}

const wrap = (hex: string) => (hex.match(/.{1,64}/g) ?? []).join('\n')

const SS: Record<string, CSSProperties> = {
  select: {
    background: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 3,
    padding: '2px 6px', fontSize: 11, color: 'var(--ink)', maxWidth: 260,
  },
  note: {
    border: '1px solid var(--warn)', background: 'rgba(217,160,43,.10)',
    color: 'var(--warn)', borderRadius: 4, padding: '7px 10px',
    fontSize: 11, marginBottom: 10,
  },
  node: {
    borderLeft: '2px solid', paddingLeft: 10, marginLeft: 2,
    marginBottom: 8, paddingBottom: 2,
  },
  tag: {
    fontSize: 9, letterSpacing: '.08em', textTransform: 'uppercase',
    color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 3,
    padding: '0 4px',
  },
  file: {
    display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 1,
    width: '100%', textAlign: 'left', padding: '4px 7px', marginTop: 4,
    border: '1px solid var(--line-soft)', borderRadius: 4,
  },
  fileActive: { borderColor: 'var(--accent-line)', background: 'var(--accent-soft)' },
  bytesPane: {
    borderTop: '1px solid var(--accent-line)', background: 'var(--panel-2)',
    maxHeight: '45%', display: 'flex', flexDirection: 'column', flexShrink: 0,
  },
  bytesHead: {
    display: 'flex', alignItems: 'baseline', gap: 10, padding: '6px 10px',
    borderBottom: '1px solid var(--line-soft)', fontSize: 11,
  },
  sha: { padding: '3px 10px', fontSize: 10, color: 'var(--ink-3)', wordBreak: 'break-all' },
  close: { fontSize: 10, color: 'var(--ink-3)', border: '1px solid var(--line)',
           borderRadius: 3, padding: '1px 6px' },
  bytes: {
    margin: 0, padding: '8px 10px', overflow: 'auto', fontSize: 11,
    fontFamily: 'var(--mono)', color: 'var(--ink-2)', whiteSpace: 'pre-wrap',
    wordBreak: 'break-all', flex: 1,
  },
}
