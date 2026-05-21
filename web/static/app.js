const $ = id => document.getElementById(id);

    const loginView   = $('login-view');
    const chatView    = $('chat-view');
    const loginForm   = $('login-form');
    const loginError  = $('login-error');
    const loginBtn    = $('login-btn');
    const messagesEl  = $('messages');
    const inputEl     = $('input');
    const sendBtn     = $('send-btn');
    const stopBtn     = $('stop-btn');
    let _abortCtl     = null;
    const modelSelect = $('model-select');
    const modeSelect  = $('mode-select');
    const logoutBtn   = $('logout-btn');


    // -- Auth ----------------------------------------------------------------

    function showLogin(msg = '') {
      loginError.textContent = msg;
      loginView.hidden = false;
      chatView.hidden = true;
    }

    async function showChat() {
      loginView.hidden = true;
      chatView.hidden = false;
      await loadModels();
      inputEl.focus();
    }

    loginForm.addEventListener('submit', async e => {
      e.preventDefault();
      loginBtn.disabled = true;
      loginError.textContent = '';
      const username = $('username').value.trim();
      const password = $('password').value;
      try {
        const res = await fetch('/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        });
        if (!res.ok) {
          loginError.textContent = res.status === 401
            ? 'Invalid username or password.'
            : `Login failed (${res.status}).`;
          return;
        }
        await res.json();
        await showChat();
      } catch {
        loginError.textContent = 'Could not reach the server.';
      } finally {
        loginBtn.disabled = false;
      }
    });

    logoutBtn.addEventListener('click', async () => {
      try { await fetch('/auth/logout', { method: 'POST' }); } catch {}
      showLogin();
    });

    // -- Models --------------------------------------------------------------

    async function loadModels() {
      try {
        const res = await apiFetch('/v1/models');
        if (!res || !res.ok) return;
        const { data } = await res.json();
        modelSelect.innerHTML = '';
        for (const m of data) {
          const opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.id;
          modelSelect.appendChild(opt);
        }
      } catch { /* non-fatal — model list stays empty */ }
    }

    // -- Authenticated fetch ----------------------------------------------------

    function apiFetch(url, opts = {}) {
      return fetch(url, {
        ...opts,
        credentials: 'same-origin',
      });
    }

    // -- Message rendering ---------------------------------------------------

    function appendMessage(role, html) {
      const div = document.createElement('div');
      div.className = `msg ${role}`;
      if (role === 'assistant') {
        div.innerHTML = DOMPurify.sanitize(html);
      } else {
        div.textContent = html; // plain text for user and error roles
      }
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return div;
    }

    // -- Send ----------------------------------------------------------------

    async function sendMessage() {
      const text = inputEl.value.trim();
      if (!text || sendBtn.disabled) return;

      inputEl.value = '';
      sendBtn.disabled = true;
      sendBtn.hidden = true;
      stopBtn.hidden = false;
      modelSelect.disabled = true;
      modeSelect.disabled = true;
      _abortCtl = new AbortController();
      let wasStopped = false;

      appendMessage('user', text);

      const thinking = document.createElement('div');
      thinking.className = 'thinking';
      thinking.textContent = 'Thinking…';
      messagesEl.appendChild(thinking);
      messagesEl.scrollTop = messagesEl.scrollHeight;

      let accumulated = '';
      let assistantDiv = null;

      try {
        const res = await apiFetch('/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: _abortCtl.signal,
          body: JSON.stringify({
            model: modelSelect.value,
            messages: [{ role: 'user', content: text }],
            stream: true,
            rag_mode: modeSelect.value,
          }),
        });

        if (res.status === 401) {
          thinking.remove();
          showLogin('Session expired. Please sign in again.');
          return;
        }

        if (!res.ok) {
          thinking.remove();
          const body = await res.text().catch(() => '');
          appendMessage('error', `Server error ${res.status}${body ? ': ' + body : ''}`);
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop(); // keep incomplete last line in buffer

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const payload = line.slice(6).trim();
            if (payload === '[DONE]') continue;
            let chunk;
            try { chunk = JSON.parse(payload); } catch { continue; }
            const delta = chunk?.choices?.[0]?.delta?.content;
            if (!delta) continue;

            accumulated += delta;
            if (!assistantDiv) {
              thinking.remove();
              assistantDiv = appendMessage('assistant', marked.parse(accumulated));
            } else {
              assistantDiv.innerHTML = DOMPurify.sanitize(marked.parse(accumulated));
              messagesEl.scrollTop = messagesEl.scrollHeight;
            }
          }
        }

        if (!assistantDiv) {
          thinking.remove();
          appendMessage('error', 'No response received.');
        }
      } catch (err) {
        if (err.name === 'AbortError') {
          wasStopped = true;
          thinking.remove();
          if (assistantDiv) {
            const suffix = document.createElement('span');
            suffix.className = 'stopped-suffix';
            suffix.textContent = '[stopped]';
            assistantDiv.appendChild(suffix);
          } else {
            appendMessage('error', '[stopped]');
          }
        } else {
          thinking.remove();
          appendMessage('error', `Request failed: ${err.message}`);
        }
      } finally {
        _abortCtl = null;
        sendBtn.disabled = false;
        sendBtn.hidden = false;
        stopBtn.hidden = true;
        modelSelect.disabled = false;
        modeSelect.disabled = false;
        if (!wasStopped) inputEl.focus();
      }
    }

    sendBtn.addEventListener('click', sendMessage);
    stopBtn.addEventListener('click', () => { if (_abortCtl) _abortCtl.abort(); });

    inputEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    inputEl.addEventListener('input', () => {
    });

    // -- Init ----------------------------------------------------------------

    (async () => {
      try {
        const res = await apiFetch('/v1/models');
        if (!res || res.status === 401) { showLogin(); return; }
        await showChat();
      } catch {
        showLogin();
      }
    })();
