// 部品トレイ: 「付箋を貼る」ボタンの下に、いまの入力で走る gh コマンドを見せる（commands.py の create_issue と同じ形）。
(function () {
  const form = document.querySelector('form.tform');
  const out = document.getElementById('issue-cmd');
  if (!form || !out) return;
  function update() {
    const val = (name) => (form.elements[name] && form.elements[name].value) || '';
    const labels = ['feedback', val('kind') || 'improvement', val('priority') || 'p2'].join(',');
    out.textContent = `gh issue create -R h-takaha/${val('repo')} -t "${val('title')}" -l ${labels}`;
  }
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
})();
