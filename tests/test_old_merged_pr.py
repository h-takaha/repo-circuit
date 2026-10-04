"""保存した実例を使う。実ブランチ・GitHub書き込み・live設定には依存しない。"""
import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
import respx

from app.actions import Actions
from app.circuit import layout
from app.explain import parts, summary
from app.github import GitHubSource, MERGED_LOOKUP_PAGE_SIZE, MERGED_LOOKUP_MAX_PAGES
from app.guards import evaluate
from app.model import LocalBranch
from app.pipeline import rows
from app.service import Service

EVIDENCE = json.loads((Path(__file__).parent / 'fixtures/old_merged_pr.json').read_text(encoding='utf-8'))
PR = EVIDENCE['pull']
NAME, HEAD = PR['head']['ref'], PR['head']['sha']
REPO = 'investment-tracker'
API = f'https://api.github.com/repos/h-takaha/{REPO}'


def mock_repo(router, tip=HEAD, lookup_status=200, lookup=None):
    main = {'sha': 'main-tip', 'commit': {'message': 'main'}}
    router.get('https://api.github.com/user/repos').respond(200, json=[
        {'name': REPO, 'owner': {'login': 'h-takaha'}}])
    router.get(API).respond(200, json={'default_branch': 'main'})
    router.get(f'{API}/commits').respond(200, json=[main])
    router.get(f'{API}/branches').respond(200, json=[
        {'name': 'main', 'commit': {'sha': 'main-tip'}}, {'name': NAME, 'commit': {'sha': tip}}])
    branch = router.get(f'{API}/branches/codex%2Fllm-dispatcher-ocr').respond(200, json={'commit': {'sha': tip}})
    for ref in (NAME, HEAD, 'a' * 40):
        router.get(f'{API}/compare/main...{ref}').respond(200, json={
            'ahead_by': 1, 'behind_by': 10, 'merge_base_commit': main,
            'commits': [{'sha': tip, 'commit': {'message': 'feature'}}]})
    router.get(f'{API}/issues').respond(200, json=[])
    recent = [{**copy.deepcopy(PR), 'number': n, 'head': {'ref': f'other-{n}', 'sha': f'head-{n}'}}
              for n in range(54, 24, -1)]
    router.get(f'{API}/pulls', params={'state': 'closed', 'per_page': 30}).respond(200, json=recent)
    opened = router.get(f'{API}/pulls', params={'state': 'open'}).respond(200, json=[])
    lookup_route = router.get(f'{API}/pulls', params={'state': 'closed', 'head': f'h-takaha:{NAME}', 'page': 1}).respond(
        lookup_status, json=([PR] if lookup is None else lookup) if lookup_status == 200 else {'message': 'Unavailable'})
    return branch, opened, lookup_route


def run(coro):
    return asyncio.run(coro)


def guard_ids(v):
    return {x.id for x in evaluate('delete_branch', {'name': NAME}, v)}


def test_old_pr_outside_recent_thirty_is_shared_across_all_surfaces():
    with respx.mock(assert_all_called=False) as router:
        _, _, lookup = mock_repo(router)
        src = GitHubSource('fixture', 'h-takaha')
        v = run(src.repo_view(REPO))
        assert len(v.closed_pulls) == 31 and v.merged_for(NAME).number == 8
        assert summary(v)['branches'] == 0
        assert not any(h['part'] == f'branch:{NAME}' for h in layout(v).hits)
        assert '合流済み' in parts(v)[f'branch:{NAME}']['body']
        assert 'create_pr' not in {a.get('action') for a in parts(v)[f'branch:{NAME}']['actions']}
        assert next(r for r in rows(v) if r['name'] == NAME)['merged']
        assert not guard_ids(v)
        run(src.repo_view(REPO))
        assert lookup.call_count == 1  # targeted lookup also uses the existing TTL
        params = lookup.calls[0].request.url.params
        assert params['head'] == f'h-takaha:{NAME}' and params['base'] == 'main'


@pytest.mark.parametrize('change', ['new_head', 'open', 'unpushed', 'dirty', 'other_base', 'fork', 'unmerged'])
def test_old_pr_does_not_hide_new_or_unverified_work(change):
    with respx.mock(assert_all_called=False) as router:
        candidate = copy.deepcopy(PR)
        if change == 'other_base':
            candidate['base']['ref'] = 'other'
        elif change == 'fork':
            candidate['head']['repo']['full_name'] = 'someone/investment-tracker'
        elif change == 'unmerged':
            candidate['merged_at'] = None
        _, opened, _ = mock_repo(router, tip='a' * 40 if change == 'new_head' else HEAD, lookup=[candidate])
        if change == 'open':
            new = {**candidate, 'number': 55, 'state': 'open', 'merged_at': None}
            opened.respond(200, json=[new])
            router.get(f'{API}/pulls/55').respond(200, json=new)
            router.get(f'{API}/commits/{HEAD}/check-runs').respond(200, json={'check_runs': []})
        v = run(GitHubSource('fixture', 'h-takaha').repo_view(REPO))
        if change in ('unpushed', 'dirty'):
            v.local.dev_branch = NAME
            v.local.branches = [LocalBranch(NAME, HEAD, unpushed=int(change == 'unpushed'), on_github=True)]
            v.local.dev_uncommitted = int(change == 'dirty')
        assert not v.completed_branch(NAME)
        assert any(h['part'] == f'branch:{NAME}' for h in layout(v).hits)
        assert any(r['name'] == NAME and not r.get('merged') for r in rows(v))
        assert guard_ids(v) & {'delete_unmerged', 'delete_open_pr', 'delete_local_work'}


@pytest.mark.parametrize('status', [403, 404, 500])
def test_supplement_failure_is_visible_and_never_allows_deletion(status):
    with respx.mock(assert_all_called=False) as router:
        mock_repo(router, lookup_status=status)
        v = run(GitHubSource('fixture', 'h-takaha').repo_view(REPO))
        assert v.merged_for(NAME) is None and 'delete_unknown' in guard_ids(v)
        assert '確認できません' in parts(v)[f'branch:{NAME}']['body']
        assert next(s for s in rows(v)[0]['stages'] if s['key'] == 'merge')['text'] == '取得不可'


def test_head_filtered_pagination_finds_second_page_and_stops():
    with respx.mock(assert_all_called=False) as router:
        _, _, first = mock_repo(router, lookup=[{**PR, 'merged_at': None}] * MERGED_LOOKUP_PAGE_SIZE)
        second = router.get(f'{API}/pulls', params={'state': 'closed', 'head': f'h-takaha:{NAME}', 'page': 2}).respond(200, json=[PR])
        v = run(GitHubSource('fixture', 'h-takaha').repo_view(REPO))
        assert v.merged_for(NAME).number == 8 and first.call_count == second.call_count == 1


def test_pagination_budget_exhaustion_is_unknown_not_merged():
    with respx.mock(assert_all_called=False) as router:
        mock_repo(router, lookup=[{**PR, 'merged_at': None}] * MERGED_LOOKUP_PAGE_SIZE)
        for page in range(2, MERGED_LOOKUP_MAX_PAGES + 1):
            router.get(f'{API}/pulls', params={'state': 'closed', 'head': f'h-takaha:{NAME}', 'page': page}).respond(
                200, json=[{**PR, 'merged_at': None}] * MERGED_LOOKUP_PAGE_SIZE)
        v = run(GitHubSource('fixture', 'h-takaha').repo_view(REPO))
        assert 'delete_unknown' in guard_ids(v)


@pytest.mark.parametrize('change', ['head', 'open', 'lookup_failure', 'branch_missing', 'bad_compare'])
def test_actions_recheck_after_preview_and_never_delete_new_work(settings, change):
    from dataclasses import replace

    with respx.mock(assert_all_called=False) as router:
        branch, opened, lookup = mock_repo(router)
        deletion = router.delete(f'{API}/git/refs/heads/{NAME}').respond(204)
        src = GitHubSource('fixture', 'h-takaha')
        actions = Actions(Service(replace(settings, github_token='fixture'), src), None)
        params = {'repo': REPO, 'name': NAME}
        assert not run(actions.check('delete_branch', params))['tripped']
        if change == 'head':
            branch.respond(200, json={'commit': {'sha': 'a' * 40}})
        elif change == 'open':
            opened.respond(200, json=[{**PR, 'number': 55, 'state': 'open', 'merged_at': None}])
        elif change == 'lookup_failure':
            lookup.respond(500, json={'message': 'Unavailable'})
        elif change == 'bad_compare':
            router.get(f'{API}/compare/main...{HEAD}').respond(200, json={})
        else:
            branch.respond(404, json={'message': 'Not Found'})
        result = run(actions.run('delete_branch', params, ack=True))
        assert result['tripped'] and not result['ok'] and not deletion.called
        assert branch.call_count == 2


def test_delete_preview_fetches_exact_sha_comparison_and_preserves_local_work(settings, monkeypatch):
    from dataclasses import replace
    from app.model import LocalState

    with respx.mock(assert_all_called=False) as router:
        mock_repo(router)
        compare = router.get(f'{API}/compare/main...{HEAD}').respond(200, json={
            'ahead_by': 1, 'behind_by': 10, 'commits': [], 'merge_base_commit': {'sha': 'base'}})
        monkeypatch.setattr('app.service.read_local_state', lambda *args: LocalState(
            dev_exists=True, dev_branch=NAME, dev_uncommitted=1))
        actions = Actions(Service(replace(settings, github_token='fixture'), GitHubSource('fixture', 'h-takaha')), None)
        result = run(actions.check('delete_branch', {'repo': REPO, 'name': NAME}))
        assert compare.called and result['tripped']
        assert 'delete_local_work' in {v['id'] for v in result['verdicts']}


def test_fresh_lookup_does_not_mutate_or_reuse_display_cache():
    with respx.mock(assert_all_called=False) as router:
        _, _, lookup = mock_repo(router)
        src = GitHubSource('fixture', 'h-takaha')
        assert run(src.repo_view(REPO)).merged_for(NAME)
        lookup.respond(500, json={'message': 'Unavailable'})
        assert run(src.repo_view_for_delete(REPO, NAME)).merged_for(NAME) is None
        assert run(src.repo_view(REPO)).merged_for(NAME)  # display retains its bounded TTL
        assert lookup.call_count == 2


def test_open_pr_beyond_global_limit_overrides_even_recent_merged_evidence():
    with respx.mock(assert_all_called=False) as router:
        _, opened, lookup = mock_repo(router)
        router.get(f'{API}/pulls', params={'state': 'closed', 'per_page': 30}).respond(200, json=[PR])
        other = {**PR, 'state': 'open', 'merged_at': None, 'head': {**PR['head'], 'ref': 'other'}}
        for n in range(100, 150):
            router.get(f'{API}/pulls/{n}').respond(200, json={**other, 'number': n})
        target = {**PR, 'number': 55, 'state': 'open', 'merged_at': None}
        opened.mock(side_effect=lambda request: httpx.Response(200, json=
                    [target] if request.url.params.get('head') else [{**other, 'number': n} for n in range(100, 150)]))
        router.get(f'{API}/pulls/55').respond(200, json=target)
        router.get(f'{API}/commits/{HEAD}/check-runs').respond(200, json={'check_runs': []})
        v = run(GitHubSource('fixture', 'h-takaha').repo_view(REPO))
        assert v.pull_for(NAME).number == 55 and not v.completed_branch(NAME)
        assert 'delete_open_pr' in guard_ids(v) and not lookup.called


@pytest.mark.parametrize('work, rule', [(True, 'delete_local_work'), (None, 'delete_unknown')])
def test_targeted_local_safety_evidence_overrides_capped_or_failed_generic_scan(settings, monkeypatch, work, rule):
    from dataclasses import replace
    from app.model import LocalState

    with respx.mock(assert_all_called=False) as router:
        mock_repo(router)
        deletion = router.delete(f'{API}/git/refs/heads/{NAME}').respond(204)
        monkeypatch.setattr('app.service.read_local_state', lambda *args: LocalState(dev_exists=True))
        monkeypatch.setattr('app.service.read_branch_work', lambda *args: work)
        actions = Actions(Service(replace(settings, github_token='fixture'), GitHubSource('fixture', 'h-takaha')), None)
        result = run(actions.run('delete_branch', {'repo': REPO, 'name': NAME}, ack=True))
        assert result['tripped'] and rule in {v['id'] for v in result['verdicts']}
        assert not deletion.called
