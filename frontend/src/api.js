export async function api(url, opts = {}) {
  const defaultHeaders = opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }
  const { headers: customHeaders, ...restOpts } = opts
  const r = await fetch(url, { ...restOpts, headers: { ...defaultHeaders, ...customHeaders } })
  if (r.status === 401) {
    window.dispatchEvent(new Event('auth-expired'))
    throw new Error('Session expired')
  }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
