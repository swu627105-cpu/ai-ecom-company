import { useEffect, useRef, useState } from 'react';
import { demoApi, organizationApi } from '../api';
import type { BusinessData, DemoHistory, DemoRun, DemoTemplate, FeedbackInput, MockShop } from '../api';

export const RESCUE_STATUS: Record<string, string> = { running: '书生正在分析', awaiting_approval: '联合方案待审批', executing: '正在执行沙箱工具', completed: '救援完成 · 结果已验证', failed: '本轮运行失败', blocked: '方案被风控拦截', rejected: '方案已驳回', verification_failed: '结果校验未通过', rolled_back: '执行前快照已恢复', interrupted: '服务重启，运行已中断' };
export function useRescueFlow(online: boolean, onChanged: () => Promise<void>) {
  const [run, setRun] = useState<DemoRun | null>(null);
  const [id, setId] = useState(() => { try { const requested = new URLSearchParams(window.location.search).get('run'); return requested && /^DEMO-[a-f0-9]{8}$/i.test(requested) ? requested : localStorage.getItem('aiecom-rescue-run') ?? ''; } catch { return ''; } });
  const [template, setTemplate] = useState<DemoTemplate | null>(null);
  const [history, setHistory] = useState<DemoHistory[]>([]);
  const [shops, setShops] = useState<MockShop[]>([]);
  const [epoch, setEpoch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const loadHistory = async () => { setHistory(await demoApi.history()); setShops(await organizationApi.shops()); };
  useEffect(() => { if (online) { demoApi.template().then(setTemplate).catch(e => setError(String(e))); void loadHistory().catch(e => setError(String(e))); } }, [online]);
  useEffect(() => {
    if (!online || !id) return;
    let stop = false;
    let timer = 0;
    let version = '';
    const poll = async () => {
      try {
        const next = await demoApi.read(id);
        if (stop) return;
        setRun(next); setError('');
        const signature = `${next.id}:${next.trace.length}:${next.status}`;
        if (signature !== version) { version = signature; await changed.current(); await loadHistory(); }
        if (!stop && ['running', 'executing', 'awaiting_approval'].includes(next.status)) timer = window.setTimeout(poll, 900);
      } catch (e) { if (!stop) { setError(`流程同步失败，将自动重连：${(e as Error).message}`); timer = window.setTimeout(poll, 3000); } }
    };
    void poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [online, id, epoch]);
  const select = (nextId: string) => { if (nextId !== id) setRun(null); setId(nextId); setEpoch(n => n + 1); try { localStorage.setItem('aiecom-rescue-run', nextId); } catch { /* optional */ } };
  const start = async (mode: 'live' | 'rehearsal', extra: { data?: BusinessData; preset?: string; shop_id?: string; parent_id?: string } = {}) => {
    setBusy(true); setError('');
    try { const next = await demoApi.start(mode, false, extra); setRun(next); setId(next.id); try { localStorage.setItem('aiecom-rescue-run', next.id); } catch { /* storage optional */ } }
    catch (e) { setError(`启动失败：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const refresh = async () => { if (id) setRun(await demoApi.read(id)); };
  const rollback = async () => { if (!run) return; setBusy(true); setError('');
    try { setRun(await demoApi.rollback(run.id)); await changed.current(); await loadHistory(); await loadHistory(); } catch (e) { setError(`回滚失败：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const mutate = async (request: () => Promise<DemoRun>) => { setBusy(true); setError(''); try { const next = await request(); setRun(next); if (['running', 'executing'].includes(next.status)) setEpoch(n => n + 1); await changed.current(); await loadHistory(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  const revise = (reason: string, data?: BusinessData) => run ? mutate(() => demoApi.revise(run.id, reason, data)) : Promise.resolve();
  const resume = () => run ? mutate(() => demoApi.resume(run.id)) : Promise.resolve();
  const feedback = (input: FeedbackInput) => run ? mutate(() => demoApi.feedback(run.id, input)) : Promise.resolve();
  const exportEvidence = async () => { if (!run) return;
    try { const evidence = await demoApi.export(run.id); const url = URL.createObjectURL(new Blob([JSON.stringify(evidence, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = `${run.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (e) { setError(`导出失败：${(e as Error).message}`); }
  };
  return { run, template, history, shops, loadHistory, select, busy, error, start, refresh, rollback, revise, resume, feedback, exportEvidence };
}
export type RescueFlow = ReturnType<typeof useRescueFlow>;
