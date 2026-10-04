import axios from 'axios'

const DEFAULT_BACKEND_PORT = import.meta.env.VITE_API_PORT || '8107'

export function resolveApiBaseUrl() {
  const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL
  if (configuredBaseUrl) {
    return configuredBaseUrl.replace(/\/+$/, '')
  }

  if (import.meta.env.PROD) {
    return ''
  }

  return `http://127.0.0.1:${DEFAULT_BACKEND_PORT}`
}

const api = axios.create({
  baseURL: resolveApiBaseUrl(),
  timeout: 15000,
})

export const AUTH_TOKEN_KEY = 'citadel_access_token'

api.interceptors.request.use((config) => {
  const token = typeof window !== 'undefined' ? window.sessionStorage.getItem(AUTH_TOKEN_KEY) : null
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use((response) => response, (error) => {
  const requestUrl = error.config?.url || ''
  if (error.response?.status === 401 && !requestUrl.startsWith('/auth/login') && !requestUrl.startsWith('/auth/register')) {
    if (typeof window !== 'undefined') {
      window.sessionStorage.removeItem(AUTH_TOKEN_KEY)
      window.dispatchEvent(new Event('citadel:session-expired'))
    }
  }
  return Promise.reject(error)
})

export const registerUser = async (payload) => (await api.post('/auth/register', payload)).data
export const loginUser = async (payload) => (await api.post('/auth/login', payload)).data
export const getCurrentUser = async () => (await api.get('/auth/me')).data
export const getStoredToken = () => (typeof window === 'undefined' ? null : window.sessionStorage.getItem(AUTH_TOKEN_KEY))
export const storeToken = (token) => window.sessionStorage.setItem(AUTH_TOKEN_KEY, token)
export const clearToken = () => window.sessionStorage.removeItem(AUTH_TOKEN_KEY)
export const getAuthToken = getStoredToken

function parseFilenameFromDisposition(headerValue) {
  if (!headerValue) {
    return 'citadel-exposure-report.pdf'
  }

  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1])
  }

  const plainMatch = headerValue.match(/filename="?([^"]+)"?/i)
  return plainMatch?.[1] || 'citadel-exposure-report.pdf'
}

export const analyzeText = async (text) => {
  const response = await api.post('/analyze', { text })
  return response.data
}

export const getAlerts = async () => {
  const response = await api.get('/alerts')
  return response.data
}

export const getStats = async () => {
  const response = await api.get('/stats')
  return response.data
}

export const getHealth = async () => {
  const response = await api.get('/health', { timeout: 5000 })
  return response.data
}

export const collectIntel = async (query, persist = true, demo = false) => {
  const response = await api.post('/collect-intel', { query, persist, demo }, { timeout: 60000 })
  return response.data
}

export const getMonitoringStats = async () => {
  const response = await api.get('/monitoring/stats')
  return response.data
}

export const getCases = async ({ limit = 200, status, priority, search } = {}) => {
  const response = await api.get('/cases', {
    params: {
      limit,
      ...(status ? { status } : {}),
      ...(priority ? { priority } : {}),
      ...(search ? { search } : {}),
    },
  })
  return response.data
}

export const getCase = async (caseId) => {
  const response = await api.get(`/cases/${caseId}`)
  return response.data
}

export const updateCase = async (caseId, payload) => {
  const response = await api.patch(`/cases/${caseId}`, payload)
  return response.data
}

export const getWatchlists = async () => {
  const response = await api.get('/watchlists')
  return response.data
}

export const createWatchlist = async (payload) => {
  const response = await api.post('/watchlists', payload)
  return response.data
}

export const updateWatchlist = async (watchlistId, payload) => {
  const response = await api.put(`/watchlists/${watchlistId}`, payload)
  return response.data
}

export const deleteWatchlist = async (watchlistId) => {
  const response = await api.delete(`/watchlists/${watchlistId}`)
  return response.data
}

export const runWatchlistNow = async (watchlistId) => {
  const response = await api.post(`/watchlists/${watchlistId}/run`)
  return response.data
}

export const getAuditEvents = async (limit = 100) => {
  const response = await api.get('/audit-events', { params: { limit } })
  return response.data
}

export const exportCasesSnapshot = async () => {
  const response = await api.get('/cases/export')
  return response.data
}

export const exportPdfReport = async ({
  startDate,
  endDate,
  severity = [],
  category = [],
  orgId,
  onDownloadProgress,
} = {}) => {
  const response = await api.get('/export/report/pdf', {
    params: {
      ...(startDate ? { start_date: startDate } : {}),
      ...(endDate ? { end_date: endDate } : {}),
      ...(orgId ? { org_id: orgId } : {}),
      ...(severity.length ? { severity } : {}),
      ...(category.length ? { category } : {}),
    },
    responseType: 'blob',
    timeout: 60000,
    onDownloadProgress,
  })

  return {
    blob: response.data,
    filename: parseFilenameFromDisposition(response.headers['content-disposition']),
    reportId: response.headers['x-citadel-report-id'] || '',
    verificationUrl: response.headers['x-citadel-verification-url'] || '',
    signatureStatus: response.headers['x-citadel-signature-status'] || 'unsigned',
  }
}

export const previewCyberCellReport = async (payload) => {
  const response = await api.post('/api/v1/report/cybercell/preview', payload, { timeout: 60000 })
  return response.data
}

export const getCyberCellReportingStatus = async () => {
  const response = await api.get('/api/v1/report/cybercell/status', { timeout: 10000 })
  return response.data
}

export const sendCyberCellReport = async (payload) => {
  const response = await api.post('/api/v1/report/cybercell/send', payload, { timeout: 60000 })
  return response.data
}

export const getVerifiedReport = async (reportId) => {
  const response = await api.get(`/api/v1/verify/report/${reportId}`, { timeout: 15000 })
  return response.data
}

export const verifyReportUpload = async (reportId, file) => {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post(`/api/v1/verify/report/${reportId}/upload`, formData, {
    timeout: 60000,
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

export default api
