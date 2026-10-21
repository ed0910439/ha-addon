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
