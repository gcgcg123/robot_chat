/* 设备接入页：ESP 开关、局域网地址、待接入绑定、设备表与固件管理。 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const csrf = () => window.__iotCsrf || '';
  const STATUS_LABEL = { online: '在线', stale: '心跳延迟', offline: '离线' };
  const FILTERS = {
    all: () => true,
    esp: (d) => String(d.transport || '').startsWith('esp'),
    sim: (d) => Number(d.is_simulator) === 1 || d.transport === 'simulator',
    online: (d) => d.status === 'online',
  };
  let filter = 'all';
  let devices = [];
  let users = [];
  let otaRequests = [];
  let currentSettings = {};

  function applyAdminProfile() {
    const name = (localStorage.getItem('iot-admin-nickname') || '').trim() || '管理员';
    const avatar = localStorage.getItem('iot-admin-avatar');
    const nameEl = $('topbar-username');
    const avatarEl = $('topbar-avatar');
    if (nameEl) nameEl.textContent = name;
    if (!avatarEl) return;
    if (avatar) {
      avatarEl.textContent = '';
      avatarEl.style.backgroundImage = `url("${avatar}")`;
      avatarEl.style.backgroundSize = 'cover';
      avatarEl.style.backgroundPosition = 'center';
      avatarEl.classList.add('has-image');
    } else {
      avatarEl.textContent = '管';
      avatarEl.style.backgroundImage = '';
      avatarEl.classList.remove('has-image');
    }
  }

  /* One banner serves two jobs: a *persistent* condition (codec missing, TTS
     mute, loopback-only address) and a *transient* acknowledgement ("已保存").
     Keeping them in separate slots matters: otherwise the acknowledgement is
     overwritten by the persistent warning one refresh later and the user never
     reads it. */
  let persistentAlert = { message: '', tone: 'ok' };
  let flashTimer = null;

  function showAlert(message, tone) {
    const box = $('esp-alert');
    if (!box) return;
    if (!message) { box.className = 'esp-alert'; box.textContent = ''; return; }
    box.className = `esp-alert show ${tone || 'ok'}`;
    box.textContent = message;
  }

  function applyPersistentAlert() {
    showAlert(persistentAlert.message, persistentAlert.tone);
  }

  function flashAlert(message, tone) {
    if (flashTimer) clearTimeout(flashTimer);
    showAlert(message, tone);
    flashTimer = setTimeout(applyPersistentAlert, 3000);
  }

  async function call(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== 'GET') headers['X-CSRF-Token'] = csrf();
    if (opts.body && !(opts.body instanceof FormData) && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts, { headers }));
    if (res.status === 401 || res.status === 403) {
      window.location.href = '/dashboard';
      throw new Error('unauthenticated');
    }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    return res.status === 204 ? null : res.json();
  }

  // ---------------------------------------------------------------- render

  function renderSettings(status) {
    const settings = status.settings || {};
    const opus = status.opus || {};
    currentSettings = settings;
    $('esp-enabled').checked = !!settings.enabled;
    $('esp-require-token').checked = !!settings.require_token;
    $('esp-ota-enabled').checked = !!settings.ota_enabled;
    $('esp-voiceprint-enabled').checked = !!settings.voiceprint_enabled;
    $('esp-tools-enabled').checked = !!settings.tools_enabled;
    $('esp-llm-stream').checked = !!settings.llm_stream;
    // Number/text inputs: never clobber what the operator is typing.
    const standby = $('esp-standby-seconds');
    if (standby) {
      const value = settings.standby_seconds ?? '';
      // dataset.last mirrors the server-confirmed value so a rejected save can
      // roll the input back instead of leaving bad text in the box.
      standby.dataset.last = String(value);
      if (document.activeElement !== standby) standby.value = value;
    }
    const notice = $('esp-standby-notice');
    if (notice) {
      const value = settings.standby_notice ?? '';
      notice.dataset.last = String(value);
      if (document.activeElement !== notice) notice.value = value;
    }
    for (const [id, key] of [
      ['esp-tts-max-chars', 'tts_max_chars'],
      ['esp-tts-min-chars', 'tts_min_chars'],
      ['esp-wake-word-hold', 'wake_word_hold_seconds'],
    ]) {
      const input = $(id);
      if (!input) continue;
      const value = settings[key] ?? '';
      input.dataset.last = String(value);
      if (document.activeElement !== input) input.value = value;
    }

    const tag = $('opus-tag');
    tag.textContent = opus.available ? 'Opus 可用' : 'Opus 不可用';
    tag.className = 'panel-tag';
    tag.style.background = opus.available ? '' : '#ffe4e6';
    tag.style.color = opus.available ? '' : '#be123c';
  }

  function renderAddresses(status) {
    const network = status.network || {};
    const addresses = Array.isArray(network.addresses) ? network.addresses : [];
    $('addr-ota').textContent = network.ota_url || '—';
    $('addr-ws').textContent = network.websocket_url || '—';
    $('addr-lan').textContent = addresses.length ? addresses.join(' , ') : '未检测到局域网地址';
    $('addr-path').textContent = (status.settings || {}).path || '—';
  }

  /* Ordered by how much each problem blocks a real device: a missing codec
     makes the transport useless, a mute TTS still "connects", and a
     loopback-only address is the classic "board never shows up" cause. */
  function renderAlerts(status) {
    const opus = status.opus || {};
    const tts = status.tts || {};
    const network = status.network || {};
    const enabled = !!(currentSettings && currentSettings.enabled);
    if (!opus.available) {
      persistentAlert = { message: `Opus 编解码不可用，设备无法收发语音：${opus.reason || '未知原因'}。请安装 pyogg，或把 opus.dll 放入 vendor/opus/。`, tone: 'bad' };
    } else if (tts.produces_audio === false) {
      persistentAlert = { message: '当前 TTS 为静音占位实现，设备能连上但不会有声音。请把 IOT_TTS_PROVIDER 设为 edge。', tone: 'warn' };
    } else if (enabled && network.reachable_locally === false) {
      persistentAlert = { message: '未检测到非回环局域网地址，设备将无法接入。请确认已连接局域网，或用 IOT_OTA_BASE_URL 指定地址。', tone: 'warn' };
    } else {
      persistentAlert = { message: '', tone: 'ok' };
    }
    applyPersistentAlert();
  }

  function renderStatusGrid(status) {
    const settings = status.settings || {};
    const network = status.network || {};
    const opus = status.opus || {};
    const port = (() => {
      try { return new URL(network.websocket_url || '').port || '—'; } catch { return '—'; }
    })();
    $('stat-esp').textContent = settings.enabled ? `监听中 :${port}` : '未启用';
    $('stat-ota').textContent = settings.ota_enabled ? `已启用 · ${network.firmware_count ?? 0} 个固件` : '未启用';
    $('stat-opus').textContent = opus.available ? '可用' : '不可用';
    $('stat-lan').textContent = (network.addresses || []).length ? network.addresses[0] : '无局域网地址';
  }

  function renderPending() {
    const bound = new Map(devices.map((d) => [d.device_id, d.bound_user_id]));
    const pending = devices.filter((d) => d.transport === 'esp_ota' && !bound.get(d.device_id));
    const lastRequest = new Map();
    otaRequests.forEach((row) => {
      const id = row.device_id;
      if (!lastRequest.has(id) || (row.created_at || 0) > (lastRequest.get(id).created_at || 0)) lastRequest.set(id, row);
    });
    $('pending-count').textContent = String(pending.length);
    if (!pending.length) {
      $('pending-list').innerHTML = '<div class="empty-state">尚无待接入设备</div>';
      return;
    }
    const options = users.map((u) => `<option value="${esc(u.user_id)}">${esc(u.display_name || u.user_id)}</option>`).join('');
    $('pending-list').innerHTML = pending.map((d) => {
      const request = lastRequest.get(d.device_id);
      const when = request && request.created_at ? new Date(request.created_at * 1000).toLocaleString() : '—';
      return `<div class="device-row">
        <div>
          <div class="device-name">${esc(d.device_id)} <span class="badge ota">待接入</span></div>
          <div class="device-meta">板型 ${esc(d.board_model || '未知')} · 固件 ${esc(d.firmware || '未提供')} · 最近请求 ${when} · IP ${esc(d.last_ip || '—')}</div>
        </div>
        <div class="panel-actions">
          <select data-bind-select="${esc(d.device_id)}" ${users.length ? '' : 'disabled'}>${options || '<option value="">无可用用户</option>'}</select>
          <button class="primary-btn" type="button" data-bind="${esc(d.device_id)}" ${users.length ? '' : 'disabled'}>绑定接入</button>
        </div>
      </div>`;
    }).join('');
  }

  function renderDevices() {
    const rows = devices.filter(FILTERS[filter] || FILTERS.all);
    if (!rows.length) {
      $('device-rows').innerHTML = '<tr><td colspan="7" class="empty-state">尚无设备接入 → 按上方向导接入 ESP32</td></tr>';
      return;
    }
    $('device-rows').innerHTML = rows.map((d) => {
      const isEsp = String(d.transport || '').startsWith('esp');
      const badge = isEsp ? '<span class="badge esp">ESP</span>' : '<span class="badge sim">模拟器</span>';
      const bound = d.bound_user_id ? esc(d.bound_user_id) : '<span class="muted">未绑定</span>';
      const seen = d.last_seen ? new Date(d.last_seen * 1000).toLocaleString() : '—';
      const status = d.status || 'offline';
      return `<tr>
        <td><a class="device-link" href="/dashboard/device/${encodeURIComponent(d.device_id)}">${esc(d.device_id)}</a><small>${esc(d.firmware || 'firmware 未提供')}</small></td>
        <td>${badge}</td>
        <td>${d.protocol_version == null ? '—' : 'v' + esc(d.protocol_version)}</td>
        <td>${esc(d.board_model || '—')}</td>
        <td>${bound}</td>
        <td>${seen}</td>
        <td><span class="device-status ${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span></td>
      </tr>`;
    }).join('');
  }

  function renderFirmware(payload) {
    const items = (payload && payload.items) || [];
    $('firmware-dir').textContent = payload && payload.bin_dir ? `存放目录：${payload.bin_dir}` : '';
    if (!items.length) {
      $('firmware-list').innerHTML = '<div class="empty-state">尚无固件。文件名示例：esp32s3_1.2.0.bin</div>';
      return;
    }
    $('firmware-list').innerHTML = items.map((item) => {
      const size = item.size_bytes ? `${(item.size_bytes / 1024).toFixed(1)} KB` : '—';
      const when = item.modified_at ? new Date(item.modified_at * 1000).toLocaleDateString() : '—';
      return `<div class="fw-row">
        <div><strong>${esc(item.filename)}</strong><span class="meta">${esc(item.model)} · v${esc(item.version)} · ${size} · ${when}</span></div>
        <button class="danger-btn" type="button" data-firmware-delete="${esc(item.filename)}">删除</button>
      </div>`;
    }).join('');
  }

  // ---------------------------------------------------------------- actions

  async function load() {
    try {
      const [status, deviceList, ota, firmware, userPayload] = await Promise.all([
        call('/api/esp/status'),
        call('/api/devices'),
        call('/api/esp/ota-requests').catch(() => []),
        call('/api/esp/firmware').catch(() => ({ items: [] })),
        call('/api/users').catch(() => ({ items: [] })),
      ]);
      devices = Array.isArray(deviceList) ? deviceList : [];
      otaRequests = Array.isArray(ota) ? ota : [];
      users = (userPayload && userPayload.items) || [];
      $('service-status').textContent = '服务正常';
      $('service-status').className = 'status-badge ok';
      renderSettings(status);
      renderAddresses(status);
      renderAlerts(status);
      renderStatusGrid(status);
      renderPending();
      renderDevices();
      renderFirmware(firmware);
    } catch (err) {
      if (err && err.message === 'unauthenticated') return;
      $('service-status').textContent = '服务连接中断';
      $('service-status').className = 'status-badge error';
    }
  }

  async function updateSetting(patch, input) {
    try {
      await call('/api/esp/settings', { method: 'POST', body: JSON.stringify(patch) });
      // Refresh first so the acknowledgement is not overwritten by the
      // persistent warning that renderAlerts() puts back.
      await load();
      flashAlert('设置已保存并立即生效。', 'ok');
    } catch (err) {
      flashAlert(`保存失败：${err.message}`, 'bad');
      if (input) {
        if (input.type === 'checkbox') input.checked = !input.checked;
        else input.value = input.dataset.last ?? '';
      }
    }
  }

  $('esp-enabled')?.addEventListener('change', (e) => updateSetting({ enabled: e.target.checked }, e.target));
  $('esp-require-token')?.addEventListener('change', (e) => updateSetting({ require_token: e.target.checked }, e.target));
  $('esp-ota-enabled')?.addEventListener('change', (e) => updateSetting({ ota_enabled: e.target.checked }, e.target));
  $('esp-voiceprint-enabled')?.addEventListener('change', (e) => updateSetting({ voiceprint_enabled: e.target.checked }, e.target));
  $('esp-tools-enabled')?.addEventListener('change', (e) => updateSetting({ tools_enabled: e.target.checked }, e.target));

  /* Number/text settings only commit on blur or Enter -- keying every digit
     would spam the API and rewrite .env mid-typing. */
  $('esp-standby-seconds')?.addEventListener('change', (e) => {
    const value = Number.parseInt(e.target.value, 10);
    if (!Number.isFinite(value) || value < 5 || value > 3600) {
      flashAlert('静默待机时长需在 5–3600 秒之间。', 'bad');
      e.target.value = currentSettings?.standby_seconds ?? '';
      return;
    }
    updateSetting({ standby_seconds: value }, e.target);
  });

  $('esp-standby-notice')?.addEventListener('change', (e) => {
    updateSetting({ standby_notice: e.target.value }, e.target);
  });

  $('esp-llm-stream')?.addEventListener('change', (e) => updateSetting({ llm_stream: e.target.checked }, e.target));

  /* Latency knobs.  Kind matters: the two char counts are integers and the
     hold window is fractional, and the API validates each against its own
     range -- sending 48.5 for a count would come back a 422. */
  for (const [id, key, kind, min, max, message] of [
    ['esp-tts-max-chars', 'tts_max_chars', 'int', 12, 200, '单句字数上限需在 12–200 之间。'],
    ['esp-tts-min-chars', 'tts_min_chars', 'int', 1, 50, '非首句最小字数需在 1–50 之间。'],
    ['esp-wake-word-hold', 'wake_word_hold_seconds', 'float', 0, 10, '唤醒词静默期需在 0–10 秒之间。'],
  ]) {
    $(id)?.addEventListener('change', (e) => {
      const value = kind === 'int'
        ? Number.parseInt(e.target.value, 10)
        : Number.parseFloat(e.target.value);
      if (!Number.isFinite(value) || value < min || value > max) {
        flashAlert(message, 'bad');
        e.target.value = currentSettings?.[key] ?? '';
        return;
      }
      updateSetting({ [key]: value }, e.target);
    });
  }

  document.addEventListener('click', async (event) => {
    const copyId = event.target.getAttribute && event.target.getAttribute('data-copy');
    if (copyId) {
      const text = $(copyId)?.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        event.target.textContent = '已复制';
        setTimeout(() => { event.target.textContent = '复制'; }, 1200);
      } catch {
        event.target.textContent = '复制失败';
      }
      return;
    }

    const bindId = event.target.getAttribute && event.target.getAttribute('data-bind');
    if (bindId) {
      const select = document.querySelector(`[data-bind-select="${CSS.escape(bindId)}"]`);
      const userId = select && select.value;
      if (!userId) return;
      try {
        await call(`/api/devices/${encodeURIComponent(bindId)}/bind`, { method: 'POST', body: JSON.stringify({ user_id: userId }) });
        await load();
        flashAlert('已绑定，设备重连后生效。', 'ok');
      } catch (err) {
        flashAlert(`绑定失败：${err.message}`, 'bad');
      }
      return;
    }

    const filename = event.target.getAttribute && event.target.getAttribute('data-firmware-delete');
    if (filename) {
      if (!window.confirm(`确认删除固件 ${filename}？`)) return;
      try {
        await call(`/api/esp/firmware/${encodeURIComponent(filename)}`, { method: 'DELETE' });
        await load();
        flashAlert('固件已删除。', 'ok');
      } catch (err) {
        flashAlert(`删除失败：${err.message}`, 'bad');
      }
    }
  });

  $('device-filter')?.addEventListener('click', (event) => {
    const value = event.target.getAttribute && event.target.getAttribute('data-filter');
    if (!value) return;
    filter = value;
    Array.prototype.forEach.call($('device-filter').children, (button) => {
      button.classList.toggle('on', button === event.target);
    });
    renderDevices();
  });

  $('refresh-devices')?.addEventListener('click', load);

  $('firmware-input')?.addEventListener('change', () => {
    const file = $('firmware-input').files && $('firmware-input').files[0];
    $('firmware-upload').disabled = !file;
  });

  $('firmware-upload')?.addEventListener('click', async () => {
    const file = $('firmware-input').files && $('firmware-input').files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      await call('/api/esp/firmware', { method: 'POST', body: form });
      $('firmware-input').value = '';
      $('firmware-upload').disabled = true;
      await load();
      flashAlert('固件已上传。', 'ok');
    } catch (err) {
      flashAlert(`上传失败：${err.message}（文件名需为 板型_版本.bin）`, 'bad');
    }
  });

  window.addEventListener('iot:authenticated', load);
  window.addEventListener('iot:admin-profile-updated', applyAdminProfile);
  applyAdminProfile();
  load();
  setInterval(load, 8000);
})();
