"""书生 × LangGraph：比赛沙箱。模型规划、人工审批、真实本地状态变更及可逆验证。"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from datetime import datetime
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from langgraph.graph import END, START, StateGraph

from . import llm
from .domain import Approval, Evidence, Step, Task, new_id, now_hm

SEED = {
    "inventory": {"sku": "SKU-101 收纳柜", "stock": 240, "daily_sales": 60, "lead_days": 7,
                  "backup_stock": 360, "transfer_days": 1, "transfer_unit_cost": 2},
    "ads": {"acquisition_budget": 3000, "retention_budget": 1000, "acquisition_roi": 2.5,
            "retention_roi": 4.2, "breakeven_roi": 3.5},
    "service": {"orders": 1000, "refund_orders": 50, "previous_refund_rate": 2,
                "damaged_orders": 30, "tickets": []},
    "finance": {"available_cash": 20000, "spent": 0},
}
TOOLS = [
    {"name": "inventory.transfer", "owner": "供应链 AI", "description": "从备用仓调拨到模拟店铺仓，沙箱立即入库；现实交期按本次数据；需在断货前到达", "arguments": {"quantity": "正整数，不超过本轮备用库存和可支付数量"}},
    {"name": "ads.rebalance", "owner": "投放 AI", "description": "从拉新预算移到复购预算，总预算不变，上限 900 元", "arguments": {"amount": "整数 1~900"}},
    {"name": "service.create_tickets", "owner": "客服 AI", "description": "为破损退款单创建排查工单，不自动退款或承诺根因", "arguments": {"count": "正整数，不超过未建工单的破损单数"}},
]
REHEARSAL_PLAN = {
    "headline": "先补足交期覆盖，再调整低效预算并排查破损退款",
    "findings": ["240 ÷ 60 = 4 天库存，低于 7 天交期；需处理 3 天缺口。",
                 "拉新 ROI 2.5 低于保本线 3.5，复购 ROI 4.2；迁移预算仅能提供静态估算。",
                 "50 / 1000 = 5% 退款率；30 单涉及破损，根因待工单排查。"],
    "actions": [
        {"tool": "inventory.transfer", "arguments": {"quantity": 240}, "why": "备用仓可调拨 360 件，调拨 240 件后覆盖 8 天，现实交期 1 天。"},
        {"tool": "ads.rebalance", "arguments": {"amount": 900}, "why": "迁移拉新预算的 30%，总预算保持 4000 元。"},
        {"tool": "service.create_tickets", "arguments": {"count": 30}, "why": "逐单核查 30 单破损问题，不直接推断包装根因。"},
    ],
}


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InventoryInput(InputModel):
    sku: str = Field(default="SKU-101 收纳柜", min_length=1, max_length=80)
    stock: int = Field(ge=0, le=1000000)
    daily_sales: int = Field(gt=0, le=100000)
    lead_days: int = Field(gt=0, le=90)
    backup_stock: int = Field(ge=0, le=1000000)
    transfer_days: int = Field(gt=0, le=90)
    transfer_unit_cost: float | int = Field(gt=0, le=10000)


class AdsInput(InputModel):
    acquisition_budget: int = Field(ge=0, le=10000000)
    retention_budget: int = Field(ge=0, le=10000000)
    acquisition_roi: float | int = Field(ge=0, le=100)
    retention_roi: float | int = Field(ge=0, le=100)
    breakeven_roi: float | int = Field(gt=0, le=100)

    @model_validator(mode="after")
    def nonzero(self):
        if self.acquisition_budget + self.retention_budget <= 0:
            raise ValueError("广告总预算必须大于零")
        return self


class ServiceInput(InputModel):
    orders: int = Field(gt=0, le=10000000)
    refund_orders: int = Field(ge=0, le=10000000)
    previous_refund_rate: float | int = Field(ge=0, le=100)
    damaged_orders: int = Field(ge=0, le=10000)
    tickets: list[dict] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def consistent(self):
        if not self.damaged_orders <= self.refund_orders <= self.orders:
            raise ValueError("必须满足破损单数 ≤ 退款单数 ≤ 总订单数")
        if len(self.tickets) > self.damaged_orders:
            raise ValueError("工单数不能超过破损订单数")
        if any(set(t) != {"id", "reason", "status"} or not all(isinstance(v, str) and len(v) <= 200 for v in t.values()) for t in self.tickets):
            raise ValueError("工单只能包含 id、reason、status 字符串")
        if len({t["id"] for t in self.tickets}) != len(self.tickets):
            raise ValueError("工单 ID 不可重复")
        return self


class FinanceInput(InputModel):
    available_cash: float | int = Field(ge=0, le=100000000)
    spent: float | int = Field(default=0, ge=0, le=100000000)


class BusinessInput(InputModel):
    inventory: InventoryInput
    ads: AdsInput
    service: ServiceInput
    finance: FinanceInput


PRESETS = []
for key, title, changes in [
    ("rescue", "大促联合救援", {}),
    ("stock", "库存告急", {"inventory": {"stock": 120, "daily_sales": 40, "lead_days": 6}, "ads": {"acquisition_roi": 4.0}, "service": {"damaged_orders": 0}}),
    ("ads", "广告亏损", {"inventory": {"stock": 600}, "ads": {"acquisition_roi": 1.8, "retention_roi": 4.8}, "service": {"damaged_orders": 0}}),
    ("refund", "退款异常", {"inventory": {"stock": 600}, "ads": {"acquisition_roi": 4.0}, "service": {"refund_orders": 80, "damaged_orders": 48}}),
    ("cash", "资金不足 · 风控拦截", {"finance": {"available_cash": 100}}),
]:
    sample = copy.deepcopy(SEED)
    for section, values in changes.items():
        sample[section].update(values)
    PRESETS.append({"id": key, "title": title, "data": sample})


def rule_plan(data: dict) -> dict:
    """公开的确定性基线。按当前数据计算，不伪装成模型结果。"""
    i, a, service = data["inventory"], data["ads"], data["service"]
    actions = []
    if i["stock"] < i["daily_sales"] * i["lead_days"]:
        desired = i["daily_sales"] * (i["lead_days"] + 1) - i["stock"]
        qty = min(desired, i["backup_stock"], int(data["finance"]["available_cash"] // i["transfer_unit_cost"]))
        if qty > 0:
            actions.append({"tool": "inventory.transfer", "arguments": {"quantity": qty}, "why": f"按当前销量补到交期加1天；资源允许{qty}件"})
    if a["acquisition_roi"] < a["breakeven_roi"] and a["retention_roi"] > a["acquisition_roi"] and a["acquisition_budget"]:
        amount = min(900, a["acquisition_budget"], max(1, int(a["acquisition_budget"] * .3)))
        actions.append({"tool": "ads.rebalance", "arguments": {"amount": amount}, "why": "向当前ROI较高计划迁移预算，总额不变"})
    count = service["damaged_orders"] - len(service["tickets"])
    if count:
        actions.append({"tool": "service.create_tickets", "arguments": {"count": count}, "why": f"为尚未建单的{count}笔破损单创建排查工单"})
    m = metrics(data)
    return {"headline": f"按本轮数据处理{len(actions)}类经营动作", "findings": [
        f"库存{m['coverage_days']}天，常规交期{i['lead_days']}天，备用仓交期{i['transfer_days']}天",
        f"拉新ROI {a['acquisition_roi']}，复购ROI {a['retention_roi']}，迁移限额900元",
        f"退款率{m['refund_rate']}%，待补工单{count}笔；现金{data['finance']['available_cash']}元"], "actions": actions}


def stamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="milliseconds")


def approval_labels(plan: dict) -> tuple[str, str]:
    names = {"inventory.transfer": "调拨库存", "ads.rebalance": "调整广告预算", "service.create_tickets": "创建售后工单"}
    actions = [names[a["tool"]] for a in plan["actions"]]
    title = "三岗位联合方案" if len(actions) == 3 else f"{len(actions)}项业务方案"
    impact = "批准后在本次独立沙箱执行：" + "、".join(actions) if actions else "本轮无需业务变更，批准后仅核验当前状态"
    return "大促经营救援 · " + title, impact


def metrics(data: dict) -> dict:
    i, a, s = data["inventory"], data["ads"], data["service"]
    total = a["acquisition_budget"] + a["retention_budget"]
    return {
        "stock": i["stock"], "coverage_days": round(i["stock"] / i["daily_sales"], 2),
        "lead_gap_days": round(max(0, i["lead_days"] - i["stock"] / i["daily_sales"]), 2),
        "budget_total": total, "acquisition_budget": a["acquisition_budget"], "retention_budget": a["retention_budget"],
        "estimated_roi": round((a["acquisition_budget"] * a["acquisition_roi"] + a["retention_budget"] * a["retention_roi"]) / total, 3),
        "ticket_count": len(s["tickets"]), "damaged_orders": s["damaged_orders"],
        "refund_rate": round(s["refund_orders"] / s["orders"] * 100, 2),
        "spent": data["finance"]["spent"],
    }


def decision_context(data: dict) -> dict:
    i, a, service = data["inventory"], data["ads"], data["service"]
    minimum = max(0, i["daily_sales"] * i["lead_days"] - i["stock"])
    affordable = int(data["finance"]["available_cash"] // i["transfer_unit_cost"])
    maximum = min(i["backup_stock"], affordable)
    return {"inventory": {"needs_transfer": minimum > 0, "minimum_transfer": minimum,
        "preferred_transfer": max(0, i["daily_sales"] * (i["lead_days"] + 1) - i["stock"]) if minimum else 0,
        "maximum_transfer": maximum, "resources_sufficient": minimum <= maximum,
        "arrival_in_time": i["stock"] / i["daily_sales"] >= i["transfer_days"]},
        "ads": {"needs_rebalance": a["acquisition_roi"] < a["breakeven_roi"] and a["retention_roi"] > a["acquisition_roi"] and a["acquisition_budget"] > 0, "maximum_move": min(900, a["acquisition_budget"])},
        "service": {"uncovered_damaged_orders": service["damaged_orders"] - len(service["tickets"])}}


def model_data(data: dict) -> dict:
    """业务摘要减少长工单列表噪声，同时保留完整原始快照供审计。"""
    result = copy.deepcopy(data)
    tickets = data["service"]["tickets"]
    result["service"]["tickets"] = {
        "already_created": len(tickets),
        "pending_contact": sum(t["status"] != "已解决" for t in tickets),
        "resolved": sum(t["status"] == "已解决" for t in tickets),
        "not_created": data["service"]["damaged_orders"] - len(tickets),
        "meaning": "待人工联系、已解决都是已创建工单，不得重复建单，只能为not_created创建新单"}
    return result


def plan_issues(plan: dict, data: dict) -> list[str]:
    projected = copy.deepcopy(data)
    for action in plan["actions"]:
        execute_tool(action, projected, "preview")
    # 资源充足但方案遗漏动作时，让模型根据校验结果修正；资源不足交由风控拦截。
    context = decision_context(data)
    issues = []
    if context["inventory"]["resources_sufficient"] and projected["inventory"]["stock"] < data["inventory"]["daily_sales"] * data["inventory"]["lead_days"]:
        issues.append(f"遗漏或不足的库存动作，至少调拨{context['inventory']['minimum_transfer']}件")
    if len(projected["service"]["tickets"]) != data["service"]["damaged_orders"]:
        issues.append(f"需为剩余{context['service']['uncovered_damaged_orders']}单创建工单")
    if context["ads"]["needs_rebalance"] and not any(a["tool"] == "ads.rebalance" for a in plan["actions"]):
        issues.append("遗漏低效投放预算调整")
    return issues


def validate_action(action: dict, data: dict) -> None:
    name, args = action.get("tool"), action.get("arguments")
    allowed = {t["name"] for t in TOOLS}
    if name not in allowed or not isinstance(args, dict):
        raise ValueError("方案包含未授权工具或无效参数")
    keys = {"inventory.transfer": "quantity", "ads.rebalance": "amount", "service.create_tickets": "count"}
    key = keys[name]
    if set(args) != {key} or type(args[key]) is not int or args[key] <= 0:
        raise ValueError(f"{name} 参数须为正整数，且只能包含 {key}")
    value = args[key]
    if name == "inventory.transfer":
        if value > data["inventory"]["backup_stock"] or value * data["inventory"]["transfer_unit_cost"] > data["finance"]["available_cash"]:
            raise ValueError("调拨超过备用库存或可用现金")
    elif name == "ads.rebalance":
        if value > min(900, data["ads"]["acquisition_budget"]):
            raise ValueError("预算迁移超过 900 元授权上限")
    elif value > data["service"]["damaged_orders"] - len(data["service"]["tickets"]):
        raise ValueError(f"本轮破损{data['service']['damaged_orders']}单，已经创建{len(data['service']['tickets'])}张（包括待联系与已解决），只能新增{data['service']['damaged_orders'] - len(data['service']['tickets'])}张；禁止重复建单")


def validate_plan(plan: dict, data: dict) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("headline"), str) or not plan["headline"].strip():
        raise ValueError("模型规划缺少结论")
    actions = plan.get("actions")
    findings = plan.get("findings")
    if not isinstance(actions, list) or len(actions) > 3 or not isinstance(findings, list) or not all(isinstance(f, str) for f in findings) or len(findings) < 2:
        raise ValueError("模型规划需包含至少两条发现，工具动作最多三个")
    if any(not isinstance(a, dict) for a in actions) or len({a.get("tool") for a in actions}) != len(actions):
        raise ValueError("每个工具最多调用一次，动作必须是对象")
    for action in actions:
        validate_action(action, data)
        if not isinstance(action.get("why"), str) or not action["why"].strip():
            raise ValueError("每个工具动作需附依据")
    return plan


def execute_tool(action: dict, data: dict, run_id: str) -> dict:
    """只调用本地白名单工具；原地改变当前运行的独立沙箱。"""
    validate_action(action, data)
    name, args = action["tool"], action["arguments"]
    if name == "inventory.transfer":
        qty = args["quantity"]
        cost = qty * data["inventory"]["transfer_unit_cost"]
        data["inventory"]["stock"] += qty
        data["inventory"]["backup_stock"] -= qty
        data["finance"]["available_cash"] -= cost
        data["finance"]["spent"] += cost
        return {"transfer_id": f"TR-{run_id}", "quantity": qty, "cost": cost, "sandbox_received": True, "real_world_lead_days": data["inventory"]["transfer_days"]}
    if name == "ads.rebalance":
        amount = args["amount"]
        data["ads"]["acquisition_budget"] -= amount
        data["ads"]["retention_budget"] += amount
        return {"moved": amount, "total_budget": data["ads"]["acquisition_budget"] + data["ads"]["retention_budget"]}
    count = args["count"]
    offset = len(data["service"]["tickets"])
    data["service"]["tickets"] += [{"id": f"CS-{run_id}-{offset + n + 1:02}", "reason": "物流破损待排查", "status": "待人工联系"} for n in range(count)]
    return {"created": count, "status": "待人工联系", "refunds_executed": 0}


def verification(before: dict, after: dict) -> list[dict]:
    b, a = metrics(before), metrics(after)
    return [
        {"name": "库存覆盖不低于采购交期", "passed": after["inventory"]["stock"] >= after["inventory"]["daily_sales"] * after["inventory"]["lead_days"], "detail": f"{b['coverage_days']} → {a['coverage_days']} 天；要求 ≥ {after['inventory']['lead_days']} 天"},
        {"name": "广告总预算保持不变", "passed": b["budget_total"] == a["budget_total"], "detail": f"{b['budget_total']} → {a['budget_total']} 元"},
        {"name": "破损订单已建排查工单", "passed": a["ticket_count"] == a["damaged_orders"], "detail": f"{a['ticket_count']} / {a['damaged_orders']} 单；历史退款率仍为 {a['refund_rate']}%"},
        {"name": "预算加权ROI估算不下降", "passed": a["estimated_roi"] >= b["estimated_roi"], "detail": f"{b['estimated_roi']} → {a['estimated_roi']}，不代表真实收益"},
        {"name": "库存与资金守恒", "passed": before["inventory"]["stock"] + before["inventory"]["backup_stock"] == after["inventory"]["stock"] + after["inventory"]["backup_stock"] and after["finance"]["available_cash"] >= 0 and abs(before["finance"]["available_cash"] - after["finance"]["available_cash"] - (a["spent"] - b["spent"])) < .001, "detail": f"模拟调拨费用 {a['spent'] - b['spent']} 元，无负库存、负现金"},
    ]


class GraphState(TypedDict):
    run_id: str


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["live", "rehearsal"] = "live"
    auto_approve: bool = False
    preset: str = "rescue"
    data: BusinessInput | None = None
    shop_id: str = "sandbox"
    parent_id: str | None = None


class ReviseRun(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    data: BusinessInput | None = None


class FeedbackIn(InputModel):
    fulfilled_orders: int = Field(ge=0, le=100000)
    next_daily_sales: int = Field(gt=0, le=100000)
    acquisition_spend: int = Field(ge=0, le=10000000)
    acquisition_gmv: int = Field(ge=0, le=100000000)
    retention_spend: int = Field(ge=0, le=10000000)
    retention_gmv: int = Field(ge=0, le=100000000)
    resolved_tickets: int = Field(ge=0, le=10000)



class DemoService:
    def __init__(self, store):
        self.store = store
        self.jobs: set[asyncio.Task] = set()
        # 不在重启后静默重复模型调用或写操作。
        for run in store.demo_runs.values():
            run.setdefault("shop_id", "sandbox")
            run.setdefault("preset", "rescue")
            run.setdefault("version", 1)
            run.setdefault("versions", [{"version": 1, "at": run["created_at"], "data": copy.deepcopy(run["before"]), "plan": copy.deepcopy(run["plan"]), "review": copy.deepcopy(run["review"]), "preflight": copy.deepcopy(run.get("preflight", [])), "reason": "历史方案"}] if run.get("plan") else [])
            if run.get("task_id"):
                run.setdefault("approved_version", run["version"])
            card = next((a for a in store.approvals if a.id == run.get("approval_id")), None)
            if card:
                card.demo_run_id = run["id"]
                if run.get("plan"):
                    card.title, card.impact = approval_labels(run["plan"])
            if run["status"] in ("running", "executing"):
                run["status"] = "interrupted"
                run["error"] = "服务重启中断了运行；可从已保存的步骤继续，或回滚。已保留当前沙箱与工具记录。"
                task = next((t for t in store.tasks if t.id == run.get("task_id")), None)
                if task:
                    task.status = "failed"
        self.plan_graph = self.graph([("sense", self.sense), ("plan", self.plan), ("review", self.review), ("gate", self.gate)])
        execution = StateGraph(GraphState)
        execution.add_node("execute", self.execute)
        execution.add_node("verify", self.verify)
        execution.add_edge(START, "execute")
        execution.add_conditional_edges("execute", lambda state: "execute" if self.run(state)["cursor"] < len(self.run(state)["plan"]["actions"]) else "verify", {"execute": "execute", "verify": "verify"})
        execution.add_edge("verify", END)
        self.execution_graph = execution.compile()
        self.store.save()

    def graph(self, nodes):
        graph = StateGraph(GraphState)
        previous = START
        for name, fn in nodes:
            graph.add_node(name, fn)
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        return graph.compile()

    def run(self, state):
        return self.store.demo_runs[state["run_id"]]

    def log(self, run, node, actor, message, *, detail=None, kind="done"):
        run["node"] = node
        run["trace"].append({"seq": len(run["trace"]) + 1, "at": stamp(), "node": node, "actor": actor, "message": message, "detail": detail, "kind": kind})
        actors = {"经营监测": "ana", "经营规划 AI": "ceo", "预置策略": "ceo", "风控复核 AI": "cmp", "边界校验": "cmp", "审批网关": "ceo", "供应链 AI": "sc", "投放 AI": "ad", "客服 AI": "cs", "结果校验器": "ana", "主理人": "ceo"}
        self.store.event(actors.get(actor, "ceo"), f"[经营救援] {message}", "seal" if kind == "waiting" else "ok" if kind == "done" else None)
        self.store.save()

    async def sense(self, state):
        run = self.run(state)
        self.log(run, "sense", "经营监测", "已读取独立沙箱，按本次输入重新计算库存、投放、售后指标", detail={"data": run["before"], "calculated": metrics(run["before"]), "input_hash": run["input_hash"], "calculator": decision_context(run["before"])})
        return {}

    async def model_json(self, run, actor, system, payload):
        started = time.monotonic()
        self.log(run, "plan" if actor == "经营规划 AI" else "review", actor, "正在调用书生模型…", kind="running")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(2):
            started = time.monotonic()
            try:
                raw = await asyncio.wait_for(llm.chat(messages, max_tokens=1500, temperature=0.1), timeout=65)
            except Exception as exc:
                run["model_calls"].append({"actor": actor, "model": run["model"], "version": run.get("version", 1), "attempt": attempt + 1, "elapsed_seconds": round(time.monotonic() - started, 2), "error": type(exc).__name__ + ": " + (str(exc) or "请求超时"), "output": None})
                self.store.save()
                if attempt:
                    raise
                self.log(run, run["node"], actor, "模型请求失败，正在重试一次；本轮失败记录已保留", kind="running")
                continue
            record = {"actor": actor, "model": run["model"], "version": run.get("version", 1), "attempt": attempt + 1,
                      "elapsed_seconds": round(time.monotonic() - started, 2),
                      "input_hash": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]}
            try:
                stripped = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                parsed = json.loads(stripped[stripped.index("{"):stripped.rindex("}") + 1])
            except (ValueError, json.JSONDecodeError) as exc:
                record.update(output={"parse_error": str(exc), "raw_response": raw[:6000]})
                run["model_calls"].append(record)
                self.store.save()
                if attempt:
                    raise ValueError("书生两次返回的 JSON 均无法解析，请查看模型调用证据") from exc
                self.log(run, run["node"], actor, "返回格式未通过校验，正在请求模型修正 JSON", kind="running")
                messages += [{"role": "assistant", "content": raw[:6000]}, {"role": "user", "content": "上条响应不是合法JSON。请修正JSON语法，用英文双引号，严格输出一个完整JSON对象，不加说明和代码围栏，保持原来的字段。"}]
                continue
            record["output"] = copy.deepcopy(parsed)
            record["usage"] = getattr(raw, "usage", {})
            record["response_model"] = getattr(raw, "model", run["model"])
            run["model_calls"].append(record)
            self.store.save()
            return parsed

    async def plan(self, state):
        run = self.run(state)
        if run["mode"] == "live":
            if not llm.enabled():
                raise ValueError("未配置书生 API，请在运营台的模型配置中设置，或选择预置策略演练")
            prompt = """你是电商经营规划AI，根据本次data重新计算，禁止套用历史数字。只输出JSON：
{"headline":"结论","findings":["至少两条带数字的发现"],"actions":[{"tool":"工具名","arguments":{"参数名":整数},"why":"依据"}]}。
最多3个动作，每工具最多一次；没有异常可不使用工具。判断是否补货必须比较库存覆盖与常规lead_days，不能用备用仓transfer_days作为目标。transfer_days只用于判断来不来得及到货。库存不足正常交期时，目标补到(lead_days+1)*daily_sales，但不能超过备用库存或现金；备用仓交期以本轮transfer_days为准。
拉新ROI低于保本线且复购ROI更高时可迁移预算，上限min(900,acquisition_budget)。只为未建工单的破损单补工单，待人工联系的工单也已经建单，不能重复创建。calculator.service.uncovered_damaged_orders=0时严禁输出service.create_tickets。
资源不足应如实说明，不能编造已解决。不能将ROI估算当实际收益。收到revision_feedback时调整方案或指出仍缺少的资源。
headline不超过40字，findings最多4条，why不超过50字。只调用目录工具。calculator是确定性计算工具输出，优先依据该结果选择动作。needs_transfer=false禁止调拨，needs_rebalance=false不迁移预算。数量必须区分现有库存、目标库存、调拨增量。"""
            payload = {"data": model_data(run["before"]), "calculator": decision_context(run["before"]), "tools": TOOLS, "memory": run["memory"], "revision_feedback": run.get("revision_feedback", ""), "previous_plan": run.get("previous_plan")}
            for attempt in range(2):
                plan = await self.model_json(run, "经营规划 AI", prompt, payload)
                try:
                    plan = validate_plan(plan, run["before"])
                    issues = plan_issues(plan, run["before"])
                    if issues:
                        raise ValueError("；".join(issues))
                    break
                except ValueError as exc:
                    run.setdefault("corrections", []).append({"version": run.get("version", 1), "invalid_plan": plan, "error": str(exc), "at": stamp()})
                    if attempt:
                        raise
                    payload["previous_invalid_plan"] = plan
                    payload["validation_feedback"] = str(exc)
                    self.log(run, "plan", "边界校验", f"模型方案需修正：{exc}，正请求书生重新规划", kind="running")
        else:
            plan = rule_plan(run["before"])
        run["plan"] = validate_plan(plan, run["before"])
        self.log(run, "plan", "经营规划 AI" if run["mode"] == "live" else "预置策略", plan["headline"], detail=plan)
        return {}

    async def review(self, state):
        run = self.run(state)
        projected = copy.deepcopy(run["before"])
        for action in run["plan"]["actions"]:
            execute_tool(action, projected, "preview")
        checks = verification(run["before"], projected)
        i = run["before"]["inventory"]
        if any(a["tool"] == "inventory.transfer" for a in run["plan"]["actions"]):
            checks.append({"name": "备用仓在断货前到达", "passed": i["stock"] / i["daily_sales"] >= i["transfer_days"], "detail": f"现库存覆盖{i['stock'] / i['daily_sales']:.2f}天，调拨交期{i['transfer_days']}天"})
        for action in run["plan"]["actions"]:
            key = {"inventory.transfer": "sc", "ads.rebalance": "ad", "service.create_tickets": "cs"}[action["tool"]]
            cfg = self.store.staff[key]
            checks.append({"name": f"{key} 已雇佣并具备执行权限", "passed": cfg["hired"] and cfg["level"] >= 2, "detail": f"负责人{cfg['owner'] or '主理人'} · L{cfg['level']}（L1仅建议）"})
        if run["mode"] == "live":
            prompt = """你是独立电商风控复核AI。只输出JSON：{"safe":true或false,"summary":"审核意见","concerns":["风险或注意事项"]}。
完全按本次data和calculator、tool_checks核算，不编造交期、费用或销量。tool_checks为程序对本方案试执行的结果。
任一程序检查不通过必须safe=false。全通过时还须核实方案有没有其他真实依据支持的风险，无法执行才标false，可人工核实的常规注意事项可标true并列入concerns。
只在模拟沙箱执行，无真实外部操作。ROI仅估算，建工单不降低历史退款率。summary不超过60字，concerns最多4条。"""
            payload = {"data": model_data(run["before"]), "calculator": decision_context(run["before"]), "plan": run["plan"], "projected_metrics": metrics(projected), "tool_checks": checks, "tools": TOOLS}
            review = await self.model_json(run, "风控复核 AI", prompt, payload)
            def validate_review(value):
                if not isinstance(value, dict) or type(value.get("safe")) is not bool or not isinstance(value.get("summary"), str) or not isinstance(value.get("concerns", []), list) or not all(isinstance(c, str) for c in value.get("concerns", [])):
                    raise ValueError("风控模型返回结构无效")
            validate_review(review)
            if not review["safe"] and all(c["passed"] for c in checks):
                # 不覆盖模型否决，给它一次用确定性证据核对事实的机会，仍否决就停止。
                payload["previous_review"] = review
                payload["request"] = "程序约束均通过。请逐项核实上次否决引用的数字和推理，如风险仍成立继续否决，禁止编造data没有的事实。"
                self.log(run, "review", "风控复核 AI", "风控意见与程序检查有分歧，正在依据工具证据核实一次", detail=review, kind="running")
                review = await self.model_json(run, "风控复核 AI", prompt, payload)
                validate_review(review)
        else:
            review = {"safe": True, "summary": "规则方案已计算，等待业务约束校验", "concerns": [f"真实调拨需核实{i['transfer_days']}天交期", "ROI为固定投产假设下的估算", "工单创建不代表售后问题已解决"]}
        review["model_safe"] = review["safe"]
        review["safe"] = review["safe"] and all(c["passed"] for c in checks)
        failed = [c for c in checks if not c["passed"]]
        if failed:
            review["summary"] = "程序校验未通过：" + "、".join(c["name"] for c in failed)
        review["concerns"] = list(review.get("concerns", [])) + [c["detail"] for c in failed]
        run["review"] = review
        run["preview"] = metrics(projected)
        run["preflight"] = checks
        self.log(run, "review", "风控复核 AI" if run["mode"] == "live" else "边界校验", review["summary"], detail={"review": review, "checks": checks})
        return {}

    async def gate(self, state):
        run = self.run(state)
        run["status"] = "awaiting_approval" if run["review"]["safe"] else "blocked"
        run.setdefault("versions", []).append({"version": run.get("version", 1), "at": stamp(), "data": copy.deepcopy(run["before"]), "plan": copy.deepcopy(run["plan"]), "review": copy.deepcopy(run["review"]), "preflight": copy.deepcopy(run["preflight"]), "reason": run.get("revision_feedback", "初始方案")})
        if run["status"] == "awaiting_approval":
            title, impact = approval_labels(run["plan"])
            card = Approval(id=new_id("AP"), title=title, **{"from": "ceo"},
                summary=run["plan"]["headline"], impact=impact, cost=0, risk="red", demo_run_id=run["id"],
                evidence=Evidence(summary=run["review"]["summary"], metrics=run["plan"]["findings"],
                    reasons=run["plan"]["findings"], risks=run["review"].get("concerns", []), rollback="按执行前完整快照恢复库存、预算、资金及工单",
                    sources=[run["id"], "LangGraph", run["model"] if run["mode"] == "live" else "预置策略 · 未调用模型"]))
            self.store.approvals.insert(0, card)
            run["approval_id"] = card.id
        self.log(run, "gate", "审批网关", "联合方案已进入右侧审批中心，等待主理人拍板" if run["review"]["safe"] else "方案未通过复核，已阻止执行", kind="waiting")
        if run["status"] == "awaiting_approval" and run["auto_approve"]:
            self.begin_execution(run)
            await self.execution_graph.ainvoke(state)
        return {}

    def begin_execution(self, run):
        if run["status"] != "awaiting_approval":
            raise HTTPException(409, "当前演练未处于待审批状态")
        self.check_staff(run)
        run["approved_version"] = run.get("version", 1)
        run["status"] = "executing"
        card = next((a for a in self.store.approvals if a.id == run.get("approval_id")), None)
        if card:
            card.status = "approved"
        task = Task(id=new_id("T"), title="大促经营救援 · 联合执行", kind="demo", **{"from": "ceo"},
            approval_id=run.get("approval_id", ""), demo_run_id=run["id"], risk="red",
            steps=[Step(text=t["tool"], status="running" if i == 0 else "wait") for i, t in enumerate(run["plan"]["actions"])] + [Step(text="核验库存、预算、工单与资金")],
            snapshot={"kind": "经营救援沙箱", "run_id": run["id"], "before": copy.deepcopy(run["before"])})
        self.store.tasks.insert(0, task)
        run["task_id"] = task.id
        self.log(run, "gate", "主理人", "已批准联合方案，任务列表开始记录实际工具执行")
        self.store.audit_log("owner", "demo.approve", run["id"])
        self.store.save()

    def check_staff(self, run):
        keys = ["ceo", "cmp"] + [{"inventory.transfer": "sc", "ads.rebalance": "ad", "service.create_tickets": "cs"}[a["tool"]] for a in run["plan"]["actions"]]
        for key in keys:
            cfg = self.store.staff[key]
            if not cfg["hired"] or (key not in ("ceo", "cmp") and cfg["level"] < 2):
                raise HTTPException(409, f"{self.store.agent_by[key].name}未雇佣或仅有L1建议权限，请调整员工配置后重新复核")

    def revise(self, run, body):
        if run["status"] not in ("awaiting_approval", "blocked", "rejected", "failed") or run["receipts"]:
            raise HTTPException(409, "只能修改未执行的方案；已有变更请先回滚再创建新一轮")
        card = next((a for a in self.store.approvals if a.id == run.get("approval_id")), None)
        if card:
            card.status = "rejected"
        run["previous_plan"] = copy.deepcopy(run["plan"])
        run["version"] = run.get("version", 1) + 1
        run["revision_feedback"] = body.reason
        if body.data:
            run["before"] = body.data.model_dump()
            run["current"] = copy.deepcopy(run["before"])
        run["input_hash"] = hashlib.sha256(json.dumps(run["before"], sort_keys=True).encode()).hexdigest()[:16]
        run.update(status="running", node="sense", plan=None, review=None, error="", approval_id=None, preflight=[], preview=None)
        self.store.audit_log("owner", "demo.revise", run["id"])
        self.log(run, "sense", "主理人", f"退回修改 v{run['version']}：{body.reason}")
        self.launch(run["id"], self.plan_graph)

    def feedback(self, run, body):
        if run["status"] != "completed" or run.get("feedback"):
            raise HTTPException(409, "仅可对已完成运行录入一次反馈；后续反馈请创建下一轮")
        data = copy.deepcopy(run["current"])
        if body.fulfilled_orders > data["inventory"]["stock"]:
            raise HTTPException(422, "模拟销量不能超过可用库存")
        pending = [t for t in data["service"]["tickets"] if t["status"] != "已解决"]
        if body.resolved_tickets > len(pending):
            raise HTTPException(422, "解决数超过待处理工单")
        for prefix in ("acquisition", "retention"):
            spend, gmv = getattr(body, prefix + "_spend"), getattr(body, prefix + "_gmv")
            if (not spend and gmv) or (spend and gmv / spend > 100):
                raise HTTPException(422, "反馈GMV与消耗不一致或ROI超过100")
            if spend > data["ads"][prefix + "_budget"]:
                raise HTTPException(422, "观察窗口的广告消耗不能超过该计划预算")
            if spend:
                data["ads"][prefix + "_roi"] = round(gmv / spend, 4)
        data["inventory"]["stock"] -= body.fulfilled_orders
        data["inventory"]["daily_sales"] = body.next_daily_sales
        for ticket in pending[:body.resolved_tickets]:
            ticket["status"] = "已解决"
        run["execution_result"] = copy.deepcopy(run["current"])
        run["current"] = data
        run["feedback"] = {"at": stamp(), "source": "人工录入的模拟观察窗口，非真实店铺收益", "input": body.model_dump(), "metrics": metrics(data)}
        self.log(run, "feedback", "经营监测", "已录入模拟经营反馈，可基于新库存和实测窗口ROI创建下一轮方案", detail=run["feedback"])
        self.store.audit_log("owner", "demo.feedback", run["id"])
        self.store.save()

    def resume(self, run):
        if run["status"] not in ("failed", "interrupted"):
            raise HTTPException(409, "当前状态无需恢复")
        if not run.get("task_id"):
            run.update(status="running", error="")
            self.launch(run["id"], self.plan_graph)
        else:
            self.check_staff(run)
            if run.get("approved_version", 1) != run.get("version", 1):
                raise HTTPException(409, "方案版本已变化，须重新审批")
            # 每次工具变更和游标一同保存；已提交步骤不会重复执行。
            if run["cursor"] != len(run["receipts"]):
                raise HTTPException(409, "检查点与工具回执不一致，请回滚")
            run.update(status="executing", error="")
            task = self.task_of(run)
            if task:
                task.status = "running"
            self.launch(run["id"], self.execution_graph)
        self.log(run, run["node"], "主理人", "从持久化检查点恢复，已完成工具不会重放")

    def task_of(self, run):
        return next((t for t in self.store.tasks if t.id == run.get("task_id")), None)

    def approve_run(self, run):
        self.begin_execution(run)
        self.launch(run["id"], self.execution_graph)

    def rollback_run(self, run):
        if run["status"] not in ("completed", "verification_failed", "failed", "interrupted") or not run["receipts"]:
            raise HTTPException(409, "当前状态无法回滚，或尚无工具变更")
        run["current"] = copy.deepcopy(run["before"])
        run["status"] = "rolled_back"
        run["rollback_verified"] = run["current"] == run["before"]
        task = self.task_of(run)
        if task:
            task.status = "failed"
            if task.snapshot:
                task.snapshot["rollback_verified"] = True
        self.log(run, "rollback", "主理人", "已恢复执行前快照，库存、预算、现金及工单完全一致", detail={"restored": run["rollback_verified"]})
        self.store.audit_log("owner", "demo.rollback", run["id"])
        self.store.save()

    async def execute(self, state):
        run = self.run(state)
        self.check_staff(run)
        if run["cursor"] >= len(run["plan"]["actions"]):
            return {}
        action = run["plan"]["actions"][run["cursor"]]
        before = copy.deepcopy(run["current"])
        output = execute_tool(action, run["current"], run["id"])
        run["receipts"].append({"tool": action["tool"], "arguments": action["arguments"], "before": before, "output": output, "after": copy.deepcopy(run["current"]), "at": stamp()})
        run["cursor"] += 1
        task = self.task_of(run)
        if task:
            task.steps[run["cursor"] - 1].status = "done"
            task.steps[run["cursor"] - 1].at = now_hm()
            task.steps[run["cursor"]].status = "running"
        self.log(run, "execute", next(t["owner"] for t in TOOLS if t["name"] == action["tool"]), f"已调用 {action['tool']}，沙箱状态已更新", detail={"arguments": action["arguments"], "output": output})
        await asyncio.sleep(0)
        return {}

    async def verify(self, state):
        run = self.run(state)
        run["checks"] = verification(run["before"], run["current"])
        run["status"] = "completed" if all(c["passed"] for c in run["checks"]) else "verification_failed"
        task = self.task_of(run)
        if task:
            task.status = "done" if run["status"] == "completed" else "failed"
            task.finished_at = now_hm()
            task.steps[-1].status = "done"
            task.steps[-1].at = now_hm()
            task.evented = True
        run["finished_at"] = stamp()
        run["elapsed_seconds"] = round(time.time() - run["started_ts"], 2)
        self.log(run, "verify", "结果校验器", "沙箱执行完成，业务约束已校验" if run["status"] == "completed" else "结果未满足约束，请检查或回滚", detail=run["checks"])
        self.store.audit_log("demo", "demo." + run["status"], run["id"])
        self.store.save()
        return {}

    async def drive(self, run_id, graph):
        run = self.store.demo_runs[run_id]
        try:
            await graph.ainvoke({"run_id": run_id})
        except Exception as exc:
            run["status"] = "failed"
            task = self.task_of(run)
            if task:
                task.status = "failed"
                task.finished_at = now_hm()
            run["error"] = str(exc)[:300] or "运行失败"
            if not any(v["version"] == run.get("version", 1) for v in run.get("versions", [])):
                run.setdefault("versions", []).append({"version": run.get("version", 1), "at": stamp(), "data": copy.deepcopy(run["before"]), "plan": copy.deepcopy(run.get("plan")), "review": copy.deepcopy(run.get("review")), "error": run["error"], "reason": run.get("revision_feedback", "初始方案")})
            self.log(run, run["node"], "运行控制器", run["error"], kind="error")
            self.store.save()

    def launch(self, run_id, graph):
        task = asyncio.create_task(self.drive(run_id, graph))
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    def public(self, run):
        return {**run, "before_metrics": metrics(run["before"]), "current_metrics": metrics(run["current"])}


def create_router(store):
    router = APIRouter(prefix="/api/demo", tags=["competition-demo"])
    service = DemoService(store)
    store.demo_service = service

    def get(run_id):
        run = store.demo_runs.get(run_id)
        if not run:
            raise HTTPException(404, "演练不存在")
        return run

    @router.get("/template")
    def template():
        return {"title": "大促前的经营救援", "data": SEED, "metrics": metrics(SEED), "tools": TOOLS, "presets": PRESETS, "harness": "LangGraph", "model": llm.current()["model"]}

    @router.get("/runs")
    def list_runs():
        return [{"id": r["id"], "mode": r["mode"], "status": r["status"], "created_at": r["created_at"], "shop_id": r.get("shop_id", "sandbox"), "version": r.get("version", 1), "title": (r.get("plan") or {}).get("headline", "分析中")} for r in list(store.demo_runs.values())[-100:]][::-1]

    @router.post("/runs", status_code=201)
    async def start(body: StartRun):
        if any(r["status"] in ("running", "executing") for r in store.demo_runs.values()) or any(r["status"] == "running" for r in store.evaluations.values()):
            raise HTTPException(409, "已有演练或评测正在运行，请等待完成")
        if body.shop_id != "sandbox" and not any(x["id"] == body.shop_id for x in store.shops):
            raise HTTPException(404, "模拟店铺不存在")
        for key in ("ceo", "cmp"):
            if not store.staff[key]["hired"]:
                raise HTTPException(409, "请先雇佣经营规划和风控岗位")
        preset = next((p for p in PRESETS if p["id"] == body.preset), None)
        if not preset:
            raise HTTPException(422, "业务案例不存在")
        data = body.data.model_dump() if body.data else copy.deepcopy(preset["data"])
        if body.parent_id:
            parent = get(body.parent_id)
            if parent["status"] != "completed" or not parent.get("feedback") or parent.get("shop_id", "sandbox") != body.shop_id:
                raise HTTPException(409, "下一轮须来自同一店铺已反馈的完成运行")
            if parent.get("followup_run_id"):
                raise HTTPException(409, "该反馈已创建下一轮，请从历史中查看")
            data = copy.deepcopy(parent["current"])
        run_id = new_id("DEMO")
        prior = [r for r in store.demo_runs.values() if r["status"] == "completed" and r.get("shop_id", "sandbox") == body.shop_id][-3:]
        run = {"id": run_id, "created_at": stamp(), "started_ts": time.time(), "mode": body.mode,
               "model": llm.current()["model"] if body.mode == "live" else "", "auto_approve": body.auto_approve, "harness": "LangGraph", "status": "running", "node": "sense",
               "before": copy.deepcopy(data), "current": copy.deepcopy(data), "version": 1, "versions": [], "shop_id": body.shop_id, "preset": body.preset, "parent_id": body.parent_id, "plan": None, "review": None,
               "trace": [], "model_calls": [], "receipts": [], "cursor": 0, "checks": [], "error": "",
               "memory": [{"run_id": r["id"], "summary": r["plan"]["headline"], "execution_verified_metrics": metrics(r.get("execution_result", r["current"])), "current_metrics": metrics(r["current"])} for r in prior],
               "input_hash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]}
        store.demo_runs[run_id] = run
        if body.parent_id:
            get(body.parent_id)["followup_run_id"] = run_id
        store.save()
        service.launch(run_id, service.plan_graph)
        return service.public(run)

    @router.get("/runs/{run_id}")
    def read(run_id: str):
        return service.public(get(run_id))

    @router.post("/runs/{run_id}/approve")
    async def approve(run_id: str):
        run = get(run_id)
        service.approve_run(run)
        return service.public(run)

    @router.post("/runs/{run_id}/rollback")
    def rollback(run_id: str):
        run = get(run_id)
        service.rollback_run(run)
        return service.public(run)

    @router.post("/runs/{run_id}/revise")
    async def revise(run_id: str, body: ReviseRun):
        run = get(run_id)
        service.revise(run, body)
        return service.public(run)

    @router.post("/runs/{run_id}/feedback")
    async def feedback(run_id: str, body: FeedbackIn):
        run = get(run_id)
        service.feedback(run, body)
        return service.public(run)

    @router.post("/runs/{run_id}/resume")
    async def resume(run_id: str):
        if any(r["status"] in ("running", "executing") for r in store.demo_runs.values()):
            raise HTTPException(409, "已有运行正在处理，请稍后恢复")
        run = get(run_id)
        service.resume(run)
        return service.public(run)

    @router.get("/runs/{run_id}/export")
    def export(run_id: str):
        return {"schema": "ai-ecom-demo/v2", "data_scope": "合成业务数据；本地沙箱执行；不代表真实经营收益", "run": service.public(get(run_id))}

    return router
