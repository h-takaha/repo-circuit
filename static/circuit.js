// 回路図の操作。部品を押す → 解説と操作 → ブレーカー判定（/actions/check）→ 確認 → 実行（/actions/run）。
// 文字列はすべて textContent で入れる（GitHub 由来の文字列を HTML として解釈しない）。
(function () {
  const parts = JSON.parse(document.getElementById('parts-data').textContent);
  const panel = document.getElementById('panel');
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const breaker = document.getElementById('breaker');
  const breakerText = document.getElementById('breaker-text');
  let current = null;

  function setPanel(...nodes) {
    panel.replaceChildren(...nodes);
    // 狭い画面では解説パネルが図の下に来るので、押した部品の説明までスクロールする
    if (window.innerWidth < 1100) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') e.className = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      e.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return e;
  }

  function cmdBlock(cmd) {
    if (!cmd) return null;
    return el('details', {}, el('summary', {}, 'コマンドで言うと'), el('code', { class: 'cmd' }, cmd));
  }

  function setBreaker(tripped) {
    breaker.classList.toggle('trip', tripped);
    breakerText.textContent = tripped ? 'トリップ' : '正常';
  }

  function highlight(partId) {
    document.querySelectorAll('.hit').forEach((h) => h.classList.toggle('sel', h.dataset.part === partId));
  }

  function show(partId, flash) {
    const p = parts[partId];
    if (!p) return;
    current = partId;
    highlight(partId);
    setBreaker(false);
    document.querySelectorAll('.tripping').forEach((n) => n.classList.remove('tripping'));
    const acts = el('div', { class: 'acts' }, (p.actions || []).map((a) =>
      el('button', { type: 'button', class: 'btn' + (a.danger ? ' danger' : ''), onclick: () => prepare(a) }, a.label)));
    setPanel(
      flash ? el('div', { class: 'flash ' + (flash.ok ? 'ok' : 'ng') }, flash.text) : null,
      el('div', { class: 'kicker' }, '部品'),
      el('h2', {}, p.title),
      el('div', { class: 'term' }, 'GitHub では ', el('b', {}, p.term)),
      el('p', {}, p.body),
      p.url ? el('a', { href: p.url, target: '_blank', rel: 'noopener', style: 'font-size:14px' }, 'GitHub で同じものを見る ↗') : null,
      (p.actions || []).length ? acts : el('p', { class: 'cap' }, 'この部品で直接できる操作はありません。'),
      cmdBlock(p.cmd),
    );
  }

  // 入力が要る操作は、先に入力欄を出す
  function prepare(a) {
    if (!a.fields || !a.fields.length) return check(a, a.params);
    const inputs = {};
    const fields = a.fields.map((f) => {
      const id = 'f-' + f.name;
      const input = f.multiline
        ? el('textarea', { id, class: 'inp', rows: 3 })
        : el('input', { id, class: 'inp' });
      input.value = f.value || '';
      inputs[f.name] = input;
      return el('div', { class: 'field' }, el('label', { for: id }, f.label), input);
    });
    setPanel(
      el('div', { class: 'kicker' }, '入力'),
      el('h2', {}, a.label),
      ...fields,
      el('div', { style: 'display:flex;gap:8px' },
        el('button', { type: 'button', class: 'btn', onclick: () => {
          const params = { ...a.params };
          for (const [k, input] of Object.entries(inputs)) params[k] = input.value;
          check(a, params);
        } }, '確認へ'),
        el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, 'やめる')),
    );
  }

  async function post(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body),
    });
    let data;
    try { data = await r.json(); } catch (e) { data = { ok: false, message: 'サーバーの応答を読めませんでした (' + r.status + ')' }; }
    return data;
  }

  async function check(a, params) {
    const res = await post('/actions/check', { action: a.action, params });
    if (res.verdicts === undefined) return showError(res.message || res.detail);
    if (res.tripped) return showTrip(res);
    showConfirm(a, params, res);
  }

  function showError(message) {
    setPanel(el('div', { class: 'flash ng' }, message || 'エラーが起きました'),
      el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, '戻る'));
  }

  function showTrip(res) {
    setBreaker(true);
    const hit = document.querySelector('.hit.sel');
    if (res.action === 'merge_pr' && res.cmd) {
      const n = (res.cmd.match(/merge (\d+)/) || [])[1];
      const blade = n && document.getElementById('blade-' + n);
      if (blade) blade.classList.add('tripping');
    }
    if (hit) hit.classList.add('sel');
    const trips = res.verdicts.filter((v) => v.level === 'trip');
    setPanel(
      el('div', { class: 'trip-head' },
        el('span', { 'aria-hidden': 'true' }, '■'), 'ブレーカーが落ちました'),
      el('div', { class: 'kicker' }, res.label),
      ...trips.map((v) => el('div', { style: 'display:flex;flex-direction:column;gap:8px' },
        el('div', { style: 'font-size:15px;font-weight:700;line-height:1.5' }, v.rule),
        el('div', { class: 'kicker' }, 'なぜダメ？'),
        el('p', {}, v.why),
        v.steps.length ? el('div', { class: 'kicker' }, '代わりにやること') : null,
        v.steps.length ? el('ol', {}, v.steps.map((s) => el('li', {}, s))) : null,
        cmdBlock(v.cmd))),
      el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, 'わかった（ブレーカーを戻す）'),
    );
  }

  function showConfirm(a, params, res) {
    const warns = res.verdicts.filter((v) => v.level === 'warn');
    const go = el('button', { type: 'button', class: 'btn', disabled: warns.length > 0 }, '実行する');
    const ack = warns.length ? el('input', { type: 'checkbox', id: 'ack', onchange: (e) => { go.disabled = !e.target.checked; } }) : null;
    go.addEventListener('click', () => run(a, params, !!(ack && ack.checked), go));
    setPanel(
      el('div', { class: 'kicker' }, '操作の確認'),
      el('h2', {}, res.label),
      el('div', { style: 'font-size:14px;font-weight:700' }, 'この操作で起きること'),
      el('ul', {}, res.preview.map((t) => el('li', {}, t))),
      ...warns.map((v) => el('div', { class: 'guard warn' },
        el('div', { class: 'gt' }, '▲ 注意 — ' + v.rule),
        el('p', { style: 'font-size:13px;line-height:1.6' }, v.why),
        v.steps.length ? el('ul', { style: 'font-size:13px' }, v.steps.map((s) => el('li', {}, s))) : null)),
      ack ? el('label', { class: 'ack', for: 'ack' }, ack, '注意を読んで理解した') : null,
      el('div', { style: 'display:flex;gap:8px' }, go,
        el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, 'やめる')),
      cmdBlock(res.cmd),
    );
  }

  async function run(a, params, ack, btn) {
    btn.disabled = true;
    const res = await post('/actions/run', { action: a.action, params, ack });
    if (res.tripped) return showTrip(res);
    if (!res.ok) return showError(res.message);
    const done = () => {
      const q = new URLSearchParams({ msg: res.message, sel: current || '' });
      if (res.url) q.set('url', res.url);
      location.href = location.pathname + '?' + q.toString();
    };
    if (a.action === 'merge_pr') {
      const n = params.number;
      const blade = document.getElementById('blade-' + n);
      if (blade) { blade.setAttribute('x2', blade.dataset.cx); blade.setAttribute('y2', blade.dataset.cy); }
      const flow = document.getElementById('flow-' + n);
      if (flow) flow.classList.add('go');
      setTimeout(() => document.getElementById('lamp').classList.add('on'), 600);
      setPanel(el('div', { class: 'kicker' }, 'いま何が起きた？'), el('h2', {}, '通電しました'),
        el('ul', {}, res.preview.map((t) => el('li', {}, t))));
      setTimeout(done, 2200);
    } else {
      done();
    }
  }

  document.querySelectorAll('[data-part]').forEach((b) => b.addEventListener('click', () => show(b.dataset.part)));
  const sw = document.getElementById('repo-switch');
  if (sw) sw.addEventListener('change', () => { location.href = '/repo/' + encodeURIComponent(sw.value); });

  const q = new URLSearchParams(location.search);
  if (q.get('msg')) {
    const sel = q.get('sel');
    const flash = { ok: true, text: q.get('msg') };
    if (sel && parts[sel]) show(sel, flash);
    else panel.prepend(el('div', { class: 'flash ok' }, flash.text));
    history.replaceState(null, '', location.pathname);
  } else if (q.get('sel') && parts[q.get('sel')]) {
    show(q.get('sel'));
  }
})();
