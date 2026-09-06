import { tableFromIPC } from 'apache-arrow'

/**
 * Client for the terminal API.
 *
 * Arrow IPC is the default on the wire (see api/responses.py): DuckDB hands
 * back Arrow, pyarrow writes IPC, and this decodes straight into row objects
 * with no JSON parse and no float round-tripping. `fmt=json` stays available
 * for debugging with curl but the app does not use it.
 *
 * No credential lives here on purpose. In dev, vite.config.ts proxies /api and
 * attaches the bearer token on the way through, so the browser holds nothing:
 * a token in client JS is a token in devtools, in the bfcache, and in any
 * screenshot of either.
 */

export type Row = Record<string, unknown>

export interface Frame {
  rows: Row[]
  /** x-qd-* response headers -- freshness and provenance travel with the data. */
  meta: Record<string, string>
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`)
  }
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

export async function fetchFrame(
  path: string,
  params: Record<string, string | number | undefined | null> = {},
  signal?: AbortSignal,
): Promise<Frame> {
  const res = await fetch(`/api${path}${qs(params)}`, { signal })

  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* non-JSON error body; the status is the useful part */
    }
    throw new ApiError(res.status, detail)
  }

  const meta: Record<string, string> = {}
  res.headers.forEach((v, k) => {
    if (k.startsWith('x-qd-')) meta[k.slice('x-qd-'.length)] = v
  })

  const table = tableFromIPC(new Uint8Array(await res.arrayBuffer()))
  // toArray() yields Arrow row proxies; spread into plain objects so consumers
  // (AG Grid, chart adapters) get ordinary values rather than vectors.
  const rows = table.toArray().map((r) => ({ ...r.toJSON() }) as Row)
  return { rows, meta }
}

/** Health is the one unauthenticated route, and it returns JSON, not Arrow. */
export async function fetchHealth(signal?: AbortSignal) {
  const res = await fetch('/api/ops/health', { signal })
  if (!res.ok) throw new ApiError(res.status, res.statusText)
  return (await res.json()) as { status: string; views: number }
}

export const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'bigint' ? Number(v) : Number(v)
  return Number.isFinite(n) ? n : null
}

export const str = (v: unknown): string => (v === null || v === undefined ? '' : String(v))
