import pytest

from app.circuit import layout, BUS_Y, _main_x
from app.explain import parts, summary, status_text
from app.guards import evaluate
from app.model import Branch, Commit, LocalBranch, LocalState, PullRequest, RepoView, now
from app.pipeline import rows


@pytest.fixture
def merged_view():
    commits = [Commit(s, s, now()) for s in ('base', 'oldmain', 'merge')]
    return RepoView('motion-deck', 'main', commits,
                    [Branch('work', 'head', 1, 1, [Commit('head', 'feature', now())], 'base', now())], [], [],
                    closed_pulls=[PullRequest(7, 'feature', '', 'work', 'main', 'closed', True, False,
                                              'head', 'none', 'unknown', merge_commit_sha='merge')],
                    local=LocalState(dev_exists=True, dev_branch='main', dev_head='oldmain',
                                     run_exists=True, run_head='merge'))


def test_merged_head_has_one_completed_row_and_no_active_wire(merged_view):
    v = merged_view
    rs = [r for r in rows(v) if r['name'] == 'work']
    assert len(rs) == 1 and rs[0].get('merged') and rs[0]['next'] == '完了'
    assert not any(h['part'] == 'branch:work' for h in layout(v).hits)
    p = parts(v)['branch:work']
    assert '合流済み' in p['body']
    assert 'create_pr' not in [a.get('action') for a in p['actions']]
    assert 'work' in parts(v)['cleanup']['body']
    assert summary(v)['branches'] == 0 and 'すべて幹線に合流済み' in status_text(v)
    assert 'delete_unmerged' not in {x.id for x in evaluate('delete_branch', {'name': 'work'}, v)}
    assert 'pr_no_diff' in {x.id for x in evaluate('create_pr', {'head': 'work'}, v)}


@pytest.mark.parametrize('change', ['github', 'unpushed', 'dirty', 'open', 'other_base'])
def test_reused_branch_keeps_new_work(merged_view, change):
    v = merged_view
    if change == 'github':
        v.branches[0].sha = 'newhead'
    elif change == 'other_base':
        v.closed_pulls[0].base = 'other'
    elif change == 'open':
        v.pulls.append(PullRequest(8, 'new', '', 'work', 'main', 'open', False, False,
                                  'head', 'success', 'clean'))
    else:
        v.local.dev_branch, v.local.dev_head = 'work', 'head'
        v.local.branches = [LocalBranch('work', 'head', on_github=True)]
        if change == 'unpushed':
            v.local.branches[0].unpushed = 1
            v.local.branches[0].head = 'localnew'
        else:
            v.local.dev_uncommitted = 1
    active = [r for r in rows(v) if r['name'] == 'work' and not r.get('merged')]
    assert len(active) == 1
    assert any(h['part'] == 'branch:work' for h in layout(v).hits)
    if change in ('unpushed', 'dirty'):
        assert 'スイッチを付ける' not in active[0]['next']
        assert next(s for s in active[0]['stages'] if s['key'] == 'merge')['state'] == 'off'
        assert 'delete_local_work' in {x.id for x in evaluate('delete_branch', {'name': 'work'}, v)}


@pytest.mark.parametrize('head, lag', [('oldmain', 1), ('merge', 0), ('outside', None)])
def test_clean_dev_marker_shows_real_position(merged_view, head, lag):
    v = merged_view
    v.local.dev_head = head
    s = layout(v)
    assert 'dev' in {h['part'] for h in s.hits}
    text = next(t for t in s.texts if t['t'].startswith('作業台'))
    if lag is not None:
        assert text['x'] == _main_x(2 - lag, 3)
        assert str(lag) in text['t'] if lag else '先端' in text['t']
    else:
        assert '表示範囲外' in text['t']
    run = next(t for t in s.texts if t['t'] == '本番はここまで')
    assert abs(text['y'] - run['y']) >= 20
    assert '未送電' not in text['t']
    assert head in parts(v)['dev']['body']


def test_local_only_dirty_branch_is_retained(merged_view):
    v = merged_view
    v.local.dev_branch, v.local.dev_uncommitted = 'new-work', 2
    v.local.branches = [LocalBranch('new-work', v.local.dev_head)]
    assert any(h['part'] == 'local:new-work' for h in layout(v).hits)
    assert any(t['t'] == '作業台のみ · 未コミット 2' for t in layout(v).texts)


def test_zero_ahead_does_not_turn_an_old_same_name_pr_into_current_evidence(merged_view):
    v = merged_view
    v.branches[0].sha, v.branches[0].ahead = 'different-head-already-on-main', 0
    assert v.merged_for('work') is None and not v.completed_branch('work')
    assert sum(r['name'] == 'work' for r in rows(v)) == 1
    assert not next(r for r in rows(v) if r['name'] == 'work').get('merged')
