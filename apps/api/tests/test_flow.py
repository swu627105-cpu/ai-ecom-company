"""端到端：开会 → 现场意见 → 拍板 → 派发 → 审批 → 执行 → 回滚。"""
import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def drain_agenda(mid: str) -> int:
    """把当前议题的发言全部读完，返回读到的条数。"""
    n = 0
    for _ in range(50):
        r = client.post(f"/api/meetings/{mid}/next")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["line"] is None:
            return n
        n += 1
    raise AssertionError("议题发言没有收敛")


def test_health_and_seed():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True

    agents = client.get("/api/agents").json()
    assert len(agents) == 12
    assert all(a["stance"] for a in agents)

    assert len(client.get("/api/templates").json()) == 4


def test_full_meeting_flow():
    created = client.post("/api/meetings", json={"template": "promo"}).json()
    mid = created["id"]
    assert created["stage"] == "ready"
    assert created["who"] == ["ceo", "ops", "ad", "fin", "sc", "wms", "cs"]

    assert client.post(f"/api/meetings/{mid}/start").json()["stage"] == "run"

    total_says = 0
    decisions = 0
    for round_ in range(5):
        total_says += drain_agenda(mid)

        # 现场发言：两条 AI 回应，且落到纪要
        note = client.post(
            f"/api/meetings/{mid}/say",
            json={"text": "就按 369 上预售，但库存要留住，退货率一超 6% 就回调"},
        ).json()["note"]
        assert len(note["replies"]) == 2
        assert {r[0] for r in note["replies"]} <= set(created["who"])

        meeting = client.get(f"/api/meetings/{mid}").json()
        agenda = meeting["agenda"][meeting["idx"]]
        r = client.post(f"/api/meetings/{mid}/decide", json={"v": agenda["opts"][0]["v"]})
        assert r.status_code == 200, r.text
        decisions += 1
        if r.json()["stage"] == "done":
            break
    else:
        raise AssertionError("会议没有走完")

    assert decisions == 3
    expected_says = sum(len(a["says"]) for a in created["agenda"])
    assert total_says == expected_says

    meeting = client.get(f"/api/meetings/{mid}").json()
    assert meeting["stage"] == "done"
    assert len(meeting["res"]) == 3
    assert len(meeting["notes"]) == 3
    assert meeting["cost"] > 0

    before = len(client.get("/api/approvals").json())
    fin = client.post(f"/api/meetings/{mid}/finish").json()
    assert len(fin["actions"]) == 3
    after = client.get("/api/approvals").json()
    assert len(after) == before + 3

    card = after[0]
    assert card["status"] == "pending"
    assert any("现场意见" in r for r in card["evidence"]["reasons"])

    # 重复派发要挡住，避免重复发卡
    assert client.post(f"/api/meetings/{mid}/finish").status_code == 409

    ok = client.post(f"/api/approvals/{card['id']}/decide", json={"approve": True})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.post(f"/api/approvals/{card['id']}/decide", json={"approve": True}).status_code == 409

    texts = [e["text"] for e in client.get("/api/events").json()]
    assert any("结束" in t for t in texts)
    assert any("已通过" in t for t in texts)

    # 审批通过 → 生成任务 → 按步执行 → 完成 → 可回滚
    tasks = client.get("/api/tasks").json()
    assert tasks and tasks[0]["approval_id"] == card["id"]
    task = tasks[0]
    assert len(task["steps"]) >= 4
    assert task["snapshot"] is not None

    time.sleep(1.1 * (len(task["steps"]) + 1))
    done = next(t for t in client.get("/api/tasks").json() if t["id"] == task["id"])
    assert done["status"] == "done"
    assert all(s["status"] == "done" for s in done["steps"])
    assert done["finished_at"]

    rb = client.post(f"/api/tasks/{task['id']}/rollback")
    assert rb.status_code == 200 and rb.json()["status"] == "failed"
    assert client.post(f"/api/tasks/{task['id']}/rollback").status_code == 409

    audits = client.get("/api/audit").json()
    assert any(x["action"] == "task.done" for x in audits)
    assert any(x["action"] == "task.rollback" for x in audits)
    assert len({x["hash"] for x in audits}) == len(audits)


def test_guard_rails():
    m = client.post("/api/meetings", json={"template": "refund", "who": ["cs", "rev"]}).json()
    # 未开始不能发言
    assert client.post(f"/api/meetings/{m['id']}/say", json={"text": "hi"}).status_code == 409
    # 只有一个人不行
    assert client.post("/api/meetings", json={"template": "refund", "who": ["cs"]}).status_code == 400
    # 未知模板
    assert client.post("/api/meetings", json={"template": "nope"}).status_code == 400
    # 未拍完不能派发
    client.post(f"/api/meetings/{m['id']}/start")
    assert client.post(f"/api/meetings/{m['id']}/finish").status_code == 409
    # 空发言
    assert client.post(f"/api/meetings/{m['id']}/say", json={"text": "  "}).status_code == 400


def test_mock_shop_and_staff_configuration():
    staff = client.get('/api/staff').json()
    assert 'ops' in staff
    changed = {'hired': True, 'owner': '运营主管', 'level': 1}
    assert client.put('/api/staff/ops', json=changed).json() == changed
    assert client.get('/api/staff').json()['ops'] == changed
    assert client.put('/api/staff/unknown', json=changed).status_code == 404
    assert client.put('/api/staff/ops', json={**changed, 'level': 5}).status_code == 400
    assert client.post('/api/shops', json={'platform': '未知', 'name': '演示'}).status_code == 400
    shop = client.post('/api/shops', json={'platform': '抖音', 'name': '演示店铺'}).json()
    assert shop['status'] == '模拟授权'
    assert any(s['id'] == shop['id'] for s in client.get('/api/shops').json())
    assert client.delete('/api/shops/' + shop['id']).status_code == 200
    assert client.delete('/api/shops/' + shop['id']).status_code == 404


def test_scenario_analysis_to_execution(monkeypatch):
    from app import llm
    async def mock_chat(messages, **kwargs):
        assert '240 件' in messages[1]['content']
        return '{"headline":"库存覆盖 4 天", "findings":["库存 240 件", "日均 60 件"], "actions":[{"text":"核实补货", "owner":"sc", "why":"库存仅够 4 天"}]}'
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'chat', mock_chat)
    cases = client.get('/api/scenarios').json()
    assert {c['id'] for c in cases} == {'stock', 'ads', 'refund'}
    assert client.post('/api/scenarios/nope/analyze').status_code == 404
    result = client.post('/api/scenarios/stock/analyze').json()
    assert result['source'] == 'model' and result['data']['scenario_id'] == 'stock'
    card = client.post('/api/scenarios/stock/dispatch', json={**result['actions'][0], 'source': result['source'], 'model': result['model']}).json()
    assert card['evidence']['metrics'] == cases[0]['metrics']
    assert client.post(f"/api/approvals/{card['id']}/decide", json={'approve': True}).status_code == 200
    task = next(t for t in client.get('/api/tasks').json() if t['approval_id'] == card['id'])
    assert task['status'] == 'running'
    assert any('模拟' in step['text'] for step in task['steps'])
    monkeypatch.setattr(llm, 'enabled', lambda: False)
    fallback = client.post('/api/scenarios/refund/analyze').json()
    assert fallback['source'] == 'rules' and '5%' in fallback['headline']


def test_custom_meeting_has_topic_based_agenda_and_history():
    response = client.post('/api/meetings', json={'template': 'custom', 'who': ['ceo', 'fin'], 'topic': '库存资金优先级'})
    assert response.status_code == 201
    meeting = response.json()
    assert meeting['title'] == '临时会 · 库存资金优先级'
    assert len(meeting['agenda']) == 1
    mid = meeting['id']
    client.post(f'/api/meetings/{mid}/start')
    drain_agenda(mid)
    decided = client.post(f'/api/meetings/{mid}/decide', json={'v': 'verify'})
    assert decided.json()['stage'] == 'done'
    assert any(m['id'] == mid for m in client.get('/api/meetings').json())
    assert client.post('/api/meetings', json={'template': 'custom', 'who': ['ceo', 'fin'], 'topic': ''}).status_code == 422


def test_unhired_employee_cannot_join_and_duplicates_rejected():
    from app.main import store
    original = store.staff['ad'].copy()
    try:
        store.staff['ad']['hired'] = False
        assert client.post('/api/meetings', json={'template': 'promo', 'who': ['ceo', 'ad']}).status_code == 409
        assert client.post('/api/meetings', json={'template': 'promo', 'who': ['ceo', 'ceo']}).status_code == 400
    finally:
        store.staff['ad'] = original


def test_supervisor_routing_and_red_line_boundary():
    from app.main import store
    from app.domain import Approval, Evidence, new_id
    old = store.staff['ad'].copy()
    try:
        client.put('/api/organization', json={'mode': 'team'})
        store.staff['ad']['owner'] = '投放主管'
        red = Approval(id=new_id('AP'), title='红线测试', **{'from': 'ad'}, summary='test', impact='test', cost=0, risk='red', evidence=Evidence(summary='test'))
        mid = red.model_copy(update={'id': new_id('AP'), 'risk': 'mid'})
        store.approvals.extend([red, mid])
        assert client.post(f'/api/approvals/{red.id}/decide', json={'approve': True, 'actor': '投放主管'}).status_code == 403
        assert client.post(f'/api/approvals/{mid.id}/decide', json={'approve': True, 'actor': '客服主管'}).status_code == 403
        assert client.post(f'/api/approvals/{mid.id}/decide', json={'approve': True, 'actor': '投放主管'}).status_code == 200
    finally:
        store.staff['ad'] = old
        client.put('/api/organization', json={'mode': 'solo'})


def test_approval_question_preserves_decision_and_records_source():
    from app.main import store
    pending = next(a for a in store.approvals if a.status == 'pending')
    result = client.post(f'/api/approvals/{pending.id}/ask', json={'question': '有什么风险？'})
    assert result.status_code == 200
    assert result.json()['status'] == 'pending'
    assert result.json()['discussion'][-1]['source'] == 'rules'
