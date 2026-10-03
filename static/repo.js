// ツールを切り替えても、いま見ている表示を保つ。
(function () {
  const select = document.getElementById('repo-switch');
  if (!select) return;
  select.addEventListener('change', () => {
    const suffix = select.dataset.repoTab === 'worktrees' ? '/worktrees' : '';
    location.href = '/repo/' + encodeURIComponent(select.value) + suffix;
  });
})();
