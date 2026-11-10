import { useEffect, useState } from 'react';
import { api } from '../api';
import type { ApiMeeting } from '../api';
import { AGENTS } from '../data/seed';
type Audit = Awaited<ReturnType<typeof api.audit>>;
export default function HistoryPanel({ online }: { online: boolean }) {
  const [meetings, setMeetings] = useState<ApiMeeting[]>([]);
  const [audit, setAudit] = useState<Audit>([]);
  const [error, setError] = useState('');
  const load = async () => { try { const [m, a] = await Promise.all([api.meetingHistory(), api.audit(300)]); setMeetings(m); setAudit(a); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { if (online) void load(); }, [online]);
  return <div className="scroll analysis history-panel"><div className="scenario-intro"><b>会议纪要与操作审计</b><p>读取后端持久化记录；救援运行的工具回执和版本见公司频道的运行历史。</p><button className="btn" disabled={!online} onClick={() => void load()}>刷新历史</button></div>{error && <p role="alert">{error}</p>}<h4>会议记录 · {meetings.length}</h4>{meetings.map(m => <details key={m.id}><summary>{m.title} · {m.stage === 'done' ? '已结束' : m.stage === 'ready' ? '未开始' : m.stage === 'cancelled' ? '已散会' : '进行中'}</summary><p>{m.id} · {m.goal}</p><p>参会：{m.who.map(k => AGENTS[k]?.name || k).join('、')}</p>{m.agenda.map((a, i) => <div key={i}><b>{a.t}</b><p>{a.q}</p>{a.says.slice(0, m.stage === 'done' || i < m.idx ? undefined : i === m.idx ? m.shown : 0).map(([k, t], j) => <p key={j}>{AGENTS[k]?.name || k}：{t}</p>)}</div>)}{m.notes.map(n => <div key={n.id}><b>主理人：{n.text}</b>{n.replies.map(([k, t], i) => <p key={i}>{AGENTS[k]?.name || k}：{t}</p>)}</div>)}{m.res.map((r, i) => <p key={i}>决议：{r.topic} · {r.label} · {r.eff}</p>)}<p>{m.dispatched ? '行动项已派发' : '行动项未派发'}</p></details>)}{!meetings.length && <p className="rescue-note">暂无会议。</p>}<h4>审计记录 · {audit.length}</h4><p className="rescue-note">本地哈希链用于检查记录连贯性，不作为第三方防篡改认证。</p>{[...audit].reverse().map(a => <div className="audit-row" key={a.seq}><b>#{a.seq} {a.action}</b><p>{a.actor} → {a.target} · {a.at}</p><small>{a.hash}</small></div>)}</div>;
}
