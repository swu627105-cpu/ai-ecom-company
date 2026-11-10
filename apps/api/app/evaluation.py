"""固定合成案例上的可复现实验。所有运行保留原始结果，不预填模型优胜结论。"""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
from .competition import DemoService, PRESETS, execute_tool, metrics, rule_plan, stamp, validate_plan, verification
from .domain import new_id
from .store import Store
from . import llm


class EvaluationIn(BaseModel):
    mode: Literal["rules", "live"] = "rules"


def create_router(store):
    router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])
    jobs = set()
    for report in store.evaluations.values():
        if report["status"] == "running":
            report["status"] = "interrupted"
    store.save()

    async def evaluate(report):
        arms = ["rules"] if report["mode"] == "rules" else ["rules", "single_model", "planner_reviewer"]
        try:
            for case in PRESETS:
                for arm in arms:
                    if report["status"] != "running":
                        return
                    started = time.monotonic()
                    expected = case["id"] != "cash"
                    row = {"case": case["id"], "case_title": case["title"], "arm": arm, "expected_executable": expected,
                           "input_hash": hashlib.sha256(json.dumps(case["data"], sort_keys=True).encode()).hexdigest()[:16],
                           "model_calls": [], "error": "", "proposal_valid": False, "executable": False, "checks": []}
                    try:
                        isolated = Store(persist=False)
                        service = DemoService(isolated)
                        run = {"id": "eval", "before": copy.deepcopy(case["data"]), "current": copy.deepcopy(case["data"]), "trace": [], "mode": "rehearsal" if arm == "rules" else "live", "model": llm.current()["model"], "memory": [], "model_calls": [], "input_hash": row["input_hash"], "node": "plan"}
                        isolated.demo_runs["eval"] = run
                        state = {"run_id": "eval"}
                        await service.plan(state)
                        row["plan"] = copy.deepcopy(run["plan"])
                        row["proposal_valid"] = True
                        projected = copy.deepcopy(case["data"])
                        for action in run["plan"]["actions"]:
                            execute_tool(action, projected, "evaluation")
                        checks = verification(case["data"], projected)
                        if arm == "planner_reviewer":
                            await service.review(state)
                            executable = run["review"]["safe"]
                            checks = run["preflight"]
                            row["review"] = run["review"]
                        else:
                            executable = all(c["passed"] for c in checks)
                        row.update(executable=executable, checks=checks, projected_metrics=metrics(projected))
                    except Exception as exc:
                        row["error"] = str(exc)[:500]
                    finally:
                        row["model_calls"] = run["model_calls"]
                        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        # API/解析错误不算正确拦截；资金不足案例需正确拒绝，其他案例需可执行。
                        row["correct_outcome"] = not row["error"] and row["executable"] == expected
                        report["results"].append(row)
                        report["progress"] = f"{len(report['results'])}/{len(PRESETS) * len(arms)}"
                        store.save()
            if report["status"] == "running":
                report["status"] = "completed"
        except Exception as exc:
            report.update(status="failed", error=str(exc)[:500])
        finally:
            report["finished_at"] = stamp()
            report["summary"] = []
            for arm in arms:
                rows = [r for r in report["results"] if r["arm"] == arm]
                calls = [c for r in rows for c in r["model_calls"]]
                report["summary"].append({"arm": arm, "cases": len(rows), "correct_outcomes": sum(r["correct_outcome"] for r in rows),
                    "invalid_proposals": sum(not r["proposal_valid"] for r in rows), "errors": sum(bool(r["error"]) for r in rows),
                    "mean_seconds": round(sum(r["elapsed_seconds"] for r in rows) / max(1, len(rows)), 3),
                    "model_responses": sum(c.get("output") is not None for c in calls), "model_requests": len(calls), "reported_tokens": sum(c.get("usage", {}).get("total_tokens", 0) for c in calls)})
            store.save()

    @router.get("")
    def reports():
        return list(store.evaluations.values())[::-1]

    @router.post("", status_code=201)
    async def start(body: EvaluationIn):
        if any(r["status"] == "running" for r in store.evaluations.values()) or any(r["status"] in ("running", "executing") for r in store.demo_runs.values()):
            raise HTTPException(409, "已有分析或评测在运行，请稍后再试")
        if body.mode == "live" and not llm.enabled():
            raise HTTPException(409, "请先配置书生模型")
        rid = new_id("EVAL")
        report = {"id": rid, "created_at": stamp(), "status": "running", "mode": body.mode, "model": llm.current()["model"] if body.mode == "live" else "",
                  "dataset": "synthetic-ecommerce-v2", "planner_version": "v4-calculated-planner-and-reviewer", "scope": "5个合成案例；正确结果率不是实际经营收益；所有组均有工具边界校验", "results": [], "summary": [], "progress": "0"}
        store.evaluations[rid] = report
        store.save()
        job = asyncio.create_task(evaluate(report))
        jobs.add(job)
        job.add_done_callback(jobs.discard)
        return report

    @router.post("/{report_id}/cancel")
    async def cancel(report_id: str):
        report = store.evaluations.get(report_id)
        if not report:
            raise HTTPException(404, "评测不存在")
        if report["status"] != "running":
            raise HTTPException(409, "评测已结束")
        report["status"] = "cancelled"
        store.save()
        return report

    return router
