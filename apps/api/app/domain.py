"""领域模型与会议状态机。

这一层不依赖 FastAPI，纯数据 + 规则，便于单测与后续换成真正的模型编排。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


def now_hm() -> str:
    return datetime.now().strftime("%H:%M")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------- 种子数据 ----------
class Agent(BaseModel):
    k: str
    name: str
    ch: str
    title: str
    tier: str
    color: str
    level: int
    kpi: list[str] = []
    perm: list[str] = []
    duty: list[str] = []


class Act(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    text: str
    risk: str
    due: str


class Opt(BaseModel):
    v: str
    label: str
    eff: str
    rec: bool = False
    act: Act


class Clash(BaseModel):
    by: list[str]
    text: str


class Agenda(BaseModel):
    t: str
    q: str
    says: list[tuple[str, str]]
    clash: Clash | None = None
    opts: list[Opt]


class Template(BaseModel):
    key: str
    title: str
    goal: str
    who: list[str]
    pre: list[tuple[str, str]]
    agenda: list[Agenda]


# ---------- 会议 ----------
class Note(BaseModel):
    id: str
    topic: str
    text: str
    at: str
    replies: list[tuple[str, str]]


class Decision(BaseModel):
    topic: str
    v: str
    label: str
    eff: str
    act: Act


class Meeting(BaseModel):
    id: str
    template: str
    title: str
    goal: str
    who: list[str]
    agenda: list[Agenda]
    topic: str | None = None
    stage: Literal["ready", "run", "done", "cancelled"] = "ready"
    idx: int = 0
    shown: int = 0
    phase: Literal["say", "pick"] = "say"
    mins: int = 0
    notes: list[Note] = []
    res: list[Decision] = []
    dispatched: bool = False
    created_at: str = Field(default_factory=now_hm)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost(self) -> float:
        return round(len(self.who) * 0.18 + self.mins * 0.05, 2)

    def agenda_now(self) -> Agenda:
        return self.agenda[self.idx]


# ---------- 审批与事件 ----------
class Evidence(BaseModel):
    summary: str
    metrics: list[str] = []
    reasons: list[str] = []
    risks: list[str] = []
    rollback: str = ""
    sources: list[str] = []


class Approval(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    title: str
    from_: str = Field(alias="from")
    summary: str
    impact: str
    cost: float
    risk: Literal["red", "mid", "low"]
    status: Literal["pending", "approved", "rejected"] = "pending"
    evidence: Evidence
    created_at: str = Field(default_factory=now_hm)
    demo_run_id: str | None = None
    discussion: list[dict[str, str]] = []


class Event(BaseModel):
    id: str
    at: str
    k: str
    text: str
    kind: Literal["seal", "ok"] | None = None


class Step(BaseModel):
    text: str
    at: str = ""
    status: Literal["wait", "running", "done"] = "wait"


class Task(BaseModel):
    """一次真实执行：审批通过后由工具层跑完，每一步都留痕。"""
    model_config = ConfigDict(populate_by_name=True)
    id: str
    title: str
    kind: str
    from_: str = Field(alias="from")
    approval_id: str
    demo_run_id: str | None = None
    risk: str
    status: Literal["running", "done", "failed"] = "running"
    steps: list[Step] = []
    snapshot: dict[str, Any] | None = None
    created_at: str = Field(default_factory=now_hm)
    created_ts: float = Field(default_factory=lambda: datetime.now().timestamp())
    finished_at: str | None = None
    evented: bool = False


class AuditEntry(BaseModel):
    seq: int
    at: str
    actor: str
    action: str
    target: str
    hash: str


# ---------- 主理人现场意见的关键词规则 ----------
FIRST_STEP = {
    "ceo": "目标和责任划分", "ops": "商品与价格口径", "ad": "素材与投放计划", "ct": "内容排期",
    "live": "直播脚本与货盘", "cs": "客服话术与工单", "rev": "评价区与差评回复", "sc": "备货与交期",
    "wms": "仓库与发货流程", "fin": "预算与现金流", "ana": "数据口径与看板", "cmp": "合规与资质",
    "vis": "视觉素材", "priv": "私域与老客", "cross": "多店与跨境",
}
CAVEAT = {
    "ceo": "责任要落到具体的人，不然最后还是没人推。",
    "ops": "价格一改，坑位和库存锁定也得跟着改，不然前台价和后台价会打架。",
    "ad": "素材是按老价格做的，改价之后点击率会变，ROI 得重跑一遍。",
    "ct": "排期已经发出去了，改方向就得重拍两条视频。",
    "live": "直播话术是按老价格背的，改价之前得重新对一遍口播。",
    "cs": "自动回复和话术要同步改，不然买家看到的价格和话术对不上。",
    "rev": "评价区还在按老卖点回复，口径一改，差评率可能先涨一下。",
    "sc": "供应商的锁价窗口只有三天，改量今天就得定。",
    "wms": "仓库已经按原单量排了波次，改量要提前一天说。",
    "fin": "预算和现金流是按原方案做的，加钱可以，但要先定这笔钱从哪出。",
    "ana": "口径一改，看板上的历史数据就不能直接比，得重算一版基线。",
    "cmp": "先确认这个改法在平台规则内，不然做完还要下架。",
    "vis": "视觉素材要重出，主图和详情页得一起换。",
    "priv": "老客是按原价买的，改价前要先把私域话术和补偿说清楚。",
    "cross": "多店的价格体系是联动的，单店改价要考虑其他店的价差。",
}
RULES: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"价|降价|涨价|折扣|毛利|利润"), ["ops", "fin", "ad"]),
    (re.compile(r"库存|补货|断货|交期|供应商|仓|物流|发货"), ["sc", "wms"]),
    (re.compile(r"退款|退货|售后|差评|投诉|客服|工单"), ["cs", "rev"]),
    (re.compile(r"预算|成本|现金|投放|ROI|加钱|费用"), ["fin", "ad", "ana"]),
    (re.compile(r"素材|视觉|主图|详情|视频|内容|脚本|直播|排期"), ["vis", "ct", "live"]),
    (re.compile(r"合规|资质|违规|平台|规则|下架"), ["cmp"]),
    (re.compile(r"数据|口径|看板|指标"), ["ana"]),
    (re.compile(r"目标|责任|分工|节奏"), ["ceo"]),
]


def _clip(text: str, n: int = 14) -> str:
    return text if len(text) <= n else text[:n] + "…"


def reply_for(text: str, who: list[str]) -> list[tuple[str, str]]:
    """按关键词挑两个参会 AI 回应。后续换成真实模型调用，签名保持不变。"""
    hit: list[str] = []
    for pattern, keys in RULES:
        if not pattern.search(text):
            continue
        for k in keys:
            if k in who and k not in hit:
                hit.append(k)
    for k in who:
        if k not in hit:
            hit.append(k)
    if not hit:
        return []
    a = hit[0]
    b = next((k for k in hit if k != a), a)
    return [
        (a, f"收到。「{_clip(text)}」我按这个方向重算一版{FIRST_STEP.get(a, '执行方案')}，有冲突先找你。"),
        (b, f"方向我认，但执行前得确认一句：{CAVEAT.get(b, '口径要先对齐。')}"),
    ]


# ---------- 状态机动作 ----------
def start_meeting(m: Meeting) -> Meeting:
    m.stage = "run"
    m.idx = 0
    m.shown = 0
    m.phase = "say"
    m.mins = 1
    m.notes = []
    m.res = []
    return m


def reveal_next(m: Meeting) -> tuple[str, str] | None:
    """推进一条发言；发言说完自动进入拍板阶段。"""
    if m.stage != "run":
        return None
    says = m.agenda_now().says
    if m.phase == "pick" or m.shown >= len(says):
        m.phase = "pick"
        return None
    line = says[m.shown]
    m.shown += 1
    m.mins += 1
    if m.shown >= len(says):
        m.phase = "pick"
    return line


def add_note(m: Meeting, text: str) -> Note:
    note = Note(
        id=new_id("N"),
        topic=m.agenda_now().t,
        text=text,
        at=f"第 {m.mins + 1} 分钟",
        replies=reply_for(text, m.who),
    )
    m.notes.append(note)
    m.mins += 3
    return note


def decide(m: Meeting, v: str) -> Decision:
    agenda = m.agenda_now()
    opt = next((o for o in agenda.opts if o.v == v), None)
    if opt is None:
        raise ValueError(f"没有这个方案：{v}")
    decision = Decision(topic=agenda.t, v=opt.v, label=opt.label, eff=opt.eff, act=opt.act)
    m.res.append(decision)
    if m.idx + 1 < len(m.agenda):
        m.idx += 1
        m.shown = 0
        m.phase = "say"
    else:
        m.stage = "done"
    return decision


def actions_of(m: Meeting) -> list[dict]:
    return [
        {
            "id": new_id("A"),
            "from": d.act.from_,
            "text": d.act.text,
            "due": d.act.due,
            "risk": d.act.risk,
            "topic": d.topic,
            "meeting": m.title,
        }
        for d in m.res
    ]


def approval_from_action(a: dict, note_count: int, meeting_id: str) -> Approval:
    return Approval(
        id=new_id("AP"),
        title=a["text"],
        **{"from": a["from"]},
        summary=f"{a['meeting']} · {a['topic']}",
        impact=f"截止 {a['due']}",
        cost=0.26,
        risk=a["risk"] if a["risk"] in ("red", "mid", "low") else "mid",
        evidence=Evidence(
            summary=f"由「{a['meeting']}」现场拍板产生的行动项",
            metrics=[f"议题：{a['topic']}", f"截止：{a['due']}", "算力成本：¥0.26"],
            reasons=[
                "会上各岗位从自己的 KPI 出发给出判断，分歧点已写进纪要",
                "拍板权只在主理人，AI 只提供依据与建议，不参与表决",
                f"会中主理人补充了 {note_count} 条现场意见，已写入纪要" if note_count else "本次未补充现场意见",
            ],
            risks=["执行依赖店铺接口权限与库存实际状态", "若上游价格/库存变动，需重新评估"],
            rollback="保留执行前快照，可在审批中心一键回滚",
            sources=[f"会议纪要 {meeting_id}", f"{a['topic']}"],
        ),
    )


def custom_agenda(topic: str, who: list[str]) -> list[Agenda]:
    """自定议题的可运行规则议程；不伪造调研数据或模型发言。"""
    return [Agenda(t=topic[:80], q="先明确需要核实的数据、负责人和执行边界，再决定是否派发。",
        says=[(k, f"【规则议程】关于{topic[:60]}，我负责核实{FIRST_STEP.get(k, '执行依据')}。{CAVEAT.get(k, '')}") for k in who],
        opts=[Opt(v="verify", label="先核实数据再执行", eff="生成待审批的核查行动项，不直接更改店铺", rec=True,
                  act=Act(**{"from": who[0]}, text=f"核实：{topic[:55]}", risk="mid", due="本日内")),
              Opt(v="defer", label="暂缓并补充依据", eff="记录暂缓原因与需要补充的数据",
                  act=Act(**{"from": who[0]}, text=f"补充依据：{topic[:50]}", risk="low", due="下次会议前"))])]
