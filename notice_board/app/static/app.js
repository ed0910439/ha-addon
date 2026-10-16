const appBasePath = (document.querySelector('meta[name="app-base-path"]')?.content || '').replace(/\/$/, '')
const appUrl = (path) => `${appBasePath}${path.startsWith('/') ? path : '/' + path}`

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
