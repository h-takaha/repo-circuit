from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.worktrees import FileSummary, Integration, WorktreeRow, WorktreeSnapshot
from app.model import PullRequest


def render_snapshot(snapshot):
    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / 'templates'),
                      autoescape=select_autoescape(['html']))
    return env.get_template('worktrees.html').render(
        snapshot=snapshot, repo_name='example', repo_url=None, names=['example', 'other'],
        repo_tab='worktrees', active='circuit', asset=lambda name: '/static/' + name)


def test_worktree_template_preserves_all_rows_and_unknown_counts():
    rows = [WorktreeRow(path=f'C:/work/{i}', is_primary=i == 0) for i in range(15)]
    rows[0].files = FileSummary(tracked=0, untracked=None, ignored=3, state='partial')
    html = render_snapshot(WorktreeSnapshot(rows=rows, state='partial'))
    assert '登録15件：主作業場所1件・追加14件' in html
    assert html.count('<article ') == 15
    assert '<strong>0<small> 件</small></strong>' in html
    assert '<strong>未取得</strong>' in html
    assert '一部取得' in html
    assert '該当なし' in html and 'パス例は未取得' in html


def test_worktree_template_escapes_paths_reasons_samples_and_evidence():
    attack = '<script>alert("x")</script>'
    row = WorktreeRow(path=attack, branch=attack, locked=True, locked_reason=attack,
                      manager_reason=attack, notes=[attack],
                      files=FileSummary(tracked=1, tracked_samples=[attack]),
                      integration=Integration(source=attack, notes=[attack]))
    html = render_snapshot(WorktreeSnapshot(rows=[row], notes=[attack]))
    assert attack not in html
    assert '&lt;script&gt;' in html
    assert 'circuit.js' not in html and 'parts-data' not in html and 'id="breaker"' not in html
    assert 'repo.js' in html
    assert 'href="/repo/example/worktrees" aria-current="page"' in html
    assert 'href="/repo/example"' in html
    assert '?sel=' not in html
    assert '<details' in html and '<summary>' in html


def test_unknown_empty_snapshot_does_not_claim_no_registrations():
    html = render_snapshot(WorktreeSnapshot(state='unknown'))
    assert '登録一覧を取得できていません' in html
    assert '登録件数は未取得' in html and '登録0件' not in html
    assert '登録された作業場所はありません' not in html


def test_worktree_template_shows_receipt_head_base_and_merge_shas():
    receipt = PullRequest(3, 'merged', '', 'feature', 'trunk', 'closed', True, False,
                          'a' * 40, 'none', 'unknown', merge_commit_sha='b' * 40,
                          base_sha='c' * 40)
    row = WorktreeRow(path='C:/work/example', integration=Integration(pull=receipt))
    html = render_snapshot(WorktreeSnapshot(rows=[row]))
    assert 'a' * 40 in html and 'b' * 40 in html and 'c' * 40 in html
    assert 'trunk · ' in html


def test_repo_pages_have_native_tabs_and_separate_scripts(client):
    circuit = client.get('/repo/event-calendar')
    worktrees = client.get('/repo/event-calendar/worktrees')
    assert circuit.status_code == worktrees.status_code == 200
    assert 'href="/repo/event-calendar" aria-current="page"' in circuit.text
    assert 'href="/repo/event-calendar/worktrees" aria-current="page"' in worktrees.text
    assert 'data-repo-tab="circuit"' in circuit.text
    assert 'data-repo-tab="worktrees"' in worktrees.text
    assert 'circuit.js' in circuit.text and 'circuit.js' not in worktrees.text
    assert 'id="breaker"' in circuit.text and 'id="breaker"' not in worktrees.text


def test_shared_switch_preserves_tab_using_native_navigation():
    # Execute the actual handler with a tiny DOM stub, without a browser dependency.
    import subprocess
    script = Path(__file__).resolve().parents[1] / 'static' / 'repo.js'
    runner = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
for (const tab of ['circuit', 'worktrees']) {
  let handler;
  const select = { value: 'a b', dataset: {repoTab: tab}, addEventListener: (_, fn) => handler = fn };
  const context = { document: {getElementById: () => select}, location: {} };
  vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
  handler();
  assert.equal(context.location.href, '/repo/a%20b' + (tab === 'worktrees' ? '/worktrees' : ''));
}
'''
    subprocess.run(['node', '-e', runner, str(script)], check=True)
