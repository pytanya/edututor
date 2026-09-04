// api — REST + SSE-клиент к бэкенду Adaptive Tutor.
const BASE = import.meta.env.VITE_API_BASE || ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d) => d.msg).join('; ')
    } catch {
      /* не-JSON */
    }
    throw new Error(detail)
  }
  return res.json()
}

export function parseSSEChunk(buffer, onEvent) {
  buffer = (buffer || '') + ''
  const frames = buffer.split('\n\n')
  const rest = frames.pop() || ''
  for (const frame of frames) {
    const eventLine = frame.split('\n').find((l) => l.startsWith('event:'))
    const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
    if (!dataLine) continue
    const event = eventLine ? eventLine.slice(6).trim() : 'message'
    let data = {}
    try {
      data = JSON.parse(dataLine.slice(5).trim())
    } catch {
      continue
    }
    onEvent({ event, data })
  }
  return rest
}

export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function requestBlob(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.blob()
}

export const api = {
  chat: (body) => request('/chat', { method: 'POST', body: JSON.stringify(body) }),

  chatStream(body, onEvent) {
    const controller = new AbortController()
    const run = async () => {
      const res = await fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer = parseSSEChunk(buffer + decoder.decode(value, { stream: true }), onEvent)
      }
      parseSSEChunk(buffer, onEvent)
    }
    run().catch((err) => onEvent({ event: 'error', data: { message: err.message } }))
    return () => controller.abort()
  },

  history: (sessionId) => request(`/chat/history/${encodeURIComponent(sessionId)}`),
  removeHistory: (sessionId) => fetch(`${BASE}/chat/history/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),
  student: (studentId) => request(`/student/${encodeURIComponent(studentId)}`),
  studentSessions: (studentId) => request(`/student/${encodeURIComponent(studentId)}/sessions`),
  profile: (studentId, body) =>
    request(`/student/${encodeURIComponent(studentId)}/profile`, { method: 'POST', body: JSON.stringify(body) }),
  getReview: (studentId) => request(`/student/${encodeURIComponent(studentId)}/review`),
  getKnowledgeGraph: (studentId, subject = '') =>
    request(`/student/${encodeURIComponent(studentId)}/knowledge-graph${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`),
  getRecommendations: (studentId, { current_topic = '', subject = '', limit = 5 } = {}) =>
    request(`/student/${encodeURIComponent(studentId)}/recommendations?current_topic=${encodeURIComponent(current_topic)}&subject=${encodeURIComponent(subject)}&limit=${limit}`),
  getGraph: (studentId, subject = '', grade = '') =>
    request(`/student/${encodeURIComponent(studentId)}/graph?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`),
  getGraphRelated: (studentId, nodeId) =>
    request(`/student/${encodeURIComponent(studentId)}/graph/${encodeURIComponent(nodeId)}/related`),
  getGraphWiki: (studentId, nodeId) =>
    request(`/student/${encodeURIComponent(studentId)}/graph/${encodeURIComponent(nodeId)}/wiki`),
  wiki: (studentId, subject = '') =>
    request(`/student/${encodeURIComponent(studentId)}/wiki${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`),
  wikiArticle: (studentId, subject, topic) =>
    request(`/student/${encodeURIComponent(studentId)}/wiki/${encodeURIComponent(subject)}/${encodeURIComponent(topic)}`),
  enrichWiki: (studentId, subject, topic) =>
    request(`/student/${encodeURIComponent(studentId)}/wiki/enrich`, { method: 'POST', body: JSON.stringify({ subject, topic }) }),
  deleteWiki: (studentId, subject, topic) =>
    fetch(`${BASE}/student/${encodeURIComponent(studentId)}/wiki/${encodeURIComponent(subject)}/${encodeURIComponent(topic)}`, { method: 'DELETE' }),
  exportCsv: async (studentId) => {
    const blob = await requestBlob(`/student/${encodeURIComponent(studentId)}/export/csv`)
    downloadBlob(`${studentId}_session_log.csv`, blob)
  },
  exportSummary: async (studentId) => {
    const blob = await requestBlob(`/student/${encodeURIComponent(studentId)}/export/summary.csv`)
    downloadBlob(`${studentId}_summary.csv`, blob)
  },
  exportOkf: (studentId, subject, grade = '') =>
    request(`/student/${encodeURIComponent(studentId)}/export/okf?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`),
  knowledge: () => request('/knowledge'),
  provision: (body) => request('/knowledge/provision', { method: 'POST', body: JSON.stringify(body) }),
}

export default api
