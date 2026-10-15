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
