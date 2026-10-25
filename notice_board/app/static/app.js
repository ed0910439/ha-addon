const appBasePath = (document.querySelector('meta[name="app-base-path"]')?.content || '').replace(/\/$/, '')
const appUrl = (path) => `${appBasePath}${path.startsWith('/') ? path : '/' + path}`

const HASS_ROUTE_STORAGE_KEY = 'notice-board:hass-route'

function currentAppPath() {
  const pathname = window.location.pathname || '/'
  if (appBasePath && pathname.startsWith(appBasePath)) {
    const rest = pathname.slice(appBasePath.length)
    return rest.startsWith('/') ? rest : `/${rest}`
  }
  return pathname.startsWith('/') ? pathname : `/${pathname}`
}

function hassPanelPath(appPath) {
  const normalized = appPath && appPath !== '/' ? appPath : ''
  return `/app/notice_board${normalized}`
}

function enableHomeAssistantRouteSync() {
  if (window.parent === window) return

  let storedRoute = sessionStorage.getItem(HASS_ROUTE_STORAGE_KEY) || ''
  const localPath = currentAppPath()

  // If the iframe navigated internally (links, forms, redirects), keep the
  // Home Assistant panel URL in sync before subscribing for parent updates.
  if (storedRoute && storedRoute !== localPath) {
    storedRoute = localPath
    sessionStorage.setItem(HASS_ROUTE_STORAGE_KEY, storedRoute)
    window.parent.postMessage(
      { type: 'home-assistant/navigate', path: hassPanelPath(localPath), options: { replace: true } },
      '*',
    )
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return
    const data = event.data
    if (!data || data.type !== 'home-assistant/properties') return

    const routePath = data.route?.path
    if (typeof routePath !== 'string' || !routePath.startsWith('/')) return
    if (routePath !== '/' && !routePath.startsWith('/a/')) return

    if (routePath === storedRoute) return
    storedRoute = routePath
    sessionStorage.setItem(HASS_ROUTE_STORAGE_KEY, storedRoute)

    const target = appUrl(routePath)
    if (window.location.pathname !== target) {
      window.location.replace(target)
    }
  })

  window.parent.postMessage({ type: 'home-assistant/subscribe-properties' }, '*')
}

enableHomeAssistantRouteSync()

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.copy-btn');
  if (!btn) return;
  try {
    await navigator.clipboard.writeText(btn.dataset.copy || '');
    const original = btn.textContent;
    btn.textContent = '已複製';
    setTimeout(() => btn.textContent = original, 1400);
  } catch {
    window.prompt('複製這個連結：', btn.dataset.copy || '');
  }
});

document.addEventListener('submit', (e) => {
  const form = e.target;
  const message = form.dataset.confirm;
  if (message && !window.confirm(message)) e.preventDefault();
});


async function refreshUnreadBadge() {
  const badges = Array.from(document.querySelectorAll('[data-unread-badge]'))
  if (!badges.length) return
  try {
    const response = await fetch(appUrl('/api/unread-count'), { credentials: 'same-origin', cache: 'no-store' })
    if (!response.ok) return
    const data = await response.json()
    const count = Number(data.unread_count || 0)
    badges.forEach((badge) => {
      badge.hidden = count <= 0
      badge.title = `${count} 則未讀`
      badge.setAttribute('aria-label', count > 0 ? `${count} 則未讀公告` : '沒有未讀公告')
    })
  } catch {
    // Keep the server-rendered badge state when the background refresh fails.
  }
}

refreshUnreadBadge()
setInterval(refreshUnreadBadge, 30000)


function enableDashboardFilters() {
  const controls = Array.from(document.querySelectorAll('[data-dashboard-filter]'))
  const cards = Array.from(document.querySelectorAll('[data-announcement-status]'))
  const result = document.querySelector('[data-dashboard-result]')
  const clear = document.querySelector('[data-dashboard-clear]')
  const empty = document.querySelector('[data-dashboard-filter-empty]')
  if (!controls.length || !cards.length) return

  const labels = {
    all: '顯示全部公告',
    published: '顯示已發布公告',
    unread: '顯示尚未閱讀公告',
    pending: '顯示待發布更新',
  }

  function matchesFilter(card, filter) {
    if (filter === 'all') return true
    if (filter === 'published') return card.dataset.announcementStatus === 'published'
    if (filter === 'unread') return card.dataset.announcementUnread === 'true'
    if (filter === 'pending') return card.dataset.announcementPending === 'true'
    return true
  }

  function applyFilter(filter) {
    let visibleCount = 0
    controls.forEach((control) => {
      const active = control.dataset.dashboardFilter === filter
      control.classList.toggle('is-active', active)
      control.setAttribute('aria-pressed', active ? 'true' : 'false')
    })
    cards.forEach((card) => {
      const visible = matchesFilter(card, filter)
      card.hidden = !visible
      card.classList.toggle('is-filtered-out', !visible)
      if (visible) visibleCount += 1
    })
    if (result) result.textContent = `${labels[filter] || labels.all} · ${visibleCount} 則`
    if (clear) clear.hidden = filter === 'all'
    if (empty) empty.hidden = visibleCount !== 0
  }

  controls.forEach((control) => {
    control.addEventListener('click', () => applyFilter(control.dataset.dashboardFilter || 'all'))
  })
  clear?.addEventListener('click', () => applyFilter('all'))
  applyFilter('all')
}

enableDashboardFilters()

// Explicit, version-aware read receipts. A page GET alone must never mark content read.
function initNoticeReadTracking() {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || ''
  if (!csrf) return
  document.querySelectorAll('[data-read-container][data-can-read="true"]').forEach((root) => {
    const accordion = root.querySelector('[data-notice-accordion]')
    const panel = root.querySelector('[data-notice-content]')
    const detail = root.querySelector('[data-notice-detail-content]')
    const content = panel || detail
    const viewer = root.querySelector('[data-tiptap-viewer]')
    const confirmation = root.querySelector('[data-confirm-pdf-read]')
    const hint = root.querySelector('[data-read-hint]')
    if (!content || !viewer) return
    let submitting = false
    let confirmed = root.dataset.hasRead === 'true'

    const updateReadUI = (response) => {
      root.dataset.hasRead = 'true'
      if (hint) hint.textContent = '本版已讀'
      const badge = root.querySelector('[data-read-label]')
      if (badge) {
        badge.textContent = '本版已讀'
        badge.classList.remove('unread')
        badge.classList.add('read')
      }
      root.querySelectorAll('[data-read-count]').forEach((el) => { el.textContent = String(response.read_count) })
      const list = root.querySelector('[data-reader-list]')
      if (list && list.tagName === 'UL') {
        list.querySelector('[data-read-empty]')?.remove()
        const name = response.reader.username
        if (![...list.querySelectorAll('[data-reader-username]')].some((el) => el.dataset.readerUsername === name)) {
          const li = document.createElement('li')
          li.dataset.readerUsername = name
          const strong = document.createElement('strong')
          strong.textContent = response.reader.display_name
          const sub = document.createElement('span')
          sub.className = 'muted'
          sub.textContent = ' @' + name
          li.append(strong, sub)
          list.append(li)
        }
      }
      root.dataset.announcementUnread = 'false'
      root.classList.remove('is-unread')
      const unreadStat = document.querySelector('.summary-card-unread > strong')
      if (unreadStat) unreadStat.textContent = String(Math.max(0, Number(unreadStat.textContent || 0) - 1))
      refreshUnreadBadge()
    }
    const isAtBottom = () => {
      if (panel) {
        // Only the expanded card can qualify. No scrollbar means expansion is enough.
        if (!accordion?.open) return false
        return panel.scrollHeight <= panel.clientHeight + 3 ||
          panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 10
      }
      // A full-page announcement qualifies when the end of the content becomes visible.
      const rect = content.getBoundingClientRect()
      return rect.bottom <= window.innerHeight - 8 && rect.bottom > 0
    }
    const maybeMarkRead = async (pdfConfirmed = false) => {
      if (confirmed || submitting || document.visibilityState !== 'visible') return
      if (viewer.dataset.rendered !== 'true' || !isAtBottom()) return
      // Embedded PDF viewers have their own independent scrollbar. Never infer full
      // PDF reading from a parent scroll event; require an explicit acknowledgment.
      if (viewer.querySelector('iframe.pdf-frame') && !pdfConfirmed) {
        if (confirmation) confirmation.hidden = false
        if (hint) hint.textContent = '內嵌 PDF 無法自動辨識閱讀進度，閱讀完後請點選確認。'
        return
      }
      submitting = true
      try {
        const response = await fetch(appUrl('/api/announcements/' + encodeURIComponent(root.dataset.announcementId) + '/read'), {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ csrf_token: csrf, version: root.dataset.version }),
        })
        if (!response.ok) throw new Error('HTTP ' + response.status)
        confirmed = true
        if (confirmation) confirmation.hidden = true
        updateReadUI(await response.json())
      } catch {
        if (hint) hint.textContent = '已閱紀錄尚未成功儲存；請確認網路後再試一次。'
      } finally {
        submitting = false
      }
    }

    if (confirmed) {
      if (hint) hint.textContent = '本版已讀'
      return
    }
    accordion?.addEventListener('toggle', () => {
      if (accordion.open) window.requestAnimationFrame(() => maybeMarkRead())
    })
    panel?.addEventListener('scroll', () => maybeMarkRead(), { passive: true })
    if (detail) window.addEventListener('scroll', () => maybeMarkRead(), { passive: true })
    confirmation?.addEventListener('click', () => maybeMarkRead(true))
    viewer.addEventListener('noticeboard:viewer-ready', () => window.requestAnimationFrame(() => maybeMarkRead()))
    // Watch for viewport changes and rich-content hydration, not merely a click.
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => maybeMarkRead())
      observer.observe(content)
    }
    window.addEventListener('resize', () => maybeMarkRead(), { passive: true })
    window.requestAnimationFrame(() => maybeMarkRead())
  })
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initNoticeReadTracking)
else initNoticeReadTracking()
