(function () {
  const screen = document.getElementById('login-screen');
  const form = document.getElementById('login-form');
  if (!screen || !form) return;
  const error = document.getElementById('login-error');
  const shell = document.querySelector('.app-shell');
  const csrf = () => window.__iotCsrf || '';
  async function session() {
    const response = await fetch('/api/auth/session', { cache: 'no-store' });
    if (!response.ok) throw new Error('not_authenticated');
    const data = await response.json();
    window.__iotCsrf = data.csrf_token;
    return data;
  }
  async function initialize() {
    try {
      await session();
      screen.hidden = true;
      if (shell) shell.hidden = false;
      document.body.dataset.authenticated = 'true';
      window.dispatchEvent(new CustomEvent('iot:authenticated', { detail: { csrf: csrf() } }));
    } catch {
      screen.hidden = false;
      if (shell) shell.hidden = true;
    }
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || '登入失敗');
      }
      await initialize();
      window.location.reload();
    } catch (err) {
      error.textContent = err.message === 'admin_auth_not_configured' ? '尚未設定管理員密碼，請先執行首次設定。' : '帳號或密碼不正確。';
    }
  });
  document.getElementById('logout-btn')?.addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf() } });
    window.location.reload();
  });
  initialize();
})();
