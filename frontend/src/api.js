export const TOKEN_KEY = 'sfs_token'
export const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')

export const api = async (path, options = {}) => {
  const token = localStorage.getItem(TOKEN_KEY)
  const isFormData = options.body instanceof FormData
  const response = await fetch(`${API_BASE}/api${path}`, {
    ...options,
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Something went wrong')
  return data
}

export const apiFile = async (path) => {
  const token = localStorage.getItem(TOKEN_KEY)
  const response = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || 'Could not load this file')
  }
  return URL.createObjectURL(await response.blob())
}
