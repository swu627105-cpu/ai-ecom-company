import { useState } from 'react';
import { api, demoApi } from '../api';
import type { ApiApproval } from '../api';
export default function ApprovalTools({ approval, onChanged, onOpenRun }: { approval: ApiApproval; onChanged: () => Promise<void>; onOpenRun: (id: string) => void }) {
  const [question, setQuestion] = useState('为什么采用这个方案？最主要的风险是什么？');
  const [reason, setReason] = useState('请降低资金占用并重新核对交期，无法满足时说明缺口。');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setError(''); try { await fn(); await onChanged(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  return <div className="approval-tools"><details><summary>追问 AI / 查看讨论</summary><label>审批问题<textarea value={question} maxLength={1000} onChange={e => setQuestion(e.target.value)} /></label><button className="btn tiny" disabled={busy || !question.trim()} onClick={() => void act(() => api.askApproval(approval.id, question))}>{busy ? '处理中…' : '向书生追问'}</button>{approval.discussion?.map((d, i) => <div className="approval-discussion" key={i}><b>主理人：{d.question}</b><p>{d.answer}</p><small>{d.source} · {d.at}</small></div>)}</details>{approval.demo_run_id && <><button className="btn tiny" onClick={() => onOpenRun(approval.demo_run_id!)}>查看关联运行与版本</button>{approval.status === 'pending' && <details><summary>退回修改</summary><label>修改要求<textarea value={reason} maxLength={1000} onChange={e => setReason(e.target.value)} /></label><button className="btn tiny" disabled={busy || !reason.trim()} onClick={() => void act(async () => { await demoApi.revise(approval.demo_run_id!, reason); onOpenRun(approval.demo_run_id!); })}>退回并重新规划</button></details>}</>}{error && <p role="alert">{error}</p>}</div>;
}
