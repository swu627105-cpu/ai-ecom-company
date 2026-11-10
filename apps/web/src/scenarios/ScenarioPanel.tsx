import { useEffect, useState } from 'react';
import { scenarioApi } from '../api';
import type { ApiAnalysis, Scenario } from '../api';
import { AGENTS } from '../data/seed';

type Props = { online: boolean; onDispatch: () => Promise<void>; onTasks: () => void };
export default function ScenarioPanel({ online, onDispatch, onTasks }: Props) {
  const [cases, setCases] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState('');
  const [result, setResult] = useState<ApiAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState<number | null>(null);
  const [sent, setSent] = useState<number[]>([]);
  const [message, setMessage] = useState('');
  useEffect(() => { if (online) scenarioApi.list().then(items => { setCases(items); setSelected(items[0]?.id ?? ''); }).catch(e => setMessage(`场景加载失败：${e.message}`)); }, [online]);
  const active = cases.find(c => c.id === selected);
  const choose = (id: string) => { setSelected(id); setResult(null); setSent([]); setMessage(''); };
  const run = async () => { setBusy(true); setResult(null); setSent([]); setMessage('正在分析模拟数据，书生响应可能需要一些时间…');
    try { const analysis = await scenarioApi.analyze(selected); setResult(analysis); setMessage('分析完成。选择建议提交审批，通过后可在任务中查看模拟进度。'); }
    catch (e) { setMessage(`分析失败：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const dispatch = async (index: number) => { if (!result) return; setSending(index); setMessage('');
    try { await scenarioApi.dispatch(selected, result.actions[index], result); setSent(v => [...v, index]); await onDispatch(); setMessage('建议已进入右侧审批中心。通过后可打开任务查看实时进度。'); }
    catch (e) { setMessage(`提交失败：${(e as Error).message}`); } finally { setSending(null); }
  };
  return <div className="scroll analysis scenario">
    <div className="scenario-intro"><b>业务场景演练</b><p>选一个场景 → 书生分析 → 提交建议 → 审批 → 查看模拟执行。数据与执行均为演示，不连接真实店铺。</p></div>
    {!online ? <div className="empty">请先启动后端，才能运行演练。</div> : <>
      <div className="scenario-picker">{cases.map(c => <button key={c.id} className={c.id === selected ? 'on' : ''} disabled={busy || sending !== null} onClick={() => choose(c.id)}>{c.title}</button>)}</div>
      {active && <section className="scenario-data"><h4>{active.title}</h4><p>{active.description}</p><ul>{active.metrics.map(m => <li key={m}>{m}</li>)}</ul><button className="btn primary" onClick={run} disabled={busy || sending !== null}>{busy ? '书生分析中…' : '开始分析场景'}</button></section>}
      {message && <p role="status" className="scenario-message">{message}</p>}
      {result && <section className="scenario-result"><div className="an-src"><span className={`tagx ${result.source === 'model' ? 'ok' : ''}`}>{result.source === 'model' ? `书生 · ${result.model}` : '规则兜底'}</span>{result.error && <span className="an-err">模型不可用：{result.error}</span>}</div><p className="an-head">{result.headline}</p><h5>关键发现</h5><ul className="an-list">{result.findings.map((f, i) => <li key={i}>{f}</li>)}</ul><h5>建议动作</h5>{result.actions.map((a, i) => <div className="an-act" key={i}><div><b>{a.text}</b><small>{AGENTS[a.owner]?.name ?? a.owner} · {a.why}</small></div><button className="btn tiny" disabled={sent.includes(i) || sending !== null} onClick={() => dispatch(i)}>{sent.includes(i) ? '已提交' : sending === i ? '提交中…' : '提交审批'}</button></div>)}<button className="btn" onClick={onTasks}>查看执行任务</button></section>}
    </>}
  </div>;
}
