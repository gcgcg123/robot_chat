/* 设备详情页：实时状态、字幕流、指令下发、会话历史与绑定管理。 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const STATUS_LABEL = { online: '在线', stale: '心跳延迟', offline: '离线' };
  const STATE_LABEL = { idle: '待机', listening: '正在聆听', transcribing: '正在理解', thinking: '正在思考', speaking: '正在回复', offline: '设备离线', error: '暂时无法完成' };
  const RISK_LABEL = { '': '无', none: '无', attention: '需关注', urgent: '紧急' };
  const EMOTION_LABEL = { positive: '正向', neutral: '平稳', negative: '需关注' };

  const deviceId = decodeURIComponent((window.location.pathname.split('/').filter(Boolean).pop()) || '');
  let csrf = '';
  let device = null;
  let users = [];
  let observer = null;
  let lastTurnId = '';
  let live = null;

  function alertBox(message, tone) {
    const box = $('detail-alert');
    if (!box) return;
    if (!message) { box.className = 'esp-alert'; box.textContent = ''; return; }
    box.className = `esp-alert show ${tone || 'ok'}`;
    box.textContent = message;
  }

  async function call(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== 'GET') headers['X-CSRF-Token'] = csrf;
    if (opts.body) headers['Content-Type'] = 'application/json';
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts, { headers }));
    if (res.status === 401 || res.status === 403) { window.location.href = '/dashboard'; throw new Error('unauthenticated'); }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    return res.status === 204 ? null : res.json();
  }

  async function bootstrapSession() {
    const res = await fetch('/api/auth/session', { cache: 'no-store' });
    if (!res.ok) { window.location.href = '/dashboard'; return false; }
    csrf = (await res.json()).csrf_token;
    return true;
  }

  // ---------------------------------------------------------------- render

  function renderLive(next) {
    if (next) live = Object.assign({}, live || {}, next);
    const snapshot = live || {};
    const state = device && device.connected ? (snapshot.state || 'idle') : 'offline';
    $('m-state').textContent = STATE_LABEL[state] || state;
    $('m-caption').textContent = snapshot.caption || '—';
    $('m-user').textContent = snapshot.user_id || '未识别';
    $('m-bound').textContent = snapshot.identity_verified ? '声纹已验证' : '声纹未验证';
    $('m-emotion').textContent = EMOTION_LABEL[snapshot.emotion] || snapshot.emotion || '—';
    const risk = snapshot.risk_level || '';
    $('m-risk').innerHTML = `<span class="risk-pill ${esc(risk || 'none')}">${esc(RISK_LABEL[risk] || risk || '无')}</span>`;
    const connected = !!(device && device.connected);
    $('live-dot').className = `live-dot${connected ? ' on' : ''}`;
    $('live-tag').textContent = connected ? `已连接 · ${snapshot.turns || 0} 轮` : '设备未连接';
  }

  function renderFacts() {
    const facts = [
      ['设备 ID', device.device_id],
      ['传输', String(device.transport || '').startsWith('esp') ? 'ESP 真机' : (device.transport || '—')],
      ['协议版本', device.protocol_version == null ? '—' : `v${device.protocol_version}`],
      ['板型', device.board_model || '—'],
      ['固件', device.firmware || '—'],
      ['最近 IP', device.last_ip || '—'],
      ['最后心跳', device.last_seen ? new Date(device.last_seen * 1000).toLocaleString() : '—'],
      ['最后会话', device.last_session_at ? new Date(device.last_session_at * 1000).toLocaleString() : '—'],
      ['绑定用户', device.bound_user_id || '未绑定'],
    ];
    $('device-facts').innerHTML = facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  }

  function renderSessions(rows) {
    if (!rows || !rows.length) {
      $('session-rows').innerHTML = '<tr><td colspan="6" class="empty-state">尚无会话</td></tr>';
      return;
    }
    $('session-rows').innerHTML = rows.map((row) => {
      const started = row.started_at ? new Date(row.started_at * 1000).toLocaleString() : '—';
      const ended = row.ended_at ? new Date(row.ended_at * 1000).toLocaleString() : '进行中';
      return `<tr>
        <td>${started}</td><td>${ended}</td>
        <td>${row.turn_count == null ? '—' : esc(row.turn_count)}</td>
        <td>${esc(row.board_model || '—')}</td>
        <td>${esc(row.firmware || '—')}</td>
        <td>${row.last_error ? esc(row.last_error) : '—'}</td>
      </tr>`;
    }).join('');
  }

  /* What the firmware declared it can do. An empty list with
     `available: false` is the honest answer for a build without MCP, and it is
     exactly what explains "语音控制音量没反应". */
  function renderTools(source) {
    const tools = Array.isArray(source.tools) ? source.tools : [];
    const status = source.tools_status || {};
    const tag = $('tools-tag');
    if (tools.length) {
      tag.textContent = `${tools.length} 个可用`;
      tag.style.background = '#dcfce7';
      tag.style.color = '#15803d';
    } else if (status.available === false || status.reason) {
      tag.textContent = '固件未响应';
      tag.style.background = '#ffe4e6';
      tag.style.color = '#be123c';
    } else {
      tag.textContent = source.connected ? '等待上报' : '未连接';
      tag.style.background = '';
      tag.style.color = '';
    }
    if (!tools.length) {
      const reason = status.reason ? `（${esc(status.reason)}）` : '';
      $('tool-list').innerHTML = `<div class="empty-state">设备尚未上报任何工具${reason}。固件需支持 MCP 才能语音控制音量、亮度等。</div>`;
      return;
    }
    $('tool-list').innerHTML = tools.map((tool) => {
      const params = Array.isArray(tool.parameters) ? tool.parameters : [];
      const inputs = params
        .map((name) => `<input data-tool-arg="${esc(name)}" type="text" placeholder="${esc(name)}" />`)
        .join('');
      return `<div data-tool-card style="padding:9px 0;border-top:1px solid #f1f3f7">
        <div style="display:flex;justify-content:space-between;gap:10px;align-items:center">
          <code style="font-size:12px;color:#1f2937">${esc(tool.name)}</code>
          <span style="font-size:11px;color:var(--muted)">${esc(tool.transport || 'mcp')}</span>
        </div>
        <div style="font-size:12px;color:var(--muted);margin:3px 0 6px">${esc(tool.description || '—')}</div>
        <div class="cmd-row" style="margin:0">${inputs}
          <button class="cmd-btn primary" type="button" data-command="mcp" data-tool="${esc(tool.name)}">运行</button>
        </div>
      </div>`;
    }).join('');
  }

  function renderCommands(rows) {
    if (!rows || !rows.length) {
      $('command-list').innerHTML = '<div class="empty-state">尚无指令记录</div>';
      return;
    }
    const labels = { speak: '播报', abort: '打断', close: '结束', iot: 'IoT', mcp: '工具' };
    const statusText = { queued: '已下发', offline: '设备离线', pending: '待下发', delivered: '已送达' };
    $('command-list').innerHTML = rows.map((row) => {
      const when = row.created_at ? new Date(row.created_at * 1000).toLocaleString() : '—';
      const text = row.payload && row.payload.text ? ` · “${row.payload.text}”` : '';
      // The column is `type` (device_commands.type), not `command_type`.
      const kind = row.type || row.command_type || '';
      return `<div class="cmd-row" style="margin:0;padding:8px 0;border-top:1px solid #f1f3f7;justify-content:space-between">
        <span style="font-size:13px;color:#374151">${esc(labels[kind] || kind)}${esc(text)}<br><small style="color:var(--muted);font-size:11px">${when}</small></span>
        <span class="badge ${row.status === 'queued' || row.status === 'delivered' ? 'esp' : 'warn'}" style="display:inline-flex;border-radius:999px;padding:3px 9px;font-size:11px;font-weight:700">${esc(statusText[row.status] || row.status)}</span>
      </div>`;
    }).join('');
  }

  function pushCaption(who, text) {    const stream = $('caption-stream');
    if (stream.querySelector('.empty-state')) stream.innerHTML = '';
    const row = document.createElement('div');
    row.className = `caption-row ${who}`;
    row.innerHTML = `<span class="who">${who === 'user' ? '用户' : '机器人'}</span><span class="bubble">${esc(text)}</span>`;
    stream.append(row);
    stream.scrollTop = stream.scrollHeight;
    while (stream.children.length > 60) stream.removeChild(stream.firstChild);
  }

  // ---------------------------------------------------------------- observer

  function connectObserver() {
    if (observer) { try { observer.close(); } catch { /* ignore */ } }
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    observer = new WebSocket(`${scheme}://${window.location.host}/ws/device-observe`);
    observer.onmessage = (event) => {
      let body;
      try { body = JSON.parse(event.data); } catch { return; }
      if (body.type === 'observer.ready') {
        const found = (body.live || []).find((item) => item.device_id === deviceId);
        if (found) live = Object.assign({}, live || {}, found);
        renderLive();
        return;
      }
      if (body.device_id !== deviceId) return;
      handleEvent(body);
    };
    observer.onclose = () => { if (device) device.connected = false; renderLive(); setTimeout(connectObserver, 4000); };
    observer.onerror = () => { try { observer.close(); } catch { /* ignore */ } };
  }

  function handleEvent(event) {
    const payload = event.payload || {};
    if (event.type === 'turn.started') {
      if (lastTurnId && lastTurnId !== event.turn_id) $('caption-stream').innerHTML = '';
      lastTurnId = event.turn_id;
      renderLive({ state: 'thinking', caption: '正在理解…', user_text: '', transcript: '' });
      return;
    }
    if (event.turn_id && lastTurnId && event.turn_id !== lastTurnId) return;
    if (event.type === 'stt.final') {
      pushCaption('user', payload.text || '');
      renderLive({ user_text: payload.text || '', state: 'thinking', caption: payload.text || '' });
    } else if (event.type === 'tts.segment') {
      if (payload.text) pushCaption('bot', payload.text);
      const result = payload.result || {};
      renderLive({
        state: 'speaking',
        emotion: result.emotion || undefined,
        risk_level: (result.risk && result.risk.risk_level) || undefined,
      });
    } else if (event.type === 'tts.end') {
      renderLive({ state: 'idle', caption: '' });
    } else if (event.type === 'display.state') {
      renderLive({ state: payload.state || 'idle', caption: payload.caption || '' });
    } else if (event.type === 'device.tools') {
      renderTools({ tools: payload.tools || [], connected: true });
    } else if (event.type === 'device.command') {
      pushCaption('bot', payload.status === 'ok'
        ? `（已执行设备工具 ${payload.tool}）`
        : `（设备工具 ${payload.tool} 执行失败：${payload.detail || '未知'}）`);
    } else if (event.type === 'turn.failed') {
      pushCaption('bot', `（处理失败：${payload.error || '未知'}）`);
    } else if (event.type === 'turn.interrupted') {
      renderLive({ state: 'idle', caption: '播放已打断' });
    }
  }

  // ---------------------------------------------------------------- load

  async function load() {
    try {
      device = await call(`/api/devices/${encodeURIComponent(deviceId)}`);
    } catch (err) {
      alertBox(`无法加载设备 ${deviceId}：${err.message}`, 'bad');
      $('device-name').textContent = '设备不存在';
      return;
    }
    $('device-name').textContent = device.device_id;
    const isEsp = String(device.transport || '').startsWith('esp');
    $('device-sub').textContent = `${isEsp ? 'ESP 真机' : '模拟器'} · 协议 ${device.protocol_version == null ? '—' : 'v' + device.protocol_version} · 板型 ${device.board_model || '未知'}`;
    const status = device.status || 'offline';
    $('device-status').textContent = device.connected ? '在线（已连接）' : (STATUS_LABEL[status] || status);
    $('device-status').className = `status-badge ${device.connected ? 'ok' : status === 'offline' ? 'error' : 'pending'}`;
    $('m-seen').textContent = device.last_seen ? new Date(device.last_seen * 1000).toLocaleTimeString() : '—';
    renderFacts();
    renderTools(device);
    live = device.live ? Object.assign({}, device.live) : {};
    renderLive();

    const [sessions, commands] = await Promise.all([
      call(`/api/devices/${encodeURIComponent(deviceId)}/sessions`).catch(() => []),
      call(`/api/devices/${encodeURIComponent(deviceId)}/commands`).catch(() => []),
    ]);
    renderSessions(sessions);
    renderCommands(commands);
    $('m-sessions').textContent = `会话 ${Array.isArray(sessions) ? sessions.length : 0} · 轮次 ${Array.isArray(sessions) ? sessions.reduce((sum, s) => sum + (s.turn_count || 0), 0) : 0}`;
    if (!users.length) {
      const payload = await call('/api/users').catch(() => ({ items: [] }));
      users = (payload && payload.items) || [];
      $('bind-user').innerHTML = users.map((u) => `<option value="${esc(u.user_id)}"${device.bound_user_id === u.user_id ? ' selected' : ''}>${esc(u.display_name || u.user_id)}</option>`).join('') || '<option value="">无可用用户</option>';
    }
  }

  // ---------------------------------------------------------------- actions

  document.addEventListener('click', async (event) => {
    const type = event.target.getAttribute && event.target.getAttribute('data-command');
    if (!type) return;
    const payload = {};
    if (type === 'speak') {
      const text = ($('speak-text').value || '').trim();
      if (!text) { alertBox('请输入要播报的文字。', 'bad'); return; }
      payload.text = text;
    }
    if (type === 'mcp') {
      const name = event.target.getAttribute('data-tool') || '';
      if (!name) return;
      const card = event.target.closest('[data-tool-card]');
      const args = {};
      card?.querySelectorAll('[data-tool-arg]').forEach((input) => {
        const key = input.getAttribute('data-tool-arg');
        const raw = (input.value || '').trim();
        if (!key || raw === '') return;
        args[key] = /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : raw;
      });
      payload.name = name;
      payload.arguments = args;
    }
    try {
      const result = await call(`/api/devices/${encodeURIComponent(deviceId)}/commands`, { method: 'POST', body: JSON.stringify({ type, payload }) });
      alertBox(result.delivered ? '指令已下发到在线设备。' : '设备当前未连接，指令已记录为 offline。', result.delivered ? 'ok' : 'bad');
      $('speak-text').value = '';
      const commands = await call(`/api/devices/${encodeURIComponent(deviceId)}/commands`).catch(() => []);
      renderCommands(commands);
    } catch (err) {
      alertBox(`下发失败：${err.message}`, 'bad');
    }
  });

  $('bind-btn')?.addEventListener('click', async () => {
    const userId = $('bind-user').value;
    if (!userId) { alertBox('没有可绑定的用户。', 'bad'); return; }
    try {
      await call(`/api/devices/${encodeURIComponent(deviceId)}/bind`, { method: 'POST', body: JSON.stringify({ user_id: userId }) });
      alertBox('绑定成功，设备重连后生效。', 'ok');
      await load();
    } catch (err) {
      alertBox(`绑定失败：${err.message}`, 'bad');
    }
  });

  $('unbind-btn')?.addEventListener('click', async () => {
    if (!window.confirm('确认解除该设备与用户的绑定？')) return;
    try {
      await call(`/api/devices/${encodeURIComponent(deviceId)}/bind`, { method: 'DELETE' });
      alertBox('已解除绑定。', 'ok');
      await load();
    } catch (err) {
      alertBox(`解绑失败：${err.message}`, 'bad');
    }
  });

  $('refresh-btn')?.addEventListener('click', load);

  (async () => {
    if (!await bootstrapSession()) return;
    await load();
    connectObserver();
    setInterval(load, 10000);
  })();
})();
