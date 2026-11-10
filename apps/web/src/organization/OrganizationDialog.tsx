import { useEffect, useRef, useState } from 'react';
import { AGENTS } from '../data/seed';
import { organizationApi } from '../api';
import type { MockShop, StaffConfig } from '../api';

type Props = { open: boolean; online: boolean; onClose: () => void; onChanged: () => void };
export default function OrganizationDialog({ open, online, onClose, onChanged }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState<'solo' | 'team'>('solo');
  const [tab, setTab] = useState<'staff' | 'shops'>('staff');
  const [staff, setStaff] = useState<Record<string, StaffConfig>>({});
  const [shops, setShops] = useState<MockShop[]>([]);
  const [selected, setSelected] = useState('ceo');
  const [draft, setDraft] = useState<StaffConfig>({ hired: true, owner: '', level: 2 });
  const [platform, setPlatform] = useState('淘宝 / 天猫');
  const [name, setName] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const load = async () => { const [s, shops] = await Promise.all([organizationApi.staff(), organizationApi.shops(), organizationApi.organization().then(o => setMode(o.mode))]); setStaff(s); setShops(shops); };
  useEffect(() => { const d = ref.current; if (!d) return; if (open && !d.open) d.showModal(); if (!open && d.open) d.close(); }, [open]);
  useEffect(() => { if (open && online) { setMessage(''); load().catch(e => setMessage(`加载失败：${e.message}`)); } }, [open, online]);
  useEffect(() => { if (staff[selected]) setDraft({ ...staff[selected] }); }, [staff, selected]);
  const act = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage('');
    try { await fn(); await load(); onChanged(); setMessage(success); } catch (e) { setMessage(`操作失败：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const agent = AGENTS[selected];
  return <dialog ref={ref} className="org-dialog" onCancel={onClose}>
    <div className="org-head"><div><h2>公司设置</h2><p>员工配置与模拟店铺</p></div><button className="btn" onClick={onClose}>关闭</button></div>
    <div className="org-form"><label>组织模式<select disabled={!online || busy} value={mode} onChange={e => void act(() => organizationApi.updateOrganization(e.target.value as 'solo' | 'team'), '组织模式已保存')}><option value="solo">全 AI 运营 · 主理人审批</option><option value="team">人机协作 · 主管分管</option></select></label><p className="org-note">人机协作时可切换主管演示视角，常规事项路由到员工负责人；红线联合方案仍由主理人审批。</p></div>
    <div className="org-tabs"><button className={tab === 'staff' ? 'on' : ''} onClick={() => setTab('staff')}>AI 员工库</button><button className={tab === 'shops' ? 'on' : ''} onClick={() => setTab('shops')}>店铺授权</button></div>
    {!online ? <p className="org-note">请先启动后端，员工和店铺配置需要保存到后端。</p> : tab === 'staff' ? <div className="org-grid">
      <div className="org-list">{Object.values(AGENTS).map(a => <button key={a.k} className={selected === a.k ? 'selected' : ''} onClick={() => setSelected(a.k)}><b>{a.name}</b><small>{staff[a.k]?.hired ? staff[a.k].owner || '待分配' : '未雇佣'}</small></button>)}</div>
      <div className="org-form"><h3>{agent.name}</h3><p>{agent.title}</p><p><b>岗位权限：</b>{agent.perm}</p><p><b>岗位指标：</b>{agent.kpi.join(' · ')}</p>
        <label><input type="checkbox" checked={draft.hired} onChange={e => setDraft({ ...draft, hired: e.target.checked })} /> 已雇佣</label>
        {draft.hired && <><label>分配给<input value={draft.owner} maxLength={40} placeholder="留空表示待分配" onChange={e => setDraft({ ...draft, owner: e.target.value })} /></label><label>自治等级<select value={draft.level} onChange={e => setDraft({ ...draft, level: Number(e.target.value) })}><option value={1}>L1 · 建议</option><option value={2}>L2 · 有限自治</option><option value={3}>L3 · 高度自治</option><option value={4}>L4 · 全自治（红线仍审批）</option></select></label></>}
        <p className="org-note">雇佣状态控制参会和救援工具；L1仅建议，L2–L4在审批后执行。救援联合方案由主理人统一批准。</p><button className="btn primary" disabled={busy} onClick={() => act(() => organizationApi.updateStaff(selected, draft), '员工配置已保存')}>保存配置</button>
      </div></div> : <div className="org-form"><p className="org-note">模拟授权不会跳转外部平台、获取真实店铺数据或执行真实操作。可在公司频道选择店铺，各轮保存独立数据和同店历史上下文。</p><label>平台<select value={platform} onChange={e => setPlatform(e.target.value)}>{['淘宝 / 天猫', '抖音', '拼多多', '其他'].map(x => <option key={x}>{x}</option>)}</select></label><label>店铺名称<input maxLength={40} value={name} onChange={e => setName(e.target.value)} placeholder="输入模拟店铺名称" /></label><button className="btn primary" disabled={busy || !name.trim()} onClick={() => act(async () => { await organizationApi.connect(platform, name); setName(''); }, '模拟店铺已添加')}>添加模拟店铺</button><div className="org-shops">{shops.length === 0 ? <p>尚未添加模拟店铺。</p> : shops.map(s => <div key={s.id}><span><b>{s.name}</b><small>{s.platform} · {s.status} · {s.data_source}</small></span><button className="btn tiny" disabled={busy} onClick={() => act(() => organizationApi.disconnect(s.id), '模拟店铺已移除')}>移除</button></div>)}</div></div>}
    {message && <p role="status" className="org-message">{message}</p>}
  </dialog>;
}
