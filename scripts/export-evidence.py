"""从运行中的本地服务导出非敏感证据，不读取模型配置或密钥。"""
import json
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1] / 'docs' / 'demo-evidence'
root.mkdir(parents=True, exist_ok=True)

def read(path):
    with urlopen('http://127.0.0.1:8000' + path, timeout=15) as response:
        return json.load(response)

def write(name, value):
    (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

reports = read('/api/evaluation')
write('对照评测完整记录.json', reports)
runs = read('/api/demo/runs')
write('运行索引.json', runs)
for run in runs[:10]:
    write(f"{run['id']}.json", read(f"/api/demo/runs/{run['id']}/export"))
print(f'导出 {len(reports)} 份评测和至多10轮运行证据到 {root}')
