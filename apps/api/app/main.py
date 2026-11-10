"""AI 电商公司 · HTTP 接口。

会议用「推进式」状态机：客户端每调一次 /next 出一条发言，
直到进入 pick 阶段再由主理人 /decide 拍板。这样测试可确定、前端也能接 SSE。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import domain as d
from . import llm, shopdata, tools, scenarios
from .store import Store

app = FastAPI(title="AI 电商公司 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174", "file://"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = Store()


def meet_or_404(mid: str) -> d.Meeting:
    m = store.meetings.get(mid)
    if not m:
        raise HTTPException(404, "会议不存在")
    return m


class CreateMeeting(BaseModel):
    template: str = "promo"
    who: list[str] | None = None
    topic: str | None = None


class SayIn(BaseModel):
    text: str


class DecideIn(BaseModel):
    v: str


class ApprovalDecideIn(BaseModel):
    approve: bool
    reason: str = ""
    actor: str = "主理人"


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "app": "ai-ecom-platform",
        "desktop_instance": os.getenv("AIECOM_DESKTOP_INSTANCE", ""),
        "agents": len(store.agents),
        "templates": len(store.templates),
        "model_enabled": llm.enabled(),
    }


@app.get("/api/model")
async def model_status(force: bool = False) -> dict[str, Any]:
    return await llm.status(force=force)


class ModelSettingsIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.get("/api/settings/model")
async def get_model_settings(force: bool = False) -> dict[str, Any]:
    return await llm.status(force=force)


@app.put("/api/settings/model")
async def put_model_settings(body: ModelSettingsIn) -> dict[str, Any]:
    llm.update(base_url=body.base_url, api_key=body.api_key, model=body.model)
    store.event("ceo", "更新了模型配置", "ok")
    store.save()
    return await llm.status(force=True)


@app.post("/api/settings/model/test")
async def test_model_settings(body: ModelSettingsIn) -> dict[str, Any]:
    return await llm.test_connection(body.base_url, body.api_key, body.model)


# ------------------------------ 员工 ------------------------------
@app.get("/api/agents")
def list_agents() -> list[dict[str, Any]]:
    return [
        {**a.model_dump(), "stance": store.stance.get(a.k, "")}
        for a in store.agents
    ]


# ------------------------------ 模拟组织与店铺 ------------------------------
class OrganizationIn(BaseModel):
    mode: str


@app.get("/api/organization")
def get_organization():
    return store.organization


@app.put("/api/organization")
def update_organization(body: OrganizationIn):
    if body.mode not in ("solo", "team"):
        raise HTTPException(422, "组织模式无效")
    store.organization = {"mode": body.mode}
    store.audit_log("owner", "organization.update", body.mode)
    store.save()
    return store.organization


def approval_owner(approval):
    if approval.risk == "red" or store.organization["mode"] == "solo":
        return "主理人"
    return store.staff[approval.from_].get("owner") or "主理人"

class StaffUpdate(BaseModel):
    hired: bool
    owner: str = ""
    level: int = 2


@app.get("/api/staff")
def get_staff() -> dict:
    return store.staff


@app.put("/api/staff/{key}")
def update_staff(key: str, body: StaffUpdate) -> dict:
    if key not in store.agent_by:
        raise HTTPException(404, "员工不存在")
    if body.level not in range(1, 5):
        raise HTTPException(400, "自治等级须为 L1 至 L4")
    owner = body.owner.strip()[:40] if body.hired else ""
    store.staff[key] = {"hired": body.hired, "owner": owner, "level": body.level}
    store.audit_log("owner", "staff.update", key)
    store.event("ceo", f"更新了{store.agent_by[key].name}的员工配置")
    store.save()
    return store.staff[key]


class MockShopIn(BaseModel):
    platform: str
    name: str


@app.get("/api/shops")
def list_shops() -> list[dict]:
    return store.shops


@app.post("/api/shops", status_code=201)
def connect_shop(body: MockShopIn) -> dict:
    platform, name = body.platform.strip(), body.name.strip()
    if platform not in ("淘宝 / 天猫", "抖音", "拼多多", "其他") or not name or len(name) > 40:
        raise HTTPException(400, "请选择平台并填写店铺名称（最多 40 字）")
    shop = {"id": d.new_id("SHOP"), "platform": platform, "name": name, "status": "模拟授权", "data_source": "演示数据"}
    store.shops.append(shop)
    store.audit_log("owner", "shop.mock_connect", shop["id"])
    store.event("ceo", f"添加模拟店铺：{name}（{platform}）")
    store.save()
    return shop


@app.delete("/api/shops/{shop_id}")
def remove_shop(shop_id: str) -> dict:
    shop = next ( (x for x in store.shops if x["id"] == shop_id), None)
    if not shop:
        raise HTTPException(404, "店铺不存在")
    store.shops.remove(shop)
    store.audit_log("owner", "shop.mock_disconnect", shop_id)
    store.save()
    return {"ok": True}


# ------------------------------ 会议 ------------------------------
@app.get("/api/templates")
def list_templates() -> list[d.Template]:
    return list(store.templates.values())


@app.post("/api/meetings", status_code=201)
def create_meeting(body: CreateMeeting) -> d.Meeting:
    tpl = store.templates.get(body.template)
    if not tpl:
        raise HTTPException(400, f"没有这个会议模板：{body.template}")
    who = body.who if body.who is not None else [k for k in tpl.who if store.staff[k]["hired"]]
    if len(who) != len(set(who)):
        raise HTTPException(400, "参会员工不能重复")
    unknown = [k for k in who if k not in store.agent_by]
    if unknown:
        raise HTTPException(400, f"未知参会人：{unknown}")
    if any(not store.staff[k]["hired"] for k in who):
        raise HTTPException(409, "未雇佣员工不能参会，请先调整员工配置")
    if len(who) < 2:
        raise HTTPException(400, "至少需要 2 名参会 AI")
    topic = (body.topic or "").strip()[:200]
    if body.template == "custom" and not topic:
        raise HTTPException(422, "自定义会议必须填写议题")
    m = d.Meeting(
        id=d.new_id("M"),
        template=tpl.key,
        title=f"临时会 · {topic[:60]}" if body.template == "custom" else tpl.title,
        goal=tpl.goal,
        who=who,
        # 只保留到场员工的发言；不在场的人不替他说话
        agenda=[
            a.model_copy(update={
                "says": [s for s in a.says if s[0] in who],
                "clash": (
                    a.clash.model_copy(update={"by": [k for k in a.clash.by if k in who]})
                    if a.clash and sum(1 for k in a.clash.by if k in who) >= 2 else None
                ),
            })
            for a in (d.custom_agenda(topic, who) if body.template == "custom" else tpl.agenda)
        ],
        topic=body.topic,
    )
    store.meetings[m.id] = m
    store.audit_log("owner", "meeting.create", m.id)
    store.event("ceo", f"发起会议：{m.title}（{len(who)} 人）")
    store.save()
    return m


@app.get("/api/meetings")
def list_meetings() -> list[d.Meeting]:
    return list(store.meetings.values())[::-1]


@app.get("/api/meetings/{mid}")
def get_meeting(mid: str) -> d.Meeting:
    return meet_or_404(mid)


@app.post("/api/meetings/{mid}/start")
def start(mid: str) -> d.Meeting:
    m = meet_or_404(mid)
    if m.stage != "ready":
        raise HTTPException(409, "会议已经开始过了")
    d.start_meeting(m)
    store.audit_log("owner", "meeting.start", m.id)
    store.save()
    return m


@app.post("/api/meetings/{mid}/cancel")
def cancel_meeting(mid: str):
    m = meet_or_404(mid)
    if m.stage not in ("ready", "run"):
        raise HTTPException(409, "会议已结束")
    m.stage = "cancelled"
    store.audit_log("owner", "meeting.cancel", mid)
    store.event("ceo", f"「{m.title}」已提前散会，未派发行动项")
    store.save()
    return m


@app.post("/api/meetings/{mid}/next")
def next_line(mid: str) -> dict[str, Any]:
    m = meet_or_404(mid)
    line = d.reveal_next(m)
    store.save()
    return {"line": line, "phase": m.phase, "shown": m.shown, "idx": m.idx, "mins": m.mins, "cost": m.cost}


@app.get("/api/meetings/{mid}/stream")
async def stream(mid: str) -> StreamingResponse:
    m = meet_or_404(mid)

    async def gen():
        while m.stage == "run" and m.phase == "say":
            line = d.reveal_next(m)
            if line is None:
                break
            payload = {
                "type": "say",
                "line": line,
                "shown": m.shown,
                "mins": m.mins,
                "cost": m.cost,
                "idx": m.idx,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.8)
        store.save()
        yield f"data: {json.dumps({'type': 'pick', 'idx': m.idx, 'phase': m.phase}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _persona_prompt(k: str, m: d.Meeting, note: d.Note) -> str:
    agent = store.agent_by[k]
    return (
        f"你是「{agent.name}」，岗位是{agent.title}。\n"
        f"你的立场：{store.stance.get(k, '')}\n"
        f"你在意的指标：{'、'.join(agent.kpi[:3])}\n"
        f"会议：{m.title}；当前议题：{note.topic}\n"
        f"主理人刚才说：{note.text}\n\n"
        "用 1-2 句话回应。要求：先明确接住他的判断（同意或有保留），"
        "再给出你这一环的执行前提或风险；不超过 60 字；"
        "不要客套话、不要复述他的话、不要加姓名前缀。"
    )


async def model_replies(m: d.Meeting, note: d.Note) -> list[tuple[str, str]]:
    """两个岗位并行调用模型；任何一个失败就退回该岗位的规则话术。"""
    pairs = d.reply_for(note.text, m.who)
    if not pairs or not llm.enabled() or not llm.quick_ready():
        return pairs

    async def one(k: str, fallback: str) -> tuple[str, str]:
        try:
            text = await llm.chat(
                [{"role": "system", "content": _persona_prompt(k, m, note)},
                 {"role": "user", "content": "请回应。"}],
                temperature=0.7,
                max_tokens=500,
            )
            return k, " ".join(text.split())[:120]
        except Exception:
            return k, fallback

    return await asyncio.gather(*[one(k, t) for k, t in pairs])


@app.post("/api/meetings/{mid}/say")
async def say(mid: str, body: SayIn) -> dict[str, Any]:
    m = meet_or_404(mid)
    if m.stage != "run":
        raise HTTPException(409, "会议不在进行中")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "发言不能为空")
    note = d.add_note(m, text)
    if llm.enabled() and llm.quick_ready():
        try:
            note.replies = await asyncio.wait_for(model_replies(m, note), timeout=12)
        except Exception:
            pass
    store.audit_log("owner", "meeting.note", mid)
    store.save()
    return {"note": note, "mins": m.mins, "cost": m.cost, "model": llm.enabled()}


@app.post("/api/meetings/{mid}/decide")
def decide(mid: str, body: DecideIn) -> dict[str, Any]:
    m = meet_or_404(mid)
    if m.stage != "run":
        raise HTTPException(409, "会议不在进行中")
    try:
        decision = d.decide(m, body.v)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    store.audit_log("owner", "meeting.decide", mid)
    store.event("ceo", f"会议拍板：{decision.label}", "ok")
    store.save()
    return {"decision": decision, "idx": m.idx, "phase": m.phase, "stage": m.stage, "cost": m.cost}


@app.post("/api/meetings/{mid}/finish")
def finish(mid: str) -> dict[str, Any]:
    m = meet_or_404(mid)
    if m.stage != "done":
        raise HTTPException(409, "还有议题没拍板")
    if m.dispatched:
        raise HTTPException(409, "这次会议的行动项已经派发过了")
    m.dispatched = True
    store.audit_log("owner", "meeting.dispatch", mid)
    actions = d.actions_of(m)
    cards = [d.approval_from_action(a, len(m.notes), m.id) for a in actions]
    store.approvals = cards + store.approvals
    store.event("ceo", f"「{m.title}」结束：{len(m.res)} 项决议，{len(actions)} 条行动项派发到审批中心", "ok")
    store.save()
    return {"actions": actions, "approvals": [c.model_dump(by_alias=True) for c in cards], "meeting": m}


# ------------------------------ 审批 ------------------------------
@app.get("/api/approvals")
def list_approvals() -> list[dict]:
    return [{**a.model_dump(by_alias=True), "approver": approval_owner(a)} for a in store.approvals]


@app.post("/api/approvals/{aid}/decide")
async def decide_approval(aid: str, body: ApprovalDecideIn) -> d.Approval:
    a = next((x for x in store.approvals if x.id == aid), None)
    if not a:
        raise HTTPException(404, "审批不存在")
    if body.actor not in ("主理人", approval_owner(a)):
        raise HTTPException(403, "该方案须由指定主管或主理人审批；红线只能由主理人批准")
    if a.status != "pending":
        raise HTTPException(409, "这条已经处理过了")
    demo_run = next((r for r in store.demo_runs.values() if r.get("approval_id") == aid), None)
    if demo_run:
        if demo_run["status"] != "awaiting_approval":
            raise HTTPException(409, "该救援流程当前不可审批")
        if body.approve:
            store.demo_service.approve_run(demo_run)
        else:
            a.status = "rejected"
            demo_run["status"] = "rejected"
            store.demo_service.log(demo_run, "gate", "主理人", "联合方案已驳回，沙箱保持执行前状态", kind="done")
            store.audit_log("owner", "demo.reject", demo_run["id"])
            store.save()
        return a
    if body.approve and not store.staff[a.from_]["hired"]:
        raise HTTPException(409, "提案员工已离职，请重新分配后执行")
    a.status = "approved" if body.approve else "rejected"
    store.audit_log("owner", "approval.approve" if body.approve else "approval.reject", a.id)
    if body.approve:
        task = tools.create_task(a)
        store.tasks.insert(0, task)
        store.event(a.from_, f"「{a.title}」已通过，开始执行", "ok")
        store.audit_log(a.from_, "task.start", task.id)
    else:
        store.event(a.from_, f"「{a.title}」被驳回" + (f"：{body.reason.strip()[:200]}" if body.reason.strip() else ""), "seal")
    store.save()
    return a


class ApprovalQuestion(BaseModel):
    question: str


@app.post("/api/approvals/{aid}/ask")
async def ask_approval(aid: str, body: ApprovalQuestion):
    a = next((x for x in store.approvals if x.id == aid), None)
    if not a:
        raise HTTPException(404, "审批不存在")
    question = body.question.strip()
    if not question or len(question) > 1000:
        raise HTTPException(422, "请输入1至1000字的问题")
    answer, source = "", "rules"
    run = store.demo_runs.get(a.demo_run_id or "")
    context = {"data": run["before"], "plan": run["plan"], "projected_metrics": run.get("preview"), "checks": run.get("preflight")} if run else None
    if llm.enabled():
        try:
            answer = await asyncio.wait_for(llm.chat([
                {"role": "system", "content": "你是审批方案解释助手。仅根据附带证据和business_context回答，不编造收益，不执行动作。approval.cost为演示算力费用，业务调拨费见projected_metrics.spent减data.finance.spent，不能混淆。若证据不足说明缺少什么。中文200字以内。"},
                {"role": "user", "content": json.dumps({"approval": a.model_dump(by_alias=True), "business_context": context, "question": question}, ensure_ascii=False)}], max_tokens=600), timeout=65)
            source = "书生 " + llm.current()["model"]
        except Exception:
            answer = "模型暂时未返回。现有依据：" + a.evidence.summary + "；风险：" + "；".join(a.evidence.risks)
    else:
        answer = "规则摘要（未调用模型）：" + a.evidence.summary + "；风险：" + "；".join(a.evidence.risks)
    a.discussion.append({"question": question, "answer": answer, "source": source, "at": d.now_hm()})
    store.audit_log("owner", "approval.ask", aid)
    store.save()
    return a


# ------------------------------ 经营分析 ------------------------------
class AnalyzeIn(BaseModel):
    focus: str = ""
    run_id: str | None = None


ANALYST_SYSTEM = """你是「数据分析 AI」，一家家居收纳类目电商公司的经营分析师。
规则：
1. 只基于用户给的数据，不许编造数字、不许引入外部信息。
2. 结论要指出「哪一项偏离、偏离多少、先动哪一件」。
3. 输出严格 JSON，不要 markdown 代码块，不要多余文字。

JSON 结构：
{"headline": "一句话结论，40 字以内，必须带数字",
 "findings": ["3-5 条发现，每条 60 字以内，必须引用具体数字"],
 "actions": [{"text": "建议动作，30 字以内，可直接派给岗位执行",
              "owner": "ops|ad|sc|wms|cs|ct|cmp|fin|ana",
              "why": "依据，30 字以内"}]}"""


def _parse_json(raw: str) -> dict | None:
    t = raw.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if len(t.split("```")) > 1 else t
        t = t.removeprefix("json").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def _shape(parsed: dict, model: str, data: dict) -> dict | None:
    headline = str(parsed.get("headline") or "").strip()
    findings = [str(x).strip() for x in (parsed.get("findings") or []) if str(x).strip()]
    actions = []
    for a in parsed.get("actions") or []:
        if not isinstance(a, dict):
            continue
        text = str(a.get("text") or "").strip()
        owner = str(a.get("owner") or "ceo").strip()
        why = str(a.get("why") or "").strip()
        if not text:
            continue
        actions.append({"text": text[:40], "owner": owner if owner in store.agent_by else "ceo", "why": why[:40]})
    if not headline or len(findings) < 2 or not actions:
        return None
    return {"source": "model", "model": model, "headline": headline[:60],
            "findings": findings[:5], "actions": actions[:4], "data": data}


class DispatchIn(BaseModel):
    text: str
    owner: str = "ceo"
    why: str = ""


@app.post("/api/analysis/dispatch", status_code=201)
def dispatch_analysis(body: DispatchIn) -> d.Approval:
    """把分析建议一键变成待拍板卡，走同一条审批链路。"""
    owner = body.owner if body.owner in store.agent_by else "ceo"
    card = d.Approval(
        id=d.new_id("AP"),
        title=body.text[:60],
        **{"from": owner},
        summary=body.why or "由经营分析建议派发",
        impact="待补执行范围与截止时间",
        cost=0.2,
        risk="mid",
        evidence=d.Evidence(
            summary=body.why or "经营分析给出的建议",
            metrics=["来源：模拟店铺数据 · 近 7 天", "由数据分析 AI 生成"],
            reasons=[body.why] if body.why else ["经营数据出现偏离，需要动作"],
            risks=["建议来自模型/规则分析，执行前需确认范围"],
            rollback="未执行前可直接撤回",
            sources=["经营分析", "店铺数据摘要"],
        ),
    )
    store.approvals.insert(0, card)
    store.event(owner, f"分析建议已派发：{card.title}", "seal")
    store.audit_log("owner", "analysis.dispatch", card.id)
    store.save()
    return card


@app.get("/api/data/summary")
def data_summary() -> dict[str, Any]:
    return shopdata.summary()


_analysis_cache: dict[str, Any] = {"at": 0.0, "value": None}


@app.post("/api/analysis")
async def analyze(body: AnalyzeIn | None = None) -> dict[str, Any]:
    if body and body.run_id:
        run = store.demo_runs.get(body.run_id)
        if not run:
            raise HTTPException(404, "经营运行不存在")
        from .competition import metrics, rule_plan
        data = {"scope": "当前选中经营沙箱", "run_id": run["id"], "business": run["current"], "metrics": metrics(run["current"])}
        plan = rule_plan(run["current"])
        fallback = {"source": "rules", "model": "", "headline": plan["headline"], "findings": plan["findings"], "actions": [{"text": a["tool"], "owner": {"inventory.transfer": "sc", "ads.rebalance": "ad", "service.create_tickets": "cs"}[a["tool"]], "why": a["why"]} for a in plan["actions"]], "run_id": run["id"]}
        if llm.enabled():
            try:
                raw = await asyncio.wait_for(llm.chat([{"role": "system", "content": ANALYST_SYSTEM}, {"role": "user", "content": json.dumps(data, ensure_ascii=False)}], max_tokens=1400), timeout=65)
                parsed = _parse_json(raw)
                shaped = _shape(parsed, llm.current()["model"], data) if parsed else None
                if not shaped:
                    raise ValueError("模型返回结构无效")
                return {**shaped, "run_id": run["id"]}
            except Exception as exc:
                fallback["error"] = str(exc)[:200]
        return fallback
    data = shopdata.summary()
    focus_now = (body.focus if body else "").strip()
    if not focus_now and _analysis_cache["value"] and time.time() - _analysis_cache["at"] < 120:
        return {**_analysis_cache["value"], "cached": True}
    if not llm.enabled() or not llm.quick_ready():
        return shopdata.rule_based(data)
    focus = (body.focus if body else "").strip()
    user = f"数据：\n{shopdata.digest(data)}"
    if focus:
        user += f"\n\n本次重点关注：{focus}"
    try:
        raw = await asyncio.wait_for(
            llm.chat(
                [{"role": "system", "content": ANALYST_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.3,
                max_tokens=1400,
            ),
            timeout=60,
        )
        parsed = _parse_json(raw)
        shaped = _shape(parsed, llm.current()["model"], data) if parsed else None
        if shaped is None:
            raise ValueError("模型没有给出可解析的结构")
        store.event("ana", f"完成一次经营分析：{shaped['headline'][:24]}", "ok")
        store.save()
        _analysis_cache.update(at=time.time(), value=shaped)
        return shaped
    except Exception as e:
        out = shopdata.rule_based(data)
        out["error"] = str(e) or "分析超时"
        _analysis_cache.update(at=time.time(), value=out)
        return out


# ------------------------------ 业务场景演练 ------------------------------
@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [{"id": key, "title": case["title"], "description": case["description"], "metrics": case["metrics"]}
            for key, case in scenarios.CASES.items()]


@app.post("/api/scenarios/{case_id}/analyze")
async def analyze_scenario(case_id: str) -> dict:
    case = scenarios.CASES.get(case_id)
    if not case:
        raise HTTPException(404, "场景不存在")
    data = {"scenario_id": case_id, "title": case["title"], "metrics": case["metrics"]}
    error = ""
    if llm.enabled():
        try:
            prompt = f"演练场景：{case['title']}\n" + "\n".join(case['metrics']) + f"\n任务：{case['focus']}"
            raw = await asyncio.wait_for(llm.chat([{"role": "system", "content": ANALYST_SYSTEM},
                                                     {"role": "user", "content": prompt}], temperature=0.3, max_tokens=1400), timeout=60)
            parsed = _parse_json(raw)
            result = _shape(parsed, llm.current()["model"], data) if parsed else None
            if not result:
                raise ValueError("模型没有返回可解析的分析结果")
            store.event("ana", f"完成模拟场景分析：{case['title']}", "ok")
            store.save()
            return result
        except Exception as exc:
            error = str(exc) or "模型分析超时"
    else:
        error = "未配置模型"
    return {**case['fallback'], "source": "rules", "model": "", "data": data, "error": error}


class ScenarioDispatchIn(BaseModel):
    text: str
    owner: str
    why: str = ""
    source: str = "rules"
    model: str = ""


@app.post("/api/scenarios/{case_id}/dispatch", status_code=201)
def dispatch_scenario(case_id: str, body: ScenarioDispatchIn) -> d.Approval:
    case = scenarios.CASES.get(case_id)
    if not case:
        raise HTTPException(404, "场景不存在")
    if not body.text.strip() or body.source not in ("model", "rules"):
        raise HTTPException(400, "建议内容或分析来源无效")
    owner = body.owner if body.owner in store.agent_by else "ceo"
    source = f"模型分析：{body.model[:60]}" if body.source == "model" else "规则兜底"
    card = d.Approval(id=d.new_id("AP"), title=body.text.strip()[:60], **{"from": owner},
        summary=body.why.strip()[:120] or case['description'], impact="模拟演练，执行不会更改店铺数据", cost=0,
        risk="mid", evidence=d.Evidence(summary=case['description'], metrics=case['metrics'],
        reasons=[body.why.strip()[:120]] if body.why.strip() else ["请依据场景数据复核"],
        risks=["模拟数据与模型建议需要人工核实", "执行步骤仅模拟，不会修改店铺"],
        rollback="仅回滚演示任务状态，不涉及真实店铺", sources=[f"模拟场景：{case['title']}", source]))
    store.approvals.insert(0, card)
    store.event(owner, f"模拟场景「{case['title']}」建议已提交审批：{card.title}", "seal")
    store.audit_log("owner", "scenario.dispatch", card.id)
    store.save()
    return card


# ------------------------------ 任务 ------------------------------
def _refresh_tasks() -> None:
    dirty = False
    for t in store.tasks:
        if t.demo_run_id:
            continue
        tools.refresh(t)
        if t.status == "done" and not t.evented:
            t.evented = True
            store.event(t.from_, f"执行完成：{t.title}", "ok")
            store.audit_log("system", "task.done", t.id)
            dirty = True
    if dirty:
        store.save()


@app.get("/api/tasks")
def list_tasks() -> list[d.Task]:
    _refresh_tasks()
    return store.tasks


@app.post("/api/tasks/{tid}/rollback")
def rollback_task(tid: str) -> d.Task:
    t = next((x for x in store.tasks if x.id == tid), None)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.demo_run_id:
        run = store.demo_runs.get(t.demo_run_id)
        if not run:
            raise HTTPException(404, "对应沙箱运行不存在")
        store.demo_service.rollback_run(run)
        return t
    if not tools.rollback(t):
        raise HTTPException(409, "任务还在执行，不能回滚")
    t.evented = True
    store.event(t.from_, f"已回滚：{t.title}", "seal")
    store.audit_log("owner", "task.rollback", t.id)
    store.save()
    return t


@app.get("/api/audit")
def list_audit(limit: int = 100) -> list[d.AuditEntry]:
    return store.audit[-limit:]


# ------------------------------ 其他 ------------------------------
@app.get("/api/events")
def list_events(limit: int = 50) -> list[d.Event]:
    return store.events[-limit:]


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return store.dashboard()


from .competition import create_router
app.include_router(create_router(store))

from .evaluation import create_router as evaluation_router
app.include_router(evaluation_router(store))


# 桌面版同源提供已构建前端，保留开发版独立 Vite 服务。
if os.getenv("AIECOM_WEB_DIR"):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=os.environ["AIECOM_WEB_DIR"], html=True), name="desktop-web")
