import { useEffect, useMemo, useRef, useState } from 'react';
import { AGENTS, MEETINGS, STANCE, TEMPLATE_ORDER } from '../data/seed';
import type { Template } from '../data/seed';
import { api } from '../api';
import type { ApiAction, ApiAgenda, ApiMeeting, StaffConfig } from '../api';

export type ActionItem = {
  id: string; from: string; text: string; due: string; risk: string; topic: string; meeting: string;
};
export type MeetingRecord = {
  id: string; title: string; mins: number; cost: number;
  res: { topic: string; label: string; eff: string; v: string }[];
  notes: { t: string; text: string; at: string }[];
  written: [string, string][];
};
type Props = {
  open: boolean;
  online: boolean;
  modelName?: string;
  staff?: Record<string, StaffConfig>;
  onClose: () => void;
  onDispatch: (p: { actions: ActionItem[]; record: MeetingRecord }) => void;
};

const FIRST_STEP: Record<string, string> = {
  ceo: '目标和责任划分', ops: '商品与价格口径', ad: '素材与投放计划', ct: '内容排期', live: '直播脚本与货盘',
  cs: '客服话术与工单', rev: '评价区与差评回复', sc: '备货与交期', wms: '仓库与发货流程', fin: '预算与现金流',
  ana: '数据口径与看板', cmp: '合规与资质', vis: '视觉素材', priv: '私域与老客', cross: '多店与跨境'
};
const CAVEAT: Record<string, string> = {
  ceo: '责任要落到具体的人，不然最后还是没人推。',
  ops: '价格一改，坑位和库存锁定也得跟着改，不然前台价和后台价会打架。',
  ad: '素材是按老价格做的，改价之后点击率会变，ROI 得重跑一遍。',
  ct: '排期已经发出去了，改方向就得重拍两条视频。',
  live: '直播话术是按老价格背的，改价之前得重新对一遍口播。',
  cs: '自动回复和话术要同步改，不然买家看到的价格和话术对不上。',
  rev: '评价区还在按老卖点回复，口径一改，差评率可能先涨一下。',
  sc: '供应商的锁价窗口只有三天，改量今天就得定。',
  wms: '仓库已经按原单量排了波次，改量要提前一天说。',
  fin: '预算和现金流是按原方案做的，加钱可以，但要先定这笔钱从哪出。',
  ana: '口径一改，看板上的历史数据就不能直接比，得重算一版基线。',
  cmp: '先确认这个改法在平台规则内，不然做完还要下架。',
  vis: '视觉素材要重出，主图和详情页得一起换。',
  priv: '老客是按原价买的，改价前要先把私域话术和补偿说清楚。',
  cross: '多店的价格体系是联动的，单店改价要考虑其他店的价差。'
};
const RULES: [RegExp, string[]][] = [
  [/价|降价|涨价|折扣|毛利|利润/, ['ops', 'fin', 'ad']],
  [/库存|补货|断货|交期|供应商|仓|物流|发货/, ['sc', 'wms']],
  [/退款|退货|售后|差评|投诉|客服|工单/, ['cs', 'rev']],
  [/预算|成本|现金|投放|ROI|加钱|费用/, ['fin', 'ad', 'ana']],
  [/素材|视觉|主图|详情|视频|内容|脚本|直播|排期/, ['vis', 'ct', 'live']],
  [/合规|资质|违规|平台|规则|下架/, ['cmp']],
  [/数据|口径|看板|指标/, ['ana']],
  [/目标|责任|分工|节奏/, ['ceo']]
];
const clip = (s: string, n = 14) => (s.length > n ? s.slice(0, n) + '…' : s);

type Note = { id?: string; t: string; text: string; at: string; rep: [string, string][] };
type Stage = 'setup' | 'run' | 'done';
const toAgendas = (t: Template): ApiAgenda[] => t.agenda as unknown as ApiAgenda[];

export default function MeetingDialog({ open, online, modelName, staff = {}, onClose, onDispatch }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const busy = useRef(false);

  const [stage, setStage] = useState<Stage>('setup');
  const [tplKey, setTplKey] = useState(TEMPLATE_ORDER[0]);
  const [who, setWho] = useState<string[]>(MEETINGS[TEMPLATE_ORDER[0]].who);
  const [topic, setTopic] = useState('');
  const [mid, setMid] = useState<string | null>(null);
  const [agendaList, setAgendaList] = useState<ApiAgenda[]>(toAgendas(MEETINGS[TEMPLATE_ORDER[0]]));
  const [idx, setIdx] = useState(0);
  const [lines, setLines] = useState<[string, string][]>([]);
  const [phase, setPhase] = useState<'say' | 'pick'>('say');
  const [notes, setNotes] = useState<Note[]>([]);
  const [res, setRes] = useState<{ topic: string; opt: ApiAgenda['opts'][number] }[]>([]);
  const [mins, setMins] = useState(0);
  const [costApi, setCostApi] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [paused, setPaused] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [err, setErr] = useState('');

  const closeMeeting = async () => { try { if (online && mid && stage === 'run') await api.cancelMeeting(mid); onClose(); } catch (e) { setErr(`散会未同步：${(e as Error).message}`); } };
  const agenda = agendaList[idx];
  const cost = costApi ?? +(who.length * 0.18 + mins * 0.05).toFixed(2);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setStage('setup'); setIdx(0); setLines([]); setPhase('say');
    setNotes([]); setRes([]); setMins(0); setDraft(''); setTopic('');
    setMid(null); setCostApi(null); setErr(''); busy.current = false;
    setAgendaList(toAgendas(MEETINGS[tplKey]));
    setWho(w => w.filter(k => staff[k]?.hired !== false));
  }, [open, tplKey]);

  // 逐条发言：本地模式推模板，在线模式推后端状态机
  useEffect(() => {
    if (!open || paused || stage !== 'run' || phase !== 'say' || !agenda) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      if (cancelled) return;
      if (!online || !mid) {
        if (lines.length < agenda.says.length) {
          setLines((l) => [...l, agenda.says[l.length]]);
          setMins((m) => m + 1);
        } else {
          setPhase('pick');
        }
        return;
      }
      if (busy.current) return;
      busy.current = true;
      try {
        const r = await api.next(mid);
        if (cancelled) return;
        if (r.line) { setLines((l) => [...l, r.line as [string, string]]); setMins(r.mins); }
        setCostApi(r.cost);
        if (r.phase === 'pick') setPhase('pick');
      } catch {
        if (!cancelled) setPhase('pick');
      } finally {
        busy.current = false;
      }
    }, 820 / speed);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [open, stage, phase, lines.length, idx, agenda, online, mid, paused, speed]);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, notes, phase, idx]);

  const pickTpl = (k: string) => { setTplKey(k); setWho(MEETINGS[k].who.filter(key => staff[key]?.hired !== false)); };
  const toggleWho = (k: string) =>
    setWho((w) => (w.includes(k) ? w.filter((x) => x !== k) : [...w, k]));

  const start = async () => {
    setErr(''); setPaused(false);
    if (tplKey === 'custom' && !topic.trim()) { setErr('请填写自定义议题'); return; }
    if (!online && tplKey === 'custom') { setErr('自定义会议需要连接后端'); return; }
    if (!online) {
      setAgendaList(toAgendas(MEETINGS[tplKey]));
      setStage('run'); setMins(1); setLines([]); setPhase('say');
      return;
    }
    try {
      const m: ApiMeeting = await api.createMeeting(tplKey, who, topic.trim());
      setMid(m.id);
      setAgendaList(m.agenda);
      await api.start(m.id);
      setStage('run'); setMins(1); setLines([]); setPhase('say');
    } catch (e) {
      setErr(`会议未启动：${(e as Error).message}`);
    }
  };

  const decide = async (opt: ApiAgenda['opts'][number]) => {
    const current = agenda;
    const next = [...res, { topic: current.t, opt }];
    let nextIdx = idx + 1;
    let done = idx + 1 >= agendaList.length;
    if (online && mid) {
      try {
        const r = await api.decide(mid, opt.v);
        nextIdx = r.idx;
        done = r.stage === 'done';
        setCostApi(r.cost);
      } catch (e) {
        setErr(`拍板没同步到后端：${(e as Error).message}`); return;
      }
    }
    setRes(next);
    if (done) setStage('done');
    else { setIdx(nextIdx); setLines([]); setPhase('say'); }
  };

  const reply = (text: string): [string, string][] => {
    const pool = who.slice();
    const hit: string[] = [];
    RULES.forEach(([re, ks]) => {
      if (!re.test(text)) return;
      ks.forEach((k) => { if (pool.includes(k) && !hit.includes(k)) hit.push(k); });
    });
    pool.forEach((k) => { if (!hit.includes(k)) hit.push(k); });
    const a = hit[0]; const b = hit.find((k) => k !== a) ?? pool[1] ?? a;
    return [
      [a, `收到。「${clip(text)}」我按这个方向重算一版${FIRST_STEP[a] ?? '执行方案'}，有冲突先找你。`],
      [b, `方向我认，但执行前得确认一句：${CAVEAT[b] ?? '口径要先对齐。'}`]
    ];
  };

  const say = async () => {
    const text = draft.trim();
    if (!text) return;
    if (online && mid) {
      try {
        const r = await api.say(mid, text);
        setNotes((n) => [...n, { id: r.note.id, t: r.note.topic, text: r.note.text, at: r.note.at, rep: r.note.replies }]);
        setMins(r.mins);
        setCostApi(r.cost);
        setDraft('');
        return;
      } catch (e) {
        setErr(`发言没同步到后端：${(e as Error).message}`); return;
      }
    }
    setNotes((n) => [...n, { t: agenda.t, text, at: `第 ${mins + 1} 分钟`, rep: reply(text) }]);
    setMins((m) => m + 3);
    setDraft('');
  };

  const written = useMemo(() => {
    const out: [string, string][] = [];
    Object.keys(AGENTS).forEach((k) => {
      if (!who.includes(k) && out.length < 2) out.push([k, STANCE[k]]);
    });
    return out;
  }, [who]);

  const dispatch = async () => {
    let actions: ActionItem[] = res.map((r, i) => ({
      id: `A-local-${i}`,
      from: r.opt.act.from, text: r.opt.act.text, due: r.opt.act.due,
      risk: r.opt.act.risk, topic: r.topic, meeting: tplKey === 'custom' ? `临时会 · ${topic}` : MEETINGS[tplKey].title
    }));
    if (online && mid) {
      try {
        const r = await api.finish(mid);
        actions = r.actions as ApiAction[] as ActionItem[];
      } catch (e) {
        setErr(`派发没同步到后端：${(e as Error).message}`); return;
      }
    }
    onDispatch({
      actions,
      record: {
        id: mid ?? `M-local-${Date.now()}`, title: tplKey === 'custom' ? `临时会 · ${topic}` : MEETINGS[tplKey].title, mins, cost,
        res: res.map((r) => ({ topic: r.topic, label: r.opt.label, eff: r.opt.eff, v: r.opt.v })),
        notes: notes.map((n) => ({ t: n.t, text: n.text, at: n.at })),
        written
      }
    });
    onClose();
  };

  return (
    <dialog ref={ref} onClose={onClose} onCancel={(e) => { e.preventDefault(); void closeMeeting(); }}>
      <div className="dlg-h">
        <div>
          <h3>{stage === 'setup' ? '召集会议' : tplKey === 'custom' ? `临时会 · ${topic}` : MEETINGS[tplKey].title}</h3>
          <p>
            {stage === 'setup'
              ? '选一个模板，指定参会 AI，像真开会一样把分歧摆到桌面上'
              : MEETINGS[tplKey].goal}
            <span className={`tagx ${online ? 'ok' : ''}`} style={{ marginLeft: 8 }}>
              {online ? `模板议程 · ${modelName ? "现场意见可用书生" : "后端状态机"}` : '本地演练'}
            </span>
          </p>
        </div>
        <button className="x" onClick={() => void closeMeeting()} aria-label="关闭">×</button>
      </div>

      {err && <div className="warnbar">{err}</div>}

      {stage === 'setup' && (
        <>
          <div className="dlg-b">
            <div className="tpls">
              {TEMPLATE_ORDER.map((k) => (
                <button key={k} className={`tpl${k === tplKey ? ' on' : ''}`} onClick={() => pickTpl(k)}>
                  <b>{MEETINGS[k].title}</b>
                  <p>{MEETINGS[k].goal}</p>
                </button>
              ))}
            </div>
            <div className="setup-f">
              <div className="field">
                <label>议题补充（可留空，默认按模板议程）</label>
                <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="例如：本次只讨论预售价与补货量" />
              </div>
              <div className="field">
                <label>参会 AI · 已选 {who.length} 人（未参会者只出书面意见，不参与表决）</label>
                <div className="chips">
                  {Object.values(AGENTS).filter(a => staff[a.k]?.hired !== false).map((a) => (
                    <button key={a.k} className={`chip${who.includes(a.k) ? ' on' : ''}`} onClick={() => toggleWho(a.k)}>
                      <span className="av" style={{ background: a.color }}>{a.ch}</span>
                      {a.name}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
          <div className="setup-acts">
            <button className="btn" onClick={() => void closeMeeting()}>取消</button>
            <button className="btn primary" onClick={start} disabled={who.length < 2}>开始会议</button>
          </div>
        </>
      )}

      {stage === 'run' && agenda && (
        <>
          <div className="meet-top">
            <button className="btn tiny" onClick={() => setPaused(p => !p)}>{paused ? '继续发言' : '暂停发言'}</button><select aria-label="会议播放速度" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select>
            <span>议题 <b>{idx + 1}/{agendaList.length}</b></span>
            <span>参会 <b>{who.length}</b> 人</span>
            <span>用时 <b>{mins}</b> 分钟</span>
            <span>演示费用估算 <b>¥{cost.toFixed(2)}</b></span>
          </div>
          <div className="meet-grid">
            <div className="meet-main">
              <div className="stream" ref={streamRef}>
                <div className="prep">
                  <b>{agenda.t}</b>
                  <p>{agenda.q}</p>
                  <ul>
                    {MEETINGS[tplKey].pre.map(([k, v]) => <li key={k}><b>{k}：</b>{v}</li>)}
                  </ul>
                </div>

                {lines.map(([k, text], i) => {
                  const a = AGENTS[k];
                  return (
                    <div className="say" key={i}>
                      <span className="av sm" style={{ background: a?.color }}>{a?.ch ?? '·'}</span>
                      <div className="bd">
                        <b>{a?.name ?? k}<em>{a?.title}</em></b>
                        <p>{text}</p>
                      </div>
                    </div>
                  );
                })}

                {notes.filter((n) => n.t === agenda.t).map((n, i) => (
                  <NoteBlock key={`n${i}`} note={n} />
                ))}

                {phase === 'pick' && agenda.clash && (
                  <div className="clash">
                    <span className="av sm" style={{ background: 'var(--seal)' }}>歧</span>
                    <div className="bd">
                      <b>分歧 · {agenda.clash.by.map((k) => AGENTS[k]?.name ?? k).join(' vs ')}</b>
                      <p>{agenda.clash.text}</p>
                    </div>
                  </div>
                )}

                {phase === 'pick' && (
                  <div className="mopts">
                    {agenda.opts.map((o) => (
                      <button key={o.v} className={`mopt${o.rec ? ' rec' : ''}`} onClick={() => decide(o)}>
                        <span>
                          <b>{o.label}{o.rec && <span className="tagx seal">推荐</span>}</b>
                          <small>{o.eff}</small>
                        </span>
                        <span className="go">拍板</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="meet-bottom">
                <div className="saybar">
                  <span className="lbl">主理人</span>
                  <input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') say(); }}
                    placeholder="补充意见，或直接给方案，回车发言"
                  />
                  <button className="btn" onClick={say}>发言</button>
                </div>
                <div className="mfoot">
                  <span className="hint">{phase === 'pick' ? '分歧已列出，选一个方案拍板，或直接说出你的做法' : 'AI 正在发言，可随时插话'}</span>
                  <span className="sp" />
                  {phase === 'say' && (
                    <button className="btn" onClick={() => setPhase('pick')}>跳到结论</button>
                  )}
                  <button className="btn" onClick={() => void closeMeeting()}>散会</button>
                </div>
              </div>
            </div>

            <aside className="meet-side">
              <h5>参会</h5>
              <div className="ppl">
                {who.map((k) => (
                  <div className="person" key={k}>
                    <span className="av sm" style={{ background: AGENTS[k]?.color }}>{AGENTS[k]?.ch}</span>
                    <span><b>{AGENTS[k]?.name}</b><small>{AGENTS[k]?.title}</small></span>
                  </div>
                ))}
              </div>
              <h5>未参会 · 只出书面意见</h5>
              <div className="ppl">
                {Object.values(AGENTS).filter((a) => !who.includes(a.k)).slice(0, 6).map((a) => (
                  <div className="person" key={a.k}>
                    <span className="av sm" style={{ background: a.color, opacity: 0.55 }}>{a.ch}</span>
                    <span><b>{a.name}</b><small>{a.kpi[0]}</small></span>
                  </div>
                ))}
              </div>
              <h5>会议参数</h5>
              <div className="meet-meta">
                <span>形式 <b>圆桌 · 轮流发言</b></span>
                <span>议题 <b>{agendaList.length}</b> 个</span>
                <span>现场意见 <b>{notes.length}</b> 条</span>
                <span>编号 <b>{mid ?? '本地'}</b></span>
              </div>
            </aside>
          </div>
        </>
      )}

      {stage === 'done' && (
        <>
          <div className="dlg-b">
            <div className="sum">
              <section>
                <h5>决议 · {res.length} 项</h5>
                {res.map((r, i) => (
                  <div className="mrow" key={i}>
                    <div><b>{r.opt.label}</b><small>{r.topic} · {r.opt.eff}</small></div>
                    <span className="tagx ok">已拍板</span>
                  </div>
                ))}
              </section>

              <section>
                <h5>行动项 · 确认后派发到审批中心</h5>
                {res.map((r, i) => (
                  <div className="mrow" key={i}>
                    <div><b>{r.opt.act.text}</b><small>{AGENTS[r.opt.act.from]?.name} · {r.opt.act.due} · 来自「{r.topic}」</small></div>
                    <span className="tagx">{r.opt.act.risk === 'red' ? '高风险' : r.opt.act.risk === 'mid' ? '中风险' : '低风险'}</span>
                  </div>
                ))}
              </section>

              {notes.length > 0 && (
                <section>
                  <h5>主理人现场意见 · {notes.length} 条，已进入纪要</h5>
                  {notes.map((n, i) => (
                    <div className="mrow" key={i}>
                      <div><b>{n.text}</b><small>{n.at} · {n.rep.map(([k]) => AGENTS[k]?.name ?? k).join('、')} 已回应</small></div>
                      <span className="tagx">现场意见</span>
                    </div>
                  ))}
                </section>
              )}

              <section>
                <h5>未参会 AI 的书面意见 · 只提供依据，不参与表决</h5>
                {written.map(([k, text]) => (
                  <div className="mrow" key={k}>
                    <div><b>{AGENTS[k]?.name}</b><small>{text}</small></div>
                    <span className="tagx">书面意见</span>
                  </div>
                ))}
              </section>

              <section>
                <h5>成本</h5>
                <div className="mrow">
                  <div><b>¥{cost.toFixed(2)}</b><small>{who.length} 名 AI 参与 · {mins} 分钟 · 演示估算</small></div>
                  <span className="tagx">演示费用估算</span>
                </div>
              </section>
            </div>
          </div>
          <div className="setup-acts">
            <button className="btn" onClick={() => void closeMeeting()}>关掉</button>
            <button className="btn primary" onClick={dispatch}>派发行动项</button>
          </div>
        </>
      )}
    </dialog>
  );
}

function NoteBlock({ note }: { note: Note }) {
  return (
    <>
      <div className="say me">
        <span className="av sm" style={{ background: 'var(--seal)' }}>理</span>
        <div className="bd">
          <b>主理人<em>现场意见 · {note.at}</em></b>
          <p>{note.text}</p>
        </div>
      </div>
      {note.rep.map(([k, text], i) => (
        <div className="say" key={i}>
          <span className="av sm" style={{ background: AGENTS[k]?.color }}>{AGENTS[k]?.ch ?? '·'}</span>
          <div className="bd">
            <b>{AGENTS[k]?.name ?? k}<em>{AGENTS[k]?.title}</em></b>
            <p>{text}</p>
          </div>
        </div>
      ))}
    </>
  );
}
