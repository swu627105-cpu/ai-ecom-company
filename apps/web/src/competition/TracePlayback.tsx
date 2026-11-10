import { useEffect, useState } from 'react';
import type { DemoTrace } from '../api';
export default function TracePlayback({ trace, runId }: { trace: DemoTrace[]; runId: string }) {
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  useEffect(() => { setCursor(0); setPlaying(false); }, [runId]);
  useEffect(() => { if (!playing) return; if (cursor >= trace.length - 1) { setPlaying(false); return; } const timer = window.setTimeout(() => setCursor(n => n + 1), 1400 / speed); return () => clearTimeout(timer); }, [playing, cursor, speed, trace.length]);
  if (!trace.length) return null;
  const current = trace[Math.min(cursor, trace.length - 1)];
  return <details className="rescue-playback"><summary>按时间回放本轮记录</summary><p className="rescue-note">回放已保存的过程，不重新调用模型或执行工具。</p><div className="rescue-start"><button className="btn tiny" onClick={() => setPlaying(v => !v)}>{playing ? '暂停回放' : '播放记录'}</button><select aria-label="记录回放速度" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select><button className="btn tiny" onClick={() => { setCursor(0); setPlaying(false); }}>重新开始回放</button></div><label>步骤 {cursor + 1}/{trace.length}<input type="range" min={0} max={trace.length - 1} value={cursor} onChange={e => { setCursor(Number(e.target.value)); setPlaying(false); }} /></label><p><b>{current.actor}</b> · {current.at.slice(11, 19)}</p><p>{current.message}</p></details>;
}
