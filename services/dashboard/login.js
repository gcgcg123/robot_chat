(function () {
  const landing = document.getElementById('landing-screen');
  const loginPanel = document.getElementById('landing-login-panel');
  const loginButton = document.getElementById('landing-login-btn');
  const closeButton = document.getElementById('landing-login-close');
  const form = document.getElementById('login-form');
  if (!landing || !loginPanel || !loginButton || !closeButton || !form) return;

  const error = document.getElementById('login-error');
  const shell = document.querySelector('.app-shell');
  const csrf = () => window.__iotCsrf || '';
  const unlockKey = 'iot-dashboard-unlocked';

  document.querySelectorAll('.marquee-track').forEach((track) => {
    const content = track.innerHTML;
    track.innerHTML = `<div class="marquee-group">${content}</div><div class="marquee-group" aria-hidden="true">${content}</div>`;
  });

  async function session() {
    const response = await fetch('/api/auth/session', { cache: 'no-store' });
    if (!response.ok) throw new Error('not_authenticated');
    const data = await response.json();
    window.__iotCsrf = data.csrf_token;
    return data;
  }

  function showDashboard() {
    sessionStorage.setItem(unlockKey, '1');
    landing.hidden = true;
    loginPanel.hidden = true;
    if (shell) shell.hidden = false;
    document.body.dataset.authenticated = 'true';
    window.dispatchEvent(new CustomEvent('iot:authenticated', { detail: { csrf: csrf() } }));
  }

  function showLanding() {
    landing.hidden = false;
    loginPanel.hidden = true;
    if (shell) shell.hidden = true;
    document.body.dataset.authenticated = 'false';
  }

  async function initialize() {
    if (sessionStorage.getItem(unlockKey) === '1') {
      try {
        await session();
        showDashboard();
      } catch {
        sessionStorage.removeItem(unlockKey);
        showLanding();
      }
    } else {
      showLanding();
    }
  }

  function openLogin() {
    error.textContent = '';
    loginPanel.hidden = false;
    window.setTimeout(() => document.getElementById('login-actor')?.focus(), 0);
  }

  function closeLogin() {
    error.textContent = '';
    loginPanel.hidden = true;
  }

  loginButton.addEventListener('click', openLogin);
  closeButton.addEventListener('click', closeLogin);
  loginPanel.addEventListener('click', (event) => {
    if (event.target === loginPanel) closeLogin();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !loginPanel.hidden) closeLogin();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || '登录失败');
      }
      await session();
      showDashboard();
    } catch (err) {
      error.textContent = err.message === 'admin_auth_not_configured'
        ? '尚未设定管理员密码，请先执行首次设定。'
        : '账号或密码不正确。';
    }
  });

  document.getElementById('logout-btn')?.addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf() } });
    sessionStorage.removeItem(unlockKey);
    window.location.reload();
  });

  initialize();
})();
