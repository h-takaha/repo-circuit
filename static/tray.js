// 部品トレイ: 「付箋を貼る」の下に、いまの入力で走る gh コマンドを見せる。
// 「ターミナルから実行」は、ブレーカー判定（/actions/check）を通ったときだけコマンドをコピーする。アプリは実行しない。
(function () {
  const form = document.querySelector('form.tform');
  const out = document.getElementById('issue-cmd');
  const copyBtn = document.getElementById('issue-copy');
  const msg = document.getElementById('issue-copy-msg');
  if (!form || !out) return;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  function params() {
    const val = (name) => (form.elements[name] && form.elements[name].value) || '';
    return { repo: val('repo'), title: val('title'), situation: val('situation'), want: val('want'),
             kind: val('kind') || 'improvement', priority: val('priority') || 'p2', source: 'web' };
  }

  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
    return r.json();
  }

  // 表示用（判定も記録もしない）。入力のたびにサーバーで組み立て直す
  let timer = null;
  async function refresh() {
    try {
      const res = await post('/actions/command', { action: 'create_issue', params: params() });
      if (res.cmd) out.textContent = res.cmd;
    } catch (e) { /* 表示が古いままになるだけ */ }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 250); }
  form.addEventListener('input', schedule);
  form.addEventListener('change', schedule);
  refresh();

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

  function say(ok, ...lines) {
    msg.hidden = false;
    msg.className = 'flash ' + (ok ? 'ok' : 'ng');
    msg.replaceChildren(...lines.map((t) => { const p = document.createElement('p'); p.style.margin = '4px 0'; p.textContent = t; return p; }));
  }

  if (copyBtn) copyBtn.addEventListener('click', async () => {
    if (!params().title.trim()) return say(false, 'ひとことで（タイトル）を入力してください。');
    let res;
    try { res = await post('/actions/check', { action: 'create_issue', params: params() }); } catch (e) { return say(false, '判定に失敗しました。'); }
    if (res.verdicts === undefined) return say(false, res.message || res.detail || 'エラーが起きました');
    if (res.tripped) {
      const trips = res.verdicts.filter((v) => v.level === 'trip');
      return say(false, 'ブレーカーが落ちたため、コピーしませんでした。', ...trips.flatMap((v) => [v.rule, v.why]));
    }
    const ok = await copyText(res.cmd);
    say(ok, ok ? 'コマンドをコピーしました。自分のターミナルに貼り付けて実行してください。'
               : 'コピーできませんでした。「コマンドで言うと」を開いて、文字を選んでコピーしてください。');
  });
})();
