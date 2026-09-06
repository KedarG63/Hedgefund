import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const REPO_ROOT = resolve(__dirname, '..')

/**
 * Read TERMINAL_TOKEN from the repo's .env.
 *
 * Deliberately parsed here rather than exposed to the browser. The dev proxy
 * below attaches the header on the way through, so the token stays in the Node
 * process and the page itself holds no credential -- a token in client JS is a
 * token in the browser's devtools, its cache, and any screenshot of them.
 *
 * Same minimal format core/config.py accepts: KEY=value, # comments, optional
 * surrounding quotes.
 */
function readEnvToken(): string | undefined {
  if (process.env.TERMINAL_TOKEN) return process.env.TERMINAL_TOKEN
  try {
    for (const line of readFileSync(resolve(REPO_ROOT, '.env'), 'utf8').split(/\r?\n/)) {
      const t = line.trim()
      if (!t || t.startsWith('#')) continue
      const i = t.indexOf('=')
      if (i < 0) continue
      if (t.slice(0, i).trim() !== 'TERMINAL_TOKEN') continue
      let v = t.slice(i + 1).trim()
      if (v.length >= 2 && v[0] === v[v.length - 1] && (v[0] === '"' || v[0] === "'")) {
        v = v.slice(1, -1)
      }
      return v || undefined
    }
  } catch {
    /* no .env -- the banner below says so */
  }
  return undefined
}

const TOKEN = readEnvToken()
const API = process.env.TERMINAL_API_URL ?? 'http://127.0.0.1:8787'

if (!TOKEN) {
  console.warn(
    '\n[terminal] TERMINAL_TOKEN not found in .env -- every /api call will 401.\n' +
      '           python -c "import secrets; print(secrets.token_urlsafe(32))"\n',
  )
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
    strictPort: true,
    proxy: {
      '/api': {
        target: API,
        changeOrigin: false,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            if (TOKEN) proxyReq.setHeader('authorization', `Bearer ${TOKEN}`)
          })
        },
      },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
