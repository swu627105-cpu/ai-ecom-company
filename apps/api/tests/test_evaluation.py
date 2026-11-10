import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.store import Store
from app.evaluation import create_router


def test_rule_evaluation_reports_actual_five_case_outcomes():
    store = Store(persist=False)
    app = FastAPI()
    app.include_router(create_router(store))
    with TestClient(app) as client:
        response = client.post('/api/evaluation', json={'mode': 'rules'})
        assert response.status_code == 201
        rid = response.json()['id']
        for _ in range(100):
            report = next(r for r in client.get('/api/evaluation').json() if r['id'] == rid)
            if report['status'] == 'completed': break
            time.sleep(.01)
        assert report['status'] == 'completed'
        assert len(report['results']) == 5
        assert all(r['correct_outcome'] for r in report['results'])
        assert report['summary'][0]['model_responses'] == 0
        assert not store.demo_runs and not store.tasks
