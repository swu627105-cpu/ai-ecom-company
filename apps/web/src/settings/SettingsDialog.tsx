import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { ApiModelStatus } from '../api';

type Props = {
  open: boolean;
  online: boolean;
  onClose: () => void;
  onSaved: (s: ApiModelStatus) => void;
};

export default function SettingsDialog({ open, online, onClose, onSaved }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [status, setStatus] = useState<ApiModelStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState('https://discovery-api.intern-ai.org.cn/v1');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('intern-s2');
  const [busy, setBusy] = useState<'' | 'test' | 'save'>('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setMsg(null); setApiKey(''); setBusy('');
    if (!online) return;
    (async () => {
      try {
        const s = await api.modelSettings(true);
        setStatus(s);
        setBaseUrl(s.base_url);
        setModel(s.model);
      } catch {
        setMsg({ ok: false, text: '读不到后端配置，检查后端是否在运行' });
      }
    })();
  }, [open, online]);

  const payload = () => ({
    base_url: baseUrl.trim(),
    api_key: apiKey.trim(),
    model: model.trim()
  });

  const test = async () => {
    setBusy('test'); setMsg(null);
    try {
      const s = await api.testModelSettings(payload());
      setStatus(s);
      setMsg(s.connected
        ? { ok: true, text: `连通成功，可用模型 ${s.models.length} 个${s.models.length ? '：' + s.models.slice(0, 4).join(' / ') : ''}` }
        : { ok: false, text: `连不上：${s.error || '未知错误'}` });
    } catch (e) {
      setMsg({ ok: false, text: `请求失败：${(e as Error).message}` });
    } finally {
      setBusy('');
    }
  };

  const save = async () => {
    setBusy('save'); setMsg(null);
    try {
      const s = await api.saveModelSettings(payload());
      setStatus(s);
      setApiKey('');
      onSaved(s);
      setMsg(s.connected
        ? { ok: true, text: `已保存，模型 ${s.model} 已连通` }
        : { ok: false, text: `已保存，但仍连不上：${s.error || '未知错误'}（会议会自动用规则话术兜底）` });
    } catch (e) {
      setMsg({ ok: false, text: `保存失败：${(e as Error).message}` });
    } finally {
      setBusy('');
    }
  };

  return (
    <dialog ref={ref} onClose={onClose} onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <div className="dlg-h">
        <div>
          <h3>模型配置</h3>
          <p>换一把可用的 Key 就能让会议里的 AI 由模型生成；连不上时自动回落规则话术，不会卡住会议。</p>
        </div>
        <button className="x" onClick={onClose} aria-label="关闭">×</button>
      </div>

      <div className="dlg-b">
        <div className="setup-f" style={{ padding: '16px 18px' }}>
          {!online && (
            <div className="warnbar" style={{ border: '1px solid var(--line)', borderRadius: 8 }}>
              后端没连上，配置无法保存。先启动 API（端口 8000）再打开这个面板。
            </div>
          )}

          <div className="field">
            <label>Base URL</label>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://discovery-api.intern-ai.org.cn/v1" />
          </div>

          <div className="field">
            <label>
              API Key
              {status?.has_key ? `（已保存 ${status.key_hint}，留空表示不修改）` : '（仅保存在本机后端，不会回传到前端）'}
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={status?.has_key ? '留空沿用已保存的密钥' : 'sk-...'}
              autoComplete="off"
            />
          </div>

          <div className="field">
            <label>模型{status?.models?.length ? `（该 Key 可用 ${status.models.length} 个）` : ''}</label>
            <input value={model} onChange={(e) => setModel(e.target.value)} list="model-options" placeholder="intern-s2" />
            <datalist id="model-options">
              {(status?.models ?? []).map((m) => <option key={m} value={m} />)}
            </datalist>
          </div>

          {status && (
            <div className="meet-meta" style={{ marginTop: 2 }}>
              <span>当前状态 <b style={{ color: status.connected ? 'var(--ok)' : 'var(--seal)' }}>
                {status.connected ? '已连通' : '未连通'}
              </b></span>
              {status.error && <span>原因 <b>{status.error}</b></span>}
            </div>
          )}

          {msg && (
            <div className="warnbar" style={{
              border: '1px solid var(--line)', borderRadius: 8,
              background: msg.ok ? 'var(--ok-soft)' : 'var(--seal-soft)',
              color: msg.ok ? 'var(--ok)' : 'var(--seal)'
            }}>
              {msg.text}
            </div>
          )}
        </div>
      </div>

      <div className="setup-acts">
        <button className="btn" onClick={test} disabled={!online || busy !== ''}>
          {busy === 'test' ? '测试中…' : '测试连接'}
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={onClose}>关闭</button>
        <button className="btn primary" onClick={save} disabled={!online || busy !== ''}>
          {busy === 'save' ? '保存中…' : '保存并应用'}
        </button>
      </div>
    </dialog>
  );
}
