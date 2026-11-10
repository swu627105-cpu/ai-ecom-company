"""工具层（脚手架）：把「拍板通过」变成一次可回放的执行。

现在用时间推进模拟真实调用；接店铺接口时，把 refresh() 换成真实的工具调用即可，
上层（审批、任务、事件、审计）不用改。
"""
from __future__ import annotations

import re
from datetime import datetime

from .domain import Approval, Step, Task

STEP_SECONDS = 1.1

PLANS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (re.compile(r"价|折扣|促销|预售"), "price",
     ["校验库存与价格锁定", "生成改价快照", "模拟提交店铺平台", "校验前台价与价保规则"]),
    (re.compile(r"补货|采购|库存|备货|交期"), "stock",
     ["核对可用现金与周转", "生成采购单", "模拟通知供应商锁价", "登记交期与到货计划"]),
    (re.compile(r"预算|投放|广告"), "ads",
     ["模拟核对投放数据", "模拟生成预算方案", "模拟调整投放", "模拟检查调整结果"]),
    (re.compile(r"素材|合规|话术|文案|脚本"), "content",
     ["生成合规检查清单", "改写文案并去除极限词", "模拟提交平台审核", "回收审核结果"]),
]
FALLBACK = ("generic", ["读取当前配置", "生成执行方案", "模拟调用工具执行", "校验结果并回写"])


def classify(title: str) -> tuple[str, list[str]]:
    for pattern, kind, steps in PLANS:
        if pattern.search(title):
            return kind, steps
    return FALLBACK


def snapshot(kind: str, title: str) -> dict:
    """执行前快照，用于回滚。真实环境里这里存平台返回的原始对象。"""
    if kind == "price":
        return {"kind": "price", "note": "模拟任务，无真实店铺快照"}
    if kind == "stock":
        return {"kind": "stock", "note": "模拟任务，无真实采购单"}
    if kind == "content":
        return {"kind": "content", "note": "模拟任务，无真实内容改动"}
    return {"kind": kind, "note": "模拟任务，没有真实业务快照"}


def create_task(approval: Approval) -> Task:
    kind, steps = classify(approval.title)
    task = Task(
        id=f"T-{approval.id[-6:]}",
        title=approval.title,
        kind=kind,
        **{"from": approval.from_},
        approval_id=approval.id,
        risk=approval.risk,
        steps=[Step(text=s) for s in steps],
        snapshot=snapshot(kind, approval.title),
    )
    task.steps[0].status = "running"
    task.steps[0].at = _hm(task.created_ts)
    return task


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def refresh(task: Task, now_ts: float | None = None) -> bool:
    """按经过时间推进步骤；返回是否有变化。"""
    if task.status != "running":
        return False
    now_ts = now_ts if now_ts is not None else datetime.now().timestamp()
    elapsed = max(0.0, now_ts - task.created_ts)
    done = min(len(task.steps), int(elapsed / STEP_SECONDS))
    changed = False
    for i, step in enumerate(task.steps):
        want = "done" if i < done else ("running" if i == done else "wait")
        if step.status != want:
            step.status = want  # type: ignore[assignment]
            if want != "wait" and not step.at:
                step.at = _hm(task.created_ts + i * STEP_SECONDS)
            changed = True
    if done >= len(task.steps):
        task.status = "done"
        task.finished_at = _hm(task.created_ts + len(task.steps) * STEP_SECONDS)
        changed = True
    return changed


def rollback(task: Task) -> bool:
    if task.status != "done":
        return False
    task.status = "failed"
    return True
