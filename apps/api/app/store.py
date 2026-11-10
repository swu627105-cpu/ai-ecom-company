"""存储层：先落 JSON 文件，接口按仓储边界设计，后续换 Postgres 只改这一个文件。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .paths import DATA_DIR
from .domain import Agent, Approval, AuditEntry, Event, Evidence, Meeting, Task, Template, now_hm, new_id

ROOT = Path(__file__).resolve().parent
SEED = ROOT / "seed.json"
DATA = DATA_DIR / "state.json"

SEED_APPROVALS = [
    {
        "id": "AP-1041",
        "title": "收纳柜预售价 429 → 369",
        "from": "ops",
        "risk": "red",
        "cost": 0.42,
        "summary": "预售期定为 369，同步价保话术与比价素材",
        "impact": "预计预售 2,400 件，毛利 22.6%；退货率超过 6% 即为亏损",
        "evidence": {
            "summary": "369 能进前三个比价坑位，但会成为老客新锚点",
            "metrics": ["竞品最低价 379", "预售加购 3,120 人", "转化 2.1% → 预计 2.6%", "单件净利 8.4 元"],
            "reasons": ["同款竞品最低 379，369 可进前三坑位", "价差每 10 元，点击转化约提升 0.4 个百分点", "大促前锁定价格锚点，避免当天临时降价"],
            "risks": ["退货率超过 6% 时该价格亏损", "老客价保申请量预计上升", "大促后回调 429 的转化会明显下滑"],
            "rollback": "保留原价快照，24 小时内可一键回调；价保规则随价格同时生效",
            "sources": ["竞品监控 · 5 家", "历史大促退款率 4.8%", "财务毛利测算 v3"],
        },
    },
    {
        "id": "AP-1042",
        "title": "爆款收纳柜补货 800 件",
        "from": "sc",
        "risk": "mid",
        "cost": 0.36,
        "summary": "向主供应商下 800 件补货单，付 30% 定金",
        "impact": "断货风险 32%，占用现金 14 万，周转 41 天",
        "evidence": {
            "summary": "现金优先，断货风险留 32%",
            "metrics": ["日均销量 46 件", "在库 610 件", "预计断货 4 天后", "可用现金 46 万"],
            "reasons": ["日均出库 46 件，现有库存只够 13 天", "工厂交期 15 天，晚一天赶不上大促", "定金 30% 后现金流仍在安全线内"],
            "risks": ["补 800 件仍有 32% 断货概率", "大促后退货可能带来二次压货"],
            "rollback": "未排产前可改量；已付定金部分转为下批货款",
            "sources": ["ERP 库存快照", "供应商报价单", "财务现金流表"],
        },
    },
]

SEED_EVENTS = [
    {"k": "ceo", "text": "晨会派活完成：7 名 AI 收到今日任务，红线是「大促前不动主推价」"},
    {"k": "ops", "text": "竞品价格巡检：3 个 SKU 出现 20 元以上价差，已生成调价建议"},
    {"k": "ad", "text": "投放巡检：计划「收纳-拉新」ROI 3.2，低于 3.5 保本线，已暂停 2 条素材"},
    {"k": "sc", "text": "库存预警：收纳柜预计 4 天后断货，已提交补货方案待拍板", "kind": "seal"},
    {"k": "fin", "text": "现金流：本周可用 46 万，下周需付货款 18 万", "kind": "ok"},
]


class Store:
    def __init__(self, persist: bool = True) -> None:
        seed = json.loads(SEED.read_text(encoding="utf-8"))
        self.agents: list[Agent] = [Agent(**a) for a in seed["agents"]]
        self.agent_by: dict[str, Agent] = {a.k: a for a in self.agents}
        self.stance: dict[str, str] = seed["stance"]
        self.templates: dict[str, Template] = {
            k: Template(**v) for k, v in seed["meetings"].items()
        }
        self.staff = {a.k: {"hired": True, "owner": "主理人", "level": a.level} for a in self.agents}
        self.shops: list[dict] = []
        self.organization = {"mode": "solo"}
        self.demo_runs: dict[str, dict] = {}
        self.evaluations: dict[str, dict] = {}
        self.meetings: dict[str, Meeting] = {}
        self.tasks: list[Task] = []
        self.audit: list[AuditEntry] = []
        self.approvals: list[Approval] = [
            Approval(**a, created_at=now_hm()) for a in SEED_APPROVALS
        ]
        self.events: list[Event] = [
            Event(id=f"e{i + 1}", at=now_hm(), **e) for i, e in enumerate(SEED_EVENTS)
        ]
        self.persist = persist and os.getenv("AIECOM_PERSIST", "1") != "0"
        self._seq = 1
        if self.persist and DATA.exists():
            self._load()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
        self.staff.update(raw.get("staff", {}))
        self.shops = raw.get("shops", [])
        self.organization = raw.get("organization", {"mode": "solo"})
        self.demo_runs = raw.get("demo_runs", {})
        self.evaluations = raw.get("evaluations", {})
        self.meetings = {k: Meeting(**v) for k, v in raw.get("meetings", {}).items()}
        self.approvals = [Approval(**a) for a in raw.get("approvals", [])]
        self.events = [Event(**e) for e in raw.get("events", [])]
        seen_ids = set()
        for event in self.events:
            if event.id in seen_ids:
                event.id = new_id("EV")
            seen_ids.add(event.id)
        self.tasks = [Task(**t) for t in raw.get("tasks", [])]
        self.audit = [AuditEntry(**a) for a in raw.get("audit", [])]
        self._seq = raw.get("seq", 1)

    def save(self) -> None:
        if not self.persist:
            return
        DATA.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "demo_runs": self.demo_runs,
            "evaluations": self.evaluations,
            "staff": self.staff,
            "shops": self.shops,
            "organization": self.organization,
            "meetings": {k: m.model_dump(by_alias=True) for k, m in self.meetings.items()},
            "approvals": [a.model_dump(by_alias=True) for a in self.approvals],
            "events": [e.model_dump() for e in self.events],
            "tasks": [t.model_dump(by_alias=True) for t in self.tasks],
            "audit": [a.model_dump() for a in self.audit],
            "seq": self._seq,
        }
        # 同目录原子替换，避免进程中断留下截断的JSON检查点。
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA.parent, delete=False) as f:
            temporary = Path(f.name)
            try:
                json.dump(payload, f, ensure_ascii=False, indent=1, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        os.replace(temporary, DATA)

    # ---------- 事件流 ----------
    def event(self, k: str, text: str, kind: str | None = None) -> Event:
        self._seq += 1
        e = Event(id=new_id("EV"), at=now_hm(), k=k, text=text, kind=kind)  # type: ignore[arg-type]
        self.events.append(e)
        return e

    def audit_log(self, actor: str, action: str, target: str) -> AuditEntry:
        prev = self.audit[-1].hash if self.audit else "genesis"
        seq = len(self.audit) + 1
        at = now_hm()
        digest = hashlib.sha256(f"{prev}|{seq}|{actor}|{action}|{target}|{at}".encode()).hexdigest()[:12]
        entry = AuditEntry(seq=seq, at=at, actor=actor, action=action, target=target, hash=digest)
        self.audit.append(entry)
        return entry

    def dashboard(self) -> dict:
        pending = sum(1 for a in self.approvals if a.status == "pending")
        running = sum(1 for t in self.tasks if t.status == "running")
        cost = 3.42 + sum(m.cost for m in self.meetings.values()) + pending * 0.02 + len(self.tasks) * 0.08
        return {
            "gmv": 12480,
            "margin": 27.3,
            "refund_rate": 2.4,
            "pending": pending,
            "running": running,
            "tasks": len(self.tasks),
            "cost": round(cost, 2),
        }


def build_evidence_reasons(note_count: int) -> list[str]:
    base = [
        "会上各岗位从自己的 KPI 出发给出判断，分歧点已写进纪要",
        "拍板权只在主理人，AI 只提供依据与建议，不参与表决",
    ]
    if note_count:
        base.append(f"会中主理人补充了 {note_count} 条现场意见，已写入纪要")
    return base


__all__ = ["Store", "Evidence", "build_evidence_reasons"]
