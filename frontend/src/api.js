let _reloading = false

export async function api(url, opts = {}) {
  const defaultHeaders = opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }
  const { headers: customHeaders, ...restOpts } = opts
  const r = await fetch(url, { ...restOpts, headers: { ...defaultHeaders, ...customHeaders } })
  if (r.status === 401) {
    // Reload once to force re-auth (guard against infinite loop)
    if (!_reloading) {
      _reloading = true
      window.location.reload()
    }
    throw new Error('Session expired')
  }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
