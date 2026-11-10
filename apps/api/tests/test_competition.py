"""比赛演示的行为验证：权限门、模型审查、真实沙箱变更、失败隔离与快照恢复。"""
import copy
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import competition as c
from app import llm
from app.store import Store


@pytest.fixture
def demo(monkeypatch):
    from app import main
    store = Store(persist=False)
    app = FastAPI()
    app.include_router(c.create_router(store))
    monkeypatch.setattr(main, 'store', store)
    app.add_api_route('/api/approvals/{aid}/decide', main.decide_approval, methods=['POST'])
    app.add_api_route('/api/approvals/{aid}/ask', main.ask_approval, methods=['POST'])
    app.add_api_route('/api/tasks', main.list_tasks, methods=['GET'])
    app.add_api_route('/api/tasks/{tid}/rollback', main.rollback_task, methods=['POST'])
    with TestClient(app) as client:
        yield client, store


def until(client, run_id, statuses):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = client.get(f'/api/demo/runs/{run_id}').json()
        if result['status'] in statuses:
            return result
        time.sleep(.02)
    raise AssertionError(f"运行未到达预期状态 {statuses}: {result}")


def test_click_through_approval_execution_and_rollback(demo):
    client, store = demo
    run = client.post('/api/demo/runs', json={'mode': 'rehearsal'}).json()
    rid = run['id']
    pending = until(client, rid, {'awaiting_approval'})
    assert pending['before'] == pending['current']
    assert pending['receipts'] == [] and pending['model_calls'] == []
    assert client.post(f'/api/demo/runs/{rid}/rollback').status_code == 409
    assert client.post(f'/api/demo/runs/{rid}/approve').status_code == 200
    done = until(client, rid, {'completed'})
    assert done['current_metrics']['coverage_days'] == 8
    assert done['current_metrics']['budget_total'] == 4000
    assert done['current_metrics']['acquisition_budget'] == 2100
    assert done['current_metrics']['retention_budget'] == 1900
    assert done['current_metrics']['ticket_count'] == 30
    assert done['current_metrics']['refund_rate'] == 5
    assert done['current_metrics']['spent'] == 480
    assert done['current_metrics']['estimated_roi'] == 3.308
    assert len(done['receipts']) == 3 and all(x['passed'] for x in done['checks'])
    assert client.post(f'/api/demo/runs/{rid}/approve').status_code == 409
    exported = client.get(f'/api/demo/runs/{rid}/export').json()
    assert exported['run']['input_hash'] == done['input_hash']
    second = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'auto_approve': True}).json()
    next_run = until(client, second['id'], {'completed'})
    assert next_run['memory'][0]['run_id'] == rid
    rolled = client.post(f'/api/demo/runs/{rid}/rollback').json()
    assert rolled['current'] == rolled['before'] and rolled['rollback_verified']
    assert client.post(f'/api/demo/runs/{rid}/rollback').status_code == 409
    # 第二次独立沙箱不受第一轮回滚影响。
    assert client.get(f"/api/demo/runs/{second['id']}").json()['current_metrics']['stock'] == 480


def test_live_mode_has_two_real_gateway_calls(demo, monkeypatch):
    client, store = demo
    calls = []
    async def fake_chat(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(c.REHEARSAL_PLAN, ensure_ascii=False)
        return '{"safe":true,"summary":"方案符合数据与权限约束","concerns":["ROI仅为估算"]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake_chat)
    run = client.post('/api/demo/runs', json={'mode': 'live', 'auto_approve': True}).json()
    done = until(client, run['id'], {'completed', 'failed'})
    assert done['status'] == 'completed', done.get('error')
    assert len(calls) == 2 and len(done['model_calls']) == 2
    assert 'tools' in json.loads(calls[0][1]['content'])
    assert json.loads(calls[1][1]['content'])['plan']['actions']


def test_reviewer_veto_cannot_be_approved(demo, monkeypatch):
    client, store = demo
    count = 0
    async def fake_chat(messages, **kwargs):
        nonlocal count
        count += 1
        return json.dumps(c.REHEARSAL_PLAN) if count == 1 else '{"safe":false,"summary":"需补充交期核验","concerns":[]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake_chat)
    run = client.post('/api/demo/runs', json={'mode': 'live', 'auto_approve': True}).json()
    blocked = until(client, run['id'], {'blocked'})
    assert blocked['current'] == blocked['before']
    assert client.post(f"/api/demo/runs/{run['id']}/approve").status_code == 409


def test_invalid_model_tool_fails_without_fallback_or_side_effect(demo, monkeypatch):
    client, store = demo
    plan = copy.deepcopy(c.REHEARSAL_PLAN)
    plan['actions'][0]['tool'] = 'bank.transfer'
    async def fake_chat(messages, **kwargs):
        return json.dumps(plan)
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake_chat)
    run = client.post('/api/demo/runs', json={'mode': 'live', 'auto_approve': True}).json()
    failed = until(client, run['id'], {'failed'})
    assert failed['mode'] == 'live'
    assert failed['current'] == failed['before'] and not failed['receipts']


@pytest.mark.parametrize('tool,args', [
    ('inventory.transfer', {'quantity': 361}),
    ('inventory.transfer', {'quantity': -1}),
    ('inventory.transfer', {'quantity': True}),
    ('ads.rebalance', {'amount': 901}),
    ('ads.rebalance', {'amount': 100, 'account': 'other'}),
    ('service.create_tickets', {'count': 31}),
    ('system.shell', {'command': 'anything'}),
])
def test_tool_boundary_rejects_without_mutating(tool, args):
    data = copy.deepcopy(c.SEED)
    with pytest.raises(ValueError):
        c.execute_tool({'tool': tool, 'arguments': args}, data, 'guard')
    assert data == c.SEED


def test_partial_failure_can_restore_snapshot(demo, monkeypatch):
    client, store = demo
    original = c.execute_tool
    def fail_on_second(action, data, rid):
        if rid != 'preview' and action['tool'] == 'ads.rebalance':
            raise RuntimeError('模拟工具异常')
        return original(action, data, rid)
    monkeypatch.setattr(c, 'execute_tool', fail_on_second)
    run = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'auto_approve': True}).json()
    failed = until(client, run['id'], {'failed'})
    assert len(failed['receipts']) == 1 and failed['current_metrics']['stock'] == 480
    restored = client.post(f"/api/demo/runs/{run['id']}/rollback").json()
    assert restored['current'] == restored['before']


def test_rescue_uses_original_approval_and_task_routes(demo):
    client, store = demo
    run = client.post('/api/demo/runs', json={'mode': 'rehearsal'}).json()
    waiting = until(client, run['id'], {'awaiting_approval'})
    card = next(a for a in store.approvals if a.id == waiting['approval_id'])
    assert '三岗位联合方案' in card.title
    assert client.post(f'/api/approvals/{card.id}/decide', json={'approve': True}).status_code == 200
    done = until(client, run['id'], {'completed'})
    tasks = client.get('/api/tasks').json()
    task = next(t for t in tasks if t['demo_run_id'] == run['id'])
    assert task['status'] == 'done' and all(s['status'] == 'done' for s in task['steps'])
    assert len([t for t in tasks if t['approval_id'] == card.id]) == 1
    assert client.post(f"/api/tasks/{task['id']}/rollback").status_code == 200
    restored = client.get(f"/api/demo/runs/{run['id']}").json()
    assert restored['current'] == restored['before']
    assert any('[经营救援]' in e.text for e in store.events)


def test_original_approval_reject_keeps_business_state_unchanged(demo):
    client, store = demo
    run = client.post('/api/demo/runs', json={'mode': 'rehearsal'}).json()
    waiting = until(client, run['id'], {'awaiting_approval'})
    assert client.post(f"/api/approvals/{waiting['approval_id']}/decide", json={'approve': False}).status_code == 200
    rejected = client.get(f"/api/demo/runs/{run['id']}").json()
    assert rejected['status'] == 'rejected'
    assert rejected['current'] == rejected['before'] and not rejected['receipts']


def test_model_json_format_can_retry_without_changing_business_state(demo, monkeypatch):
    client, store = demo
    attempts = 0
    async def fake_chat(messages, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return '{"headline":"broken" actions:[]}'
        if attempts == 2:
            return json.dumps(c.REHEARSAL_PLAN)
        return '{"safe":true,"summary":"模型复核通过","concerns":[]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake_chat)
    run = client.post('/api/demo/runs', json={'mode': 'live'}).json()
    waiting = until(client, run['id'], {'awaiting_approval'})
    assert len(waiting['model_calls']) == 3
    assert waiting['model_calls'][0]['output']['parse_error']
    assert waiting['before'] == waiting['current'] and waiting['receipts'] == []


@pytest.mark.parametrize('case,tools', [('stock', ['inventory.transfer']), ('ads', ['ads.rebalance']), ('refund', ['service.create_tickets'])])
def test_each_variable_case_only_uses_needed_tools(demo, case, tools):
    client, store = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'preset': case, 'auto_approve': True}).json()
    done = until(client, r['id'], {'completed', 'failed', 'blocked'})
    assert done['status'] == 'completed', done.get('review')
    assert [a['tool'] for a in done['plan']['actions']] == tools
    assert all(c['passed'] for c in done['checks'])
    card = next(a for a in store.approvals if a.id == done['approval_id'])
    assert '1项业务方案' in card.title
    assert ('创建售后工单' in card.impact) == (case == 'refund')


def test_cash_shortage_revision_preserves_old_plan_and_requires_new_approval(demo):
    client, store = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'preset': 'cash'}).json()
    blocked = until(client, r['id'], {'blocked'})
    assert not blocked['receipts'] and blocked['current'] == blocked['before']
    assert client.post(f"/api/demo/runs/{r['id']}/approve").status_code == 409
    data = copy.deepcopy(blocked['before'])
    data['finance']['available_cash'] = 1000
    revised = client.post(f"/api/demo/runs/{r['id']}/revise", json={'reason': '补充资金后重算', 'data': data})
    assert revised.status_code == 200
    waiting = until(client, r['id'], {'awaiting_approval'})
    assert waiting['version'] == 2 and len(waiting['versions']) == 2
    assert waiting['versions'][0]['data']['finance']['available_cash'] == 100
    assert waiting['versions'][0]['review']['safe'] is False
    assert not waiting['receipts']
    old_card = waiting['approval_id']
    client.post(f"/api/demo/runs/{r['id']}/revise", json={'reason': '再次核算'})
    again = until(client, r['id'], {'awaiting_approval'})
    assert again['approval_id'] != old_card
    assert next(a for a in store.approvals if a.id == old_card).status == 'rejected'
    assert client.post(f'/api/approvals/{old_card}/decide', json={'approve': True}).status_code == 409


def test_feedback_next_round_has_updated_state_and_no_duplicate_tickets(demo):
    client, _ = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'auto_approve': True}).json()
    done = until(client, r['id'], {'completed'})
    feedback = {'fulfilled_orders': 60, 'next_daily_sales': 80, 'acquisition_spend': 1000, 'acquisition_gmv': 2000, 'retention_spend': 1000, 'retention_gmv': 5000, 'resolved_tickets': 10}
    observed = client.post(f"/api/demo/runs/{r['id']}/feedback", json=feedback).json()
    assert observed['current']['inventory']['stock'] == 420
    assert observed['current']['ads']['acquisition_roi'] == 2
    assert observed['current_metrics']['refund_rate'] == 5
    assert sum(t['status'] == '已解决' for t in observed['current']['service']['tickets']) == 10
    assert client.post(f"/api/demo/runs/{r['id']}/feedback", json=feedback).status_code == 409
    child = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'parent_id': r['id'], 'auto_approve': True}).json()
    blocked = until(client, child['id'], {'blocked'})
    assert blocked['before'] == observed['current']
    updated = copy.deepcopy(blocked['before'])
    updated['inventory']['backup_stock'] = 360
    client.post(f"/api/demo/runs/{child['id']}/revise", json={'reason': '模拟备用仓新增到货240件', 'data': updated})
    next_round = until(client, child['id'], {'completed'})
    assert next_round['before']['inventory']['daily_sales'] == 80
    assert next_round['current_metrics']['coverage_days'] == 8
    assert not any(a['tool'] == 'service.create_tickets' for a in next_round['plan']['actions'])
    assert client.post('/api/demo/runs', json={'mode': 'rehearsal', 'parent_id': r['id']}).status_code == 409
    rolled = client.post(f"/api/demo/runs/{r['id']}/rollback").json()
    assert rolled['current'] == done['before']
    assert client.get(f"/api/demo/runs/{child['id']}").json()['status'] == 'completed'


def test_checkpoint_resume_never_replays_committed_transfer(demo, monkeypatch):
    client, store = demo
    original = c.execute_tool
    fail = True
    def flaky(action, data, rid):
        if fail and rid != 'preview' and action['tool'] == 'ads.rebalance':
            raise RuntimeError('临时工具中断')
        return original(action, data, rid)
    monkeypatch.setattr(c, 'execute_tool', flaky)
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'auto_approve': True}).json()
    interrupted = until(client, r['id'], {'failed'})
    assert interrupted['cursor'] == 1 and len(interrupted['receipts']) == 1
    fail = False
    assert client.post(f"/api/demo/runs/{r['id']}/resume").status_code == 200
    resumed = until(client, r['id'], {'completed'})
    assert resumed['current_metrics']['stock'] == 480
    assert len(resumed['receipts']) == 3
    assert len([t for t in store.tasks if t.demo_run_id == r['id']]) == 1


def test_hiring_and_level_enforced_before_approval(demo):
    client, store = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal'}).json()
    until(client, r['id'], {'awaiting_approval'})
    store.staff['sc']['level'] = 1
    assert client.post(f"/api/demo/runs/{r['id']}/approve").status_code == 409
    assert not store.demo_runs[r['id']]['receipts']
    store.staff['sc']['level'] = 2
    store.staff['sc']['hired'] = False
    client.post(f"/api/demo/runs/{r['id']}/revise", json={'reason': '重新核查岗位'})
    blocked = until(client, r['id'], {'blocked'})
    assert any(not c['passed'] and 'sc' in c['name'] for c in blocked['preflight'])


@pytest.mark.parametrize('change', ['negative', 'zero_sales', 'bad_orders', 'extra', 'boolean'])
def test_invalid_business_data_is_rejected_before_run(demo, change):
    client, store = demo
    data = copy.deepcopy(c.SEED)
    if change == 'negative': data['finance']['available_cash'] = -1
    elif change == 'zero_sales': data['inventory']['daily_sales'] = 0
    elif change == 'bad_orders': data['service']['damaged_orders'] = 51
    elif change == 'extra': data['inventory']['external_account'] = 'unknown'
    elif change == 'boolean': data['inventory']['stock'] = True
    before = len(store.demo_runs)
    assert client.post('/api/demo/runs', json={'mode': 'rehearsal', 'data': data}).status_code == 422
    assert len(store.demo_runs) == before


def test_shop_memory_isolation(demo):
    client, store = demo
    store.shops = [{'id': 'shop-a'}, {'id': 'shop-b'}]
    a = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'shop_id': 'shop-a', 'auto_approve': True}).json()
    until(client, a['id'], {'completed'})
    b = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'shop_id': 'shop-b'}).json()
    waiting = until(client, b['id'], {'awaiting_approval'})
    assert waiting['memory'] == []
    assert client.post('/api/demo/runs', json={'mode': 'rehearsal', 'shop_id': 'missing'}).status_code == 404


def test_healthy_data_uses_zero_tools_and_can_verify(demo):
    client, _ = demo
    data = copy.deepcopy(c.SEED)
    data['inventory']['stock'] = 600
    data['ads']['acquisition_roi'] = 4
    data['service']['damaged_orders'] = 0
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'data': data, 'auto_approve': True}).json()
    done = until(client, r['id'], {'completed'})
    assert done['plan']['actions'] == [] and done['before'] == done['current']


def test_feedback_invalid_values_do_not_mutate(demo):
    client, _ = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'auto_approve': True}).json()
    done = until(client, r['id'], {'completed'})
    data = {'fulfilled_orders': 9999, 'next_daily_sales': 60, 'acquisition_spend': 0, 'acquisition_gmv': 1, 'retention_spend': 0, 'retention_gmv': 0, 'resolved_tickets': 0}
    assert client.post(f"/api/demo/runs/{r['id']}/feedback", json=data).status_code == 422
    assert client.get(f"/api/demo/runs/{r['id']}").json()['current'] == done['current']


def test_model_semantic_error_replans_using_calculator_and_records_bad_output(demo, monkeypatch):
    client, _ = demo
    calls = []
    invalid = copy.deepcopy(c.REHEARSAL_PLAN)
    invalid['actions'][0]['arguments']['quantity'] = 99999
    async def fake(messages, **kwargs):
        calls.append(json.loads(messages[1]['content']))
        if len(calls) == 1: return json.dumps(invalid)
        if len(calls) == 2: return json.dumps(c.REHEARSAL_PLAN)
        return '{"safe":true,"summary":"方案已修正","concerns":[]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake)
    r = client.post('/api/demo/runs', json={'mode': 'live'}).json()
    waiting = until(client, r['id'], {'awaiting_approval'})
    assert calls[0]['calculator']['inventory']['minimum_transfer'] == 180
    assert 'validation_feedback' in calls[1]
    assert waiting['corrections'][0]['invalid_plan'] == invalid
    assert not waiting['receipts'] and waiting['before'] == waiting['current']


def test_model_network_failure_retries_once_without_rules_fallback(demo, monkeypatch):
    client, _ = demo
    requests = 0
    async def timeout(*args, **kwargs):
        nonlocal requests
        requests += 1
        raise TimeoutError('测试超时')
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', timeout)
    r = client.post('/api/demo/runs', json={'mode': 'live'}).json()
    failed = until(client, r['id'], {'failed'})
    assert requests == 2 and len(failed['model_calls']) == 2
    assert all('error' in c for c in failed['model_calls'])
    assert failed['mode'] == 'live' and failed['plan'] is None


def test_raw_reviewer_response_is_not_overwritten_by_deterministic_veto(demo, monkeypatch):
    client, _ = demo
    calls = 0
    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps(c.rule_plan(next(p['data'] for p in c.PRESETS if p['id'] == 'cash')))
        return '{"safe":true,"summary":"原始模型意见","concerns":[]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake)
    r = client.post('/api/demo/runs', json={'mode': 'live', 'preset': 'cash'}).json()
    blocked = until(client, r['id'], {'blocked'})
    assert blocked['review']['safe'] is False
    assert blocked['model_calls'][-1]['output']['safe'] is True
    assert blocked['model_calls'][-1]['output']['summary'] == '原始模型意见'


def test_review_receives_real_tool_check_evidence(demo, monkeypatch):
    client, _ = demo
    calls = []
    async def fake(messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        calls.append(payload)
        if len(calls) == 1: return json.dumps(c.REHEARSAL_PLAN)
        assert payload['calculator']['inventory']['arrival_in_time'] is True
        assert all(check['passed'] for check in payload['tool_checks'])
        assert payload['projected_metrics']['coverage_days'] == 8
        return '{"safe":true,"summary":"核对工具证据后通过","concerns":[]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake)
    r = client.post('/api/demo/runs', json={'mode': 'live'}).json()
    waiting = until(client, r['id'], {'awaiting_approval'})
    assert waiting['review']['safe'] is True


def test_approval_question_receives_actual_business_plan_and_cost(demo, monkeypatch):
    client, _ = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal'}).json()
    waiting = until(client, r['id'], {'awaiting_approval'})
    async def fake(messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        context = payload['business_context']
        assert context['plan']['actions'][0]['arguments']['quantity'] == 240
        assert context['projected_metrics']['spent'] == 480
        assert context['data']['finance']['spent'] == 0
        return '调拨240件，费用480元，覆盖8天。'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake)
    result = client.post(f"/api/approvals/{waiting['approval_id']}/ask", json={'question': '调拨费用是多少？'})
    assert result.status_code == 200
    assert '480' in result.json()['discussion'][-1]['answer']
    assert result.json()['status'] == 'pending'


def test_live_feedback_context_counts_open_tickets_as_already_created(demo, monkeypatch):
    client, _ = demo
    r = client.post('/api/demo/runs', json={'mode': 'rehearsal', 'auto_approve': True}).json()
    until(client, r['id'], {'completed'})
    client.post(f"/api/demo/runs/{r['id']}/feedback", json={'fulfilled_orders': 60, 'next_daily_sales': 70, 'acquisition_spend': 1000, 'acquisition_gmv': 2000, 'retention_spend': 500, 'retention_gmv': 2500, 'resolved_tickets': 10})
    calls = 0
    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        payload = json.loads(messages[1]['content'])
        if calls == 1:
            tickets = payload['data']['service']['tickets']
            assert tickets['already_created'] == 30
            assert tickets['pending_contact'] == 20 and tickets['resolved'] == 10 and tickets['not_created'] == 0
            return json.dumps({'headline': '只处理库存和投放', 'findings': ['库存覆盖6天，调拨120件', '30笔工单已经创建，不能重复'], 'actions': [{'tool': 'inventory.transfer', 'arguments': {'quantity': 120}, 'why': '资源范围内补充库存'}, {'tool': 'ads.rebalance', 'arguments': {'amount': 900}, 'why': '复购ROI更高'}]})
        return '{"safe":true,"summary":"符合工具检查","concerns":[]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', fake)
    child = client.post('/api/demo/runs', json={'mode': 'live', 'parent_id': r['id'], 'auto_approve': True}).json()
    done = until(client, child['id'], {'completed'})
    assert len(done['receipts']) == 2 and done['current_metrics']['ticket_count'] == 30
    assert done['current_metrics']['coverage_days'] > 7
