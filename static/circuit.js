// 回路図の操作。部品を押す → 解説と操作 → ブレーカー判定（/actions/check）→ 確認 → 実行（/actions/run）。
// 文字列はすべて textContent で入れる（GitHub 由来の文字列を HTML として解釈しない）。
(function () {
  const parts = JSON.parse(document.getElementById('parts-data').textContent);
  const panel = document.getElementById('panel');
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const breaker = document.getElementById('breaker');
  const breakerText = document.getElementById('breaker-text');
  let current = null;
  let checkSeq = 0;

  function setPanel(...nodes) {
    // null / false は捨てる（replaceChildren に渡すと文字列 "null" として画面に出てしまう）
    panel.replaceChildren(...nodes.filter((n) => n != null && n !== false));
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

  function actionButtons(actions) {
    return el('div', { class: 'acts' }, (actions || []).map((a) =>
      el('div', { class: 'act' },
        el('div', { class: 'act-label' }, a.label),
        el('div', { class: 'act-btns' },
          a.terminal ? null : el('button', { type: 'button', class: 'btn' + (a.danger ? ' danger' : ''), onclick: () => prepare(a, 'tool') }, 'ツールから実行'),
          el('button', { type: 'button', class: 'btn ghost', onclick: () => (a.workbench ? prepareWorkbench(a) : a.terminal ? copyAndReport(a.cmd) : prepare(a, 'terminal')) }, a.workbench ? 'コマンドの案内へ' : 'ターミナルから実行')),
        cmdBlock(a.cmd))));
  }

  // 「ターミナルから実行」: コマンドをコピーするだけ。アプリは実行しない（作業台にも GitHub にも触れない）
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* 下の方法で試す */ }
    try {
      const t = document.createElement('textarea');
      t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
      document.body.append(t); t.select();
      const ok = document.execCommand('copy');
      t.remove();
      return ok;
    } catch (e) { return false; }
  }

  async function copyAndReport(cmd) {
    const ok = !!cmd && await copyText(cmd);
    show(current, ok
      ? { ok: true, text: 'コマンドをコピーしました。自分のターミナルに貼り付けて実行してください。' }
      : { ok: false, text: 'コピーできませんでした。「コマンドで言うと」を開いて、文字を選んでコピーしてください。' });
  }

  // 'ok'（正常）/ 'warn'（注意: 確認すれば進める）/ 'trip'（トリップ: 止める）
  function setBreaker(state) {
    breaker.classList.toggle('trip', state === 'trip');
    breaker.classList.toggle('warn', state === 'warn');
    breakerText.textContent = { trip: 'トリップ', warn: '注意' }[state] || '正常';
  }

  function highlight(partId) {
    document.querySelectorAll('.hit').forEach((h) => h.classList.toggle('sel', h.dataset.part === partId));
  }

  function show(partId, flash) {
    checkSeq++;
    const p = parts[partId];
    if (!p) return;
    current = partId;
    if (p.dynamic_guide) return readGuide(p.repo, flash);
    highlight(partId);
    setBreaker('ok');
    document.querySelectorAll('.tripping').forEach((n) => n.classList.remove('tripping'));
    // 操作ごとに「ツールから実行」「ターミナルから実行」と、裏で走るコマンド（「コマンドで言うと」）を添える。
    // 作業台の操作（terminal）は、アプリが作業台に書き込まないので「ターミナルから実行」だけ。
    const acts = actionButtons(p.actions);
    const steps = (p.steps || []).map((step) => el('section', { class: 'sync-step' },
      el('h3', {}, step.title), el('p', {}, step.body), actionButtons(step.actions)));
    setPanel(
      flash ? el('div', { class: 'flash ' + (flash.ok ? 'ok' : 'ng') }, flash.text) : null,
      el('div', { class: 'kicker' }, '部品'),
      el('h2', {}, p.title),
      el('div', { class: 'term' }, p.term_label || 'GitHub では ', el('b', {}, p.term)),
      el('p', {}, p.body),
      p.files ? el('ul', { class: 'file-list' }, p.files.map((f) => el('li', {}, f))) : null,
      p.url ? el('a', { href: p.url, target: '_blank', rel: 'noopener', style: 'font-size:14px' }, 'GitHub で同じものを見る ↗') : null,
      (p.actions || []).length ? acts : steps.length ? null : el('p', { class: 'cap' }, 'この部品で直接できる操作はありません。'),
      ...steps,
      p.guided ? el('button', { type: 'button', class: 'btn ghost', onclick: () => readGuide(p.repo) }, '実行後、状態を読み直す') : null,
    );
  }

  async function readGuide(repo, flash) {
    const seq = ++checkSeq;
    setPanel(el('p', {}, '作業台とGitHub、本番の位置を確認しています…'));
    try {
      const response = await fetch('/repo/' + encodeURIComponent(repo) + '/publish-guide');
      const guide = await response.json();
      if (seq !== checkSeq) return;
      if (!response.ok) return showError(guide.message || guide.detail || '状態を取得できませんでした。');
      parts.publish = guide;
      show('publish', flash);
    } catch (e) {
      if (seq === checkSeq) showError('状態を取得できませんでした。接続を確認して読み直してください。');
    }
  }

  function prepareWorkbench(a) {
    const selected = [];
    const files = (a.files || []).map((path) => {
      const checkbox = el('input', { type: 'checkbox' });
      selected.push({ path, checkbox });
      return el('label', { class: 'file-choice' }, checkbox, el('span', {}, path));
    });
    const message = a.workbench === 'commit' ? el('input', { class: 'inp', id: 'commit-message', maxlength: 500 }) : null;
    const staged = parts[current].files || [];
    const index = parts[current].index || '';
    setPanel(el('h2', {}, a.label),
      el('p', {}, a.workbench === 'stage' ? '今回の修正に含めるファイルを選んでください。選ばないファイルは記録対象に入りません。' : a.workbench === 'commit' ? '選択済みのファイルだけを記録します。変更の名前を入力してください。' : '同じ分岐へpushします。幹線への合流は次の段です。'),
      ...files,
      message ? el('ul', { class: 'file-list' }, staged.map((f) => el('li', {}, f))) : null,
      message ? el('label', { for: 'commit-message' }, '変更の名前（コミットメッセージ）') : null,
      message,
      el('div', { class: 'acts' },
        el('button', { type: 'button', class: 'btn', onclick: () => workbenchCommand(a, {
          files: selected.filter((s) => s.checkbox.checked).map((s) => s.path),
          message: message ? message.value : '', expected_files: staged, expected_index: index,
        }) }, 'コマンドを確認する'),
        el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, '戻る')));
  }

  async function workbenchCommand(a, inputs) {
    const seq = ++checkSeq;
    try {
      const res = await post('/workbench/command', { ...a.params, ...inputs, operation: a.workbench });
      if (seq !== checkSeq) return;
      if (!res.ok || !res.cmd) return showError(res.message || res.detail);
      a.cmd = res.cmd;
      setPanel(el('h2', {}, 'コマンドを確認してコピーする'),
        el('p', {}, 'PowerShellに貼り付けて実行してください。実行後は「状態を読み直す」で次の手順を確認します。'),
        inputs.files.length ? el('ul', { class: 'file-list' }, inputs.files.map((f) => el('li', {}, f))) : null,
        inputs.message ? el('p', {}, '変更の名前: ' + inputs.message) : null,
        el('code', { class: 'cmd' }, res.cmd),
        el('button', { type: 'button', class: 'btn', onclick: () => copyAndReport(res.cmd) }, 'コマンドをコピー'),
        el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, '戻る'));
    } catch (e) {
      if (seq === checkSeq) showError('コマンドの案内を取得できませんでした。状態を読み直してください。');
    }
  }

  // 入力が要る操作は、先に入力欄を出す
  function prepare(a, mode) {
    if (!a.fields || !a.fields.length) return check(a, a.params, mode);
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
          check(a, params, mode);
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

  async function check(a, params, mode) {
    const seq = ++checkSeq;
    // 確認対象は、部品に表示した先端を保持する。応答の最新SHAへ置き換えない。
    const confirmedParams = Object.freeze({ ...params });
    const res = await post('/actions/check', { action: a.action, params: confirmedParams });
    if (seq !== checkSeq) return;
    if (res.ok === false || res.verdicts === undefined) return showError(res.message || res.detail);
    if (res.tripped) return showTrip(res);
    showConfirm(a, confirmedParams, res, mode);
  }

  function showError(message) {
    checkSeq++;
    setPanel(el('div', { class: 'flash ng' }, message || 'エラーが起きました'),
      el('button', { type: 'button', class: 'btn ghost', onclick: () => show(current) }, '戻る'));
  }

  function showTrip(res) {
    checkSeq++;
    setBreaker('trip');
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

  function showConfirm(a, params, res, mode) {
    const terminal = mode === 'terminal';
    if (terminal && !res.cmd) return showError('この入力はコマンドに埋め込めないため、コピーできません（ツールから実行してください）。');
    const warns = res.verdicts.filter((v) => v.level === 'warn');
    setBreaker(warns.length ? 'warn' : 'ok');
    const go = el('button', { type: 'button', class: 'btn', disabled: warns.length > 0 }, terminal ? 'コマンドをコピー' : '実行する');
    const ack = warns.length ? el('input', { type: 'checkbox', id: 'ack', onchange: (e) => { go.disabled = !e.target.checked; } }) : null;
    go.addEventListener('click', () => (terminal ? copyAndReport(res.cmd) : run(a, params, !!(ack && ack.checked), go)));
    setPanel(
      el('div', { class: 'kicker' }, '操作の確認'),
      el('h2', {}, res.label),
      el('div', { style: 'font-size:14px;font-weight:700' }, terminal ? 'ターミナルで実行すると起きること' : 'この操作で起きること'),
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
    if (!res.ok) return showError(res.message || res.detail);
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
