import { useEffect, useMemo, useRef, useState } from 'react';
import { AGENTS } from './data/seed';
import MeetingDialog from './meeting/MeetingDialog';
import SettingsDialog from './settings/SettingsDialog';
import OrganizationDialog from './organization/OrganizationDialog';
import EvaluationPanel from './competition/EvaluationPanel';
import ApprovalTools from './competition/ApprovalTools';
import HistoryPanel from './history/HistoryPanel';
import RescuePanel from './competition/RescuePanel';
import { useRescueFlow } from './competition/useRescueFlow';
import { organizationApi } from './api';
import type { StaffConfig } from './api';
import type { ActionItem, MeetingRecord } from './meeting/MeetingDialog';
import { api, probe } from './api';
import type { ApiAnalysis, ApiApproval, ApiDashboard, ApiEvent, ApiModelStatus, ApiTask } from './api';

type Approval = ApiApproval;
type Ev = ApiEvent;

const RISK_LABEL: Record<Approval['risk'], string> = { red: '高风险', mid: '中风险', low: '低风险' };

const SEED_APPROVALS: Approval[] = [
  {
    id: 'AP-1041', title: '收纳柜预售价 429 → 369', from: 'ops', risk: 'red', cost: 0.42, status: 'pending',
    created_at: '',
    summary: '预售期定为 369，同步价保话术与比价素材',
    impact: '预计预售 2,400 件，毛利 22.6%；退货率超过 6% 即为亏损',
    evidence: {
      summary: '369 能进前三个比价坑位，但会成为老客新锚点',
      metrics: ['竞品最低价 379', '预售加购 3,120 人', '转化 2.1% → 预计 2.6%', '单件净利 8.4 元'],
      reasons: ['同款竞品最低 379，369 可进前三坑位', '价差每 10 元，点击转化约提升 0.4 个百分点', '大促前锁定价格锚点，避免当天临时降价'],
      risks: ['退货率超过 6% 时该价格亏损', '老客价保申请量预计上升', '大促后回调 429 的转化会明显下滑'],
      rollback: '保留原价快照，24 小时内可一键回调；价保规则随价格同时生效',
      sources: ['竞品监控 · 5 家', '历史大促退款率 4.8%', '财务毛利测算 v3']
    }
  },
  {
    id: 'AP-1042', title: '爆款收纳柜补货 800 件', from: 'sc', risk: 'mid', cost: 0.36, status: 'pending',
    created_at: '',
    summary: '向主供应商下 800 件补货单，付 30% 定金',
    impact: '断货风险 32%，占用现金 14 万，周转 41 天',
    evidence: {
      summary: '现金优先，断货风险留 32%',
      metrics: ['日均销量 46 件', '在库 610 件', '预计断货 4 天后', '可用现金 46 万'],
      reasons: ['日均出库 46 件，现有库存只够 13 天', '工厂交期 15 天，晚一天赶不上大促', '定金 30% 后现金流仍在安全线内'],
      risks: ['补 800 件仍有 32% 断货概率', '大促后退货可能带来二次压货'],
      rollback: '未排产前可改量；已付定金部分转为下批货款',
      sources: ['ERP 库存快照', '供应商报价单', '财务现金流表']
    }
  }
];

const SEED_EVENTS: Ev[] = [
  { id: 'e1', at: '08:00', k: 'ceo', text: '晨会派活完成：7 名 AI 收到今日任务，红线是「大促前不动主推价」', kind: null },
  { id: 'e2', at: '08:20', k: 'ops', text: '竞品价格巡检：3 个 SKU 出现 20 元以上价差，已生成调价建议', kind: null },
  { id: 'e3', at: '10:05', k: 'ad', text: '投放巡检：计划「收纳-拉新」ROI 3.2，低于 3.5 保本线，已暂停 2 条素材', kind: null },
  { id: 'e4', at: '10:40', k: 'sc', text: '库存预警：收纳柜预计 4 天后断货，已提交补货方案待拍板', kind: 'seal' },
  { id: 'e5', at: '11:10', k: 'fin', text: '现金流：本周可用 46 万，下周需付货款 18 万', kind: 'ok' }
];

const DASH_FALLBACK: ApiDashboard = { gmv: 12480, margin: 27.3, refund_rate: 2.4, pending: 2, cost: 3.42 };

let seq = 100;
const now = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

export default function App() {
  const [online, setOnline] = useState(false);
  const [approvals, setApprovals] = useState<Approval[]>(SEED_APPROVALS);
  const [events, setEvents] = useState<Ev[]>(SEED_EVENTS);
  const [dash, setDash] = useState<ApiDashboard>(DASH_FALLBACK);
  const [model, setModel] = useState<ApiModelStatus | null>(null);
  const [meetings, setMeetings] = useState<MeetingRecord[]>([]);
  const [open, setOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [orgOpen, setOrgOpen] = useState(false);
  const [mode, setMode] = useState<'solo' | 'team'>('solo');
  const [view, setView] = useState('主理人');
  const [staff, setStaff] = useState<Record<string, StaffConfig>>({});
  const [tab, setTab] = useState<'feed' | 'tasks' | 'analysis' | 'scenario' | 'history' | 'evaluation'>('feed');
  const [analysis, setAnalysis] = useState<ApiAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [tasks, setTasks] = useState<ApiTask[]>([]);
  const doneRef = useRef(-1);
  const [clock, setClock] = useState(now());
  const [toast, setToast] = useState('');

  useEffect(() => {
    const t = setInterval(() => setClock(now()), 20000);
    return () => clearInterval(t);
  }, []);

  // 启动时探活：接上后端就用真数据，接不上就本地演练
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await probe();
      if (cancelled) return;
      setOnline(ok);
      if (!ok) return;
      try {
        const [d, aps, evs, mdl] = await Promise.all([
          api.dashboard(), api.approvals(), api.events(60), api.model()
        ]);
        if (cancelled) return;
        setDash(d);
        setApprovals(aps);
        setEvents(evs);
        setModel(mdl);
        organizationApi.staff().then(setStaff).catch(() => {}); organizationApi.organization().then(o => setMode(o.mode)).catch(() => {});
      } catch {
        if (!cancelled) setOnline(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const loadTasks = async () => {
    try {
      const ts = await api.tasks();
      setTasks(ts);
      const finished = ts.filter((t) => t.status !== 'running').length;
      if (doneRef.current !== -1 && finished !== doneRef.current) {
        setEvents(await api.events(60));
        setDash(await api.dashboard());
      }
      doneRef.current = finished;
    } catch {
      /* 轮询失败保持上次结果 */
    }
  };

  useEffect(() => {
    if (!online) return;
    loadTasks();
    const t = setInterval(loadTasks, 1500);
    return () => clearInterval(t);
  }, [online]);

  const reload = async () => {
    const [d, aps, evs] = await Promise.all([api.dashboard(), api.approvals(), api.events(60)]);
    setDash(d);
    setApprovals(aps);
    setEvents(evs);
  };

  const rescue = useRescueFlow(online, async () => { await reload(); await loadTasks(); });

  const visibleStaff = Object.values(AGENTS).filter(a => (!staff[a.k] || staff[a.k].hired) && (view === '主理人' || staff[a.k]?.owner === view));
  const visibleApprovals = approvals.filter(a => view === '主理人' || (mode === 'team' && a.risk !== 'red' && staff[a.from]?.owner === view));
  const pending = approvals.filter((a) => a.status === 'pending').length;
  const running = tasks.filter((t) => t.status === 'running').length;
  const cost = useMemo(
    () => (online ? dash.cost : dash.cost + meetings.reduce((s, m) => s + m.cost, 0)),
    [online, dash.cost, meetings]
  );

  const pushEvent = (k: string, text: string, kind?: 'seal' | 'ok') =>
    setEvents((e) => [...e, { id: `e${++seq}`, at: clock, k, text, kind: kind ?? null }]);

  const decide = async (a: Approval, ok: boolean) => {
    if (online) {
      try {
        const updated = await api.decideApproval(a.id, ok, '', view);
        setApprovals((list) => list.map((x) => (x.id === a.id ? updated : x)));
        setEvents(await api.events(60));
        setDash(await api.dashboard());
        await loadTasks();
        await rescue.refresh();
        return;
      } catch (e) {
        setToast(`操作没同步：${(e as Error).message}`);
        return;
      }
    }
    setApprovals((list) => list.map((x) => (x.id === a.id ? { ...x, status: ok ? 'approved' : 'rejected' } : x)));
    pushEvent(a.from, ok ? `「${a.title}」已通过，开始执行` : `「${a.title}」被驳回`, ok ? 'ok' : 'seal');
  };

  const runAnalysis = async () => {
    setAnalyzing(true);
    try {
      setAnalysis(await api.analyze('', rescue.run?.id));
    } catch (e) {
      setToast(`分析失败：${(e as Error).message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const dispatchAction = async (a: { text: string; owner: string; why: string }) => {
    if (analysis?.run_id) { rescue.select(analysis.run_id); setTab('feed'); setToast('建议按当前沙箱进入联合方案，修改后重新复核审批'); return; }
    try {
      const card = await api.dispatchAnalysis(a);
      setApprovals((list) => [card, ...list]);
      setEvents(await api.events(60));
      setToast(`已派发到审批中心：${a.text}`);
    } catch (e) {
      setToast(`派发失败：${(e as Error).message}`);
    }
  };

  const rollback = async (t: ApiTask) => {
    try {
      const updated = await api.rollbackTask(t.id);
      setTasks((list) => list.map((x) => (x.id === updated.id ? updated : x)));
      setEvents(await api.events(60));
      setToast(`已回滚：${t.title}`);
    } catch (e) {
      setToast(`回滚失败：${(e as Error).message}`);
    }
  };

  const onDispatch = async ({ actions, record }: { actions: ActionItem[]; record: MeetingRecord }) => {
    setMeetings((m) => [...m, record]);
    if (online) {
      try {
        await reload();
        setToast(`会议已归档，${actions.length} 条行动项进入审批中心`);
        return;
      } catch (e) {
        setToast(`会议已结束，但列表刷新失败：${(e as Error).message}`);
      }
    }
    const cards: Approval[] = actions.map((a) => ({
      id: `AP-${++seq}`, title: a.text, from: a.from, risk: a.risk as Approval['risk'], cost: 0.26,
      status: 'pending', created_at: clock,
      summary: `${a.meeting} · ${a.topic}`,
      impact: `负责人 ${AGENTS[a.from]?.name} · 截止 ${a.due}`,
      evidence: {
        summary: `由「${a.meeting}」现场拍板产生的行动项`,
        metrics: [`议题：${a.topic}`, `截止：${a.due}`, '算力成本：¥0.26'],
        reasons: [
          '会上各岗位从自己的 KPI 出发给出判断，分歧点已写进纪要',
          '拍板权只在主理人，AI 只提供依据与建议，不参与表决',
          record.notes.length ? `会中主理人补充了 ${record.notes.length} 条现场意见，已写入纪要` : '本次未补充现场意见'
        ],
        risks: ['执行依赖店铺接口权限与库存实际状态', '若上游价格/库存变动，需重新评估'],
        rollback: '保留执行前快照，可在审批中心一键回滚',
        sources: [`会议纪要 ${record.id}`, `${record.res.length} 项决议`, `${actions.length} 条行动项`]
      }
    }));
    setApprovals((list) => [...cards, ...list]);
    pushEvent('ceo', `「${record.title}」结束：${record.res.length} 项决议，${actions.length} 条行动项派发到审批中心`, 'ok');
    setToast(`会议已归档，${actions.length} 条行动项进入审批中心`);
  };

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <b>AI 电商公司</b>
          <span>模拟运营 · {Object.values(AGENTS).filter(a => !staff[a.k] || staff[a.k].hired).length} 名 AI 员工</span>
        </div>
        <div className="clock"><small>营业中</small>{clock}</div>
        <div className="state">
          <span className={`dot${online ? ' live' : ''}`} style={online ? undefined : { background: 'var(--warn)' }} />
          {online ? '后端已连' : '本地演练'}
          {online && model && (
            <span className={`tagx ${model.connected ? 'ok' : ''}`} title={model.error || model.base_url}>
              {model.connected ? `模型 ${model.model}` : '模型未连通'}
            </span>
          )}
          · {pending > 0 ? `${pending} 件事等你拍板` : '暂无待拍板事项'}
        </div>
        <span className="sp" />
        {mode === 'team' && <label className="view-select">演示视角<select aria-label="当前视角" value={view} onChange={e => setView(e.target.value)}><option>主理人</option>{[...new Set(Object.values(staff).filter(s => s.hired && s.owner && s.owner !== '主理人').map(s => s.owner))].map(o => <option key={o}>{o}</option>)}</select></label>}
        <button className="btn" onClick={() => setTab('feed')}>经营救援演示</button>
        <button className="btn" onClick={() => setOrgOpen(true)}>员工库 / 店铺</button>
        <button className="btn" onClick={() => setSettingsOpen(true)}>模型配置</button>
        <button className="btn" onClick={() => setOpen(true)}>召集会议</button>
        <button
          className="btn primary"
          onClick={async () => {
            if (online) {
              try {
                await reload();
                setToast('已从后端刷新看板');
              } catch (e) {
                setToast(`刷新失败：${(e as Error).message}`);
              }
            } else {
              pushEvent('ceo', '手动触发一轮巡检，各岗位已接单');
            }
          }}
        >
          {online ? '刷新看板' : '跑一轮巡检'}
        </button>
      </header>

      <div className="kpis">
        <div className="kpi"><span>{rescue.run ? "本轮库存覆盖" : "历史示例 GMV"}</span><b>{rescue.run ? rescue.run.current_metrics.coverage_days : `¥${dash.gmv.toLocaleString()}`}<i>{rescue.run ? "天" : "演示"}</i></b></div>
        <div className="kpi"><span>{rescue.run ? "本轮预算加权 ROI（估算）" : "历史示例毛利率"}</span><b>{rescue.run ? rescue.run.current_metrics.estimated_roi.toFixed(3) : dash.margin}<i>{rescue.run ? "" : "%"}</i></b></div>
        <div className="kpi"><span>{rescue.run ? "本轮历史退款率" : "历史示例退款率"}</span><b>{rescue.run ? rescue.run.current_metrics.refund_rate : dash.refund_rate}<i>%</i></b></div>
        <div className="kpi"><span>在跑任务</span><b>{running}<i>个</i></b></div>
        <div className="kpi warn"><span>待你拍板</span><b>{pending}<i>件</i></b></div>
        <div className="kpi"><span>{rescue.run ? '规划与复核 / tokens' : '算力费用（演示估算）'}</span><b>{rescue.run ? rescue.run.model_calls.some(call => call.usage?.total_tokens !== undefined) ? rescue.run.model_calls.reduce((sum, call) => sum + (call.usage?.total_tokens || 0), 0).toLocaleString() : rescue.run.mode === 'live' ? '未报告' : '0' : `¥${cost.toFixed(2)}`}</b></div>
      </div>

      {rescue.run && <div className="rescue-statebar"><strong>经营救援沙箱</strong><span>库存覆盖 {rescue.run.current_metrics.coverage_days} 天</span><span>预算 ROI {rescue.run.current_metrics.estimated_roi.toFixed(3)}（估算）</span><span>售后工单 {rescue.run.current_metrics.ticket_count} / {rescue.run.current_metrics.damaged_orders}</span></div>}

      <div className="main">
        <section className="pane">
          <div className="pane-h"><span>AI 员工</span><span className="n">{Object.values(AGENTS).filter(a => !staff[a.k] || staff[a.k].hired).length}</span></div>
          <div className="scroll team">
            <div className="person">
              <span className="av human">理</span>
              <span><b>主理人</b><small>拍板 / 定方向</small></span>
            </div>
            {visibleStaff.map((a) => (
              <button className="agent" key={a.k} title={a.perm} onClick={() => setOrgOpen(true)}>
                <span className="av" style={{ background: a.color }}>{a.ch}</span>
                <span className="who"><b>{a.name}</b><small>{a.title}</small></span>
                <span className="pill">
                  {rescue.run?.status === 'running' && ((rescue.run.node === 'plan' && a.k === 'ceo') || (rescue.run.node === 'review' && a.k === 'cmp')) ? '分析中' : rescue.run?.status === 'awaiting_approval' && ['ceo', 'sc', 'ad', 'cs'].includes(a.k) ? '等审批' : rescue.run?.status === 'completed' && ['ceo', 'sc', 'ad', 'cs', 'cmp'].includes(a.k) ? '救援完成' : staff[a.k]?.owner || '待分配'}
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="pane">
          <div className="pane-h">
            <div className="tabs">
              <button className={tab === 'feed' ? 'on' : ''} onClick={() => setTab('feed')}>公司频道</button>
              <button className={tab === 'tasks' ? 'on' : ''} onClick={() => setTab('tasks')}>
                任务{running > 0 ? ` · ${running}` : ''}
              </button>
              <button className={tab === 'analysis' ? 'on' : ''} onClick={() => setTab('analysis')}>分析</button>
              <button className={tab === 'scenario' ? 'on' : ''} onClick={() => setTab('scenario')}>业务演练</button>
              <button className={tab === 'evaluation' ? 'on' : ''} onClick={() => setTab('evaluation')}>评测</button>
              <button className={tab === 'history' ? 'on' : ''} onClick={() => setTab('history')}>历史审计</button>
            </div>
            <span className="n">{tab === 'feed' ? `${events.length} 条` : tab === 'tasks' ? `${tasks.length} 个` : '模拟数据'}</span>
          </div>
          {tab === 'evaluation' ? <EvaluationPanel online={online} /> : tab === 'history' ? <HistoryPanel online={online} /> : tab === 'scenario' ? (
            <div className="scroll feed"><RescuePanel flow={rescue} online={online} onTasks={() => setTab('tasks')} /></div>
          ) : tab === 'analysis' ? (
            <AnalysisPanel
              result={analysis}
              busy={analyzing}
              online={online}
              onRun={runAnalysis}
              onDispatch={dispatchAction}
            />
          ) : tab === 'tasks' ? (
            <><div className="rescue-task-result" style={{ margin: '4px 14px' }}>{rescue.run?.receipts.length ? <><b>本轮经营救援</b><p>库存覆盖 {rescue.run.before_metrics.coverage_days} → {rescue.run.current_metrics.coverage_days} 天 · 工单 {rescue.run.current_metrics.ticket_count} / {rescue.run.current_metrics.damaged_orders} · {rescue.run.checks.filter(c => c.passed).length} 项执行校验通过{rescue.run.status === 'rolled_back' ? '（已回滚，校验为历史记录）' : ''}</p></> : '联合方案通过右侧审批后，任务列表将记录实际沙箱工具回执。'}</div><TaskList tasks={tasks} online={online} onRollback={async (t) => { await rollback(t); await rescue.refresh(); }} /></>
          ) : (
          <div className="scroll feed">
            <RescuePanel flow={rescue} online={online} onTasks={() => setTab('tasks')} />
            {[...events].filter(e => view === '主理人' || staff[e.k]?.owner === view).reverse().map((e) => {
              const a = AGENTS[e.k];
              return (
                <div className="ev" key={e.id}>
                  <span className="av sm" style={{ background: a?.color ?? 'var(--seal)' }}>{a?.ch ?? '经'}</span>
                  <div className={`bd${e.kind === 'seal' ? ' me' : ''}`}>
                    <b><span className="t">{e.at}</span>{a?.name ?? '系统'}
                      {e.kind === 'seal' && <span className="tagx seal">需拍板</span>}
                      {e.kind === 'ok' && <span className="tagx ok">已完成</span>}
                    </b>
                    <p>{e.text}</p>
                  </div>
                </div>
              );
            })}
          </div>
          )}
        </section>

        <section className="pane">
          <div className="pane-h"><span>审批中心</span><span className="n">{pending} 待拍板</span></div>
          <div className="scroll aps">
            {visibleApprovals.map((a) => (
              <article className={`ap risk-${a.risk} status-${a.status}`} key={a.id}>
                <div className="ap-h">
                  <div className="row">
                    <span className="av sm" style={{ background: AGENTS[a.from]?.color }}>{AGENTS[a.from]?.ch}</span>
                    <b>{a.title}</b>
                  </div>
                  <div className="meta">
                    <span>{a.id}</span><span>{AGENTS[a.from]?.name}</span>
                    <span>{RISK_LABEL[a.risk]}</span><span>审批人：{a.approver || '主理人'}</span><span>¥{a.cost.toFixed(2)}</span>
                  </div>
                  <p className="sum">{a.summary}</p>
                  <div className="meta"><span>{a.impact}</span></div>
                  <details>
                    <summary style={{ fontSize: 12, color: 'var(--accent)', cursor: 'pointer' }}>看决策依据</summary>
                    <div style={{ fontSize: 12.2, color: 'var(--muted)', marginTop: 6, display: 'grid', gap: 4 }}>
                      <div><b style={{ color: 'var(--ink)' }}>结论：</b>{a.evidence.summary}</div>
                      <div><b style={{ color: 'var(--ink)' }}>数据：</b>{a.evidence.metrics.join(' · ')}</div>
                      <div><b style={{ color: 'var(--ink)' }}>理由：</b>{a.evidence.reasons.join('；')}</div>
                      <div><b style={{ color: 'var(--ink)' }}>风险：</b>{a.evidence.risks.join('；')}</div>
                      <div><b style={{ color: 'var(--ink)' }}>回滚：</b>{a.evidence.rollback}</div>
                      <div><b style={{ color: 'var(--ink)' }}>出处：</b>{a.evidence.sources.join(' · ')}</div>
                    </div>
                  </details>
                </div>
                <ApprovalTools approval={a} onChanged={async () => { await reload(); await rescue.refresh(); }} onOpenRun={id => { rescue.select(id); setTab('feed'); }} />
                <div className="ap-f">
                  {a.status === 'pending' ? (
                    <>
                      <button className="btn tiny" onClick={() => decide(a, false)}>驳回</button>
                      <span className="sp" />
                      <button className="btn tiny seal" onClick={() => decide(a, true)}>通过并模拟执行</button>
                    </>
                  ) : (
                    <span className={`done${a.status === 'rejected' ? ' no' : ''}`}>
                      {a.status === 'approved'
                        ? `已通过 · ${(() => {
                            const t = tasks.find((x) => x.approval_id === a.id);
                            return !t ? '排队中' : t.status === 'done' ? '执行完成' : t.status === 'failed' ? (t.demo_run_id && !t.snapshot?.rollback_verified ? '执行失败' : '已回滚') : '执行中';
                          })()}`
                        : '已驳回'}
                    </span>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      </div>

      {toast && (
        <div className="toast" onClick={() => setToast('')}>
          {toast}
          <span>×</span>
        </div>
      )}

      <OrganizationDialog open={orgOpen} online={online} onClose={() => setOrgOpen(false)} onChanged={() => { organizationApi.staff().then(setStaff).catch(() => {}); organizationApi.organization().then(o => { setMode(o.mode); if (o.mode === 'solo') setView('主理人'); }).catch(() => {}); }} />

      <SettingsDialog
        open={settingsOpen}
        online={online}
        onClose={() => setSettingsOpen(false)}
        onSaved={(s) => setModel(s)}
      />

      <MeetingDialog
        open={open}
        online={online}
        staff={staff}
        modelName={model?.connected ? model.model : undefined}
        onClose={() => setOpen(false)}
        onDispatch={onDispatch}
      />
    </div>
  );
}

function TaskList({ tasks, online, onRollback }: { tasks: ApiTask[]; online: boolean; onRollback: (t: ApiTask) => void }) {
  if (!online) return <div className="empty">后端未连接，任务执行链路不可用</div>;
  if (!tasks.length) return <div className="empty">还没有任务。通过一张审批卡，就会生成执行任务。</div>;
  return (
    <div className="scroll tasks">
      {tasks.map((t) => {
        const done = t.steps.filter((s) => s.status === 'done').length;
        const pct = Math.round((done / t.steps.length) * 100);
        const a = AGENTS[t.from];
        return (
          <article className={`task st-${t.status}`} key={t.id}>
            <div className="th">
              <span className="av sm" style={{ background: a?.color }}>{a?.ch}</span>
              <b>{t.title}</b><span className="tagx">{t.demo_run_id ? '沙箱工具执行' : '步骤演示'}</span>
              <span className={`pill ${t.status === 'running' ? 'work' : t.status === 'failed' ? 'wait' : ''}`}>
                {t.status === 'running' ? '执行中' : t.status === 'done' ? '已完成' : t.demo_run_id && !t.snapshot?.rollback_verified ? '执行失败' : '已回滚'}
              </span>
            </div>
            <div className="prog"><i style={{ width: `${pct}%` }} /></div>
            <div className="tmeta">
              {t.id} · {done}/{t.steps.length} 步 · {t.finished_at ? `完成于 ${t.finished_at}` : '执行中'}
              {t.snapshot?.kind ? ` · 快照 ${String(t.snapshot.kind)}` : ''}
            </div>
            <details>
              <summary>看执行步骤</summary>
              <ol className="steps">
                {t.steps.map((s, i) => (
                  <li key={i} className={s.status}>
                    <span>{s.text}</span>
                    <em>{s.status === 'done' ? s.at : s.status === 'running' ? '进行中' : '待执行'}</em>
                  </li>
                ))}
              </ol>
            </details>
            <div className="tact">
              <span className="sp" />
              {t.status === 'done' && (
                <button className="btn tiny" onClick={() => onRollback(t)}>回滚</button>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function AnalysisPanel({ result, busy, online, onRun, onDispatch }: {
  result: ApiAnalysis | null;
  busy: boolean;
  online: boolean;
  onRun: () => void;
  onDispatch: (a: { text: string; owner: string; why: string }) => void;
}) {
  return (
    <div className="scroll analysis">
      <div className="an-bar">
        <div>
          <b>经营分析</b>
          <small>模拟店铺数据 · 近 7 天经营数据 / 历史售后样本 / 3 条差评</small>
        </div>
        <button className="btn primary" onClick={onRun} disabled={!online || busy}>
          {busy ? '分析中…' : '让书生分析'}
        </button>
      </div>

      {!result && (
        <div className="empty">
          点「让书生分析」，把近 7 天的订单、退款、评价与投放数据交给模型，
          <br />它会给出结论、关键发现和可直接派发的动作。
        </div>
      )}

      {result && (
        <>
          <div className="an-src">
            <span className={`tagx ${result.source === 'model' ? 'ok' : ''}`}>
              {result.source === 'model' ? `书生 · ${result.model}` : '规则兜底'}
            </span>
            {result.error && <span className="an-err">模型未连通：{result.error}</span>}
          </div>

          <p className="an-head">{result.headline}</p>

          <div className="an-sec">
            <h5>关键发现</h5>
            <ul className="an-list">
              {result.findings.map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          </div>

          <div className="an-sec">
            <h5>建议动作 · 可直接派发到审批中心</h5>
            {result.actions.map((a, i) => (
              <div className="an-act" key={i}>
                <span className="av sm" style={{ background: AGENTS[a.owner]?.color }}>
                  {AGENTS[a.owner]?.ch ?? '经'}
                </span>
                <div>
                  <b>{a.text}</b>
                  <small>{AGENTS[a.owner]?.name ?? a.owner}{a.why ? ` · ${a.why}` : ''}</small>
                </div>
                <button className="btn tiny" onClick={() => onDispatch(a)}>派发</button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
