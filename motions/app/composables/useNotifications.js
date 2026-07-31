// #region ALD 25/05/2026 - Notification center bell.
// State persist qua localStorage + useState (cross-page). Push từ applySnapshot khi
// job transition done/error/cancel. Bell badge hiện unread count. Click bell → panel
// list 50 noti gần nhất, click 1 noti → mark read + navigate đến workflow run nếu có.
//
// Schema noti:
//   { id, kind: 'done'|'error'|'cancel', title, body, ts, jobId?, workflowId?, runId?, read }
const STORAGE_KEY = 'noti:items'
const MAX_ITEMS = 50

function loadFromStorage() {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function saveToStorage(items) {
  if (typeof localStorage === 'undefined') return
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(items)) } catch { /* quota */ }
}

export function useNotifications() {
  const items = useState('noti.items', () => loadFromStorage())
  const open = useState('noti.open', () => false)

  const unreadCount = computed(() => items.value.filter((n) => !n.read).length)

  function push(noti) {
    const entry = {
      id: `noti-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      ts: Date.now(),
      read: false,
      ...noti,
    }
    items.value = [entry, ...items.value].slice(0, MAX_ITEMS)
    saveToStorage(items.value)
    // Browser notification nếu user grant permission + tab ẩn
    if (typeof window !== 'undefined' && document?.visibilityState === 'hidden'
        && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      try { new Notification(entry.title || 'Motions', { body: entry.body || '', tag: entry.id }) } catch { /* ignore */ }
    }
  }

  function markRead(id) {
    const it = items.value.find((n) => n.id === id)
    if (it && !it.read) {
      it.read = true
      items.value = [...items.value]
      saveToStorage(items.value)
    }
  }

  function markAllRead() {
    let changed = false
    for (const n of items.value) if (!n.read) { n.read = true; changed = true }
    if (changed) {
      items.value = [...items.value]
      saveToStorage(items.value)
    }
  }

  function clear() {
    items.value = []
    saveToStorage(items.value)
  }

  function remove(id) {
    items.value = items.value.filter((n) => n.id !== id)
    saveToStorage(items.value)
  }

  function requestBrowserPermission() {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      try { Notification.requestPermission() } catch { /* ignore */ }
    }
  }

  return { items, open, unreadCount, push, markRead, markAllRead, clear, remove, requestBrowserPermission }
}
// #endregion
