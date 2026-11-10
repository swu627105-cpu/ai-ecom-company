"""打包本地模拟参赛版本：白名单源码，不打包凭据、运行数据或依赖目录。"""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
output = root / 'docs' / 'AI电商公司-可复现源码.zip'
folders = [root / p for p in ['apps/web/src', 'apps/web/public', 'packages', 'apps/api/app', 'apps/api/tests', 'apps/desktop', 'scripts', 'docs/demo-evidence']]
explicit = [
    'package.json', 'package-lock.json',
    'apps/web/package.json', 'apps/web/tsconfig.json', 'apps/web/vite.config.ts', 'apps/web/index.html',
    'apps/api/requirements.txt', 'apps/api/requirements-windows.txt', 'apps/api/desktop_entry.py',
    'README.md', '原型功能核对.md',
    'docs/平台使用说明.md', 'docs/平台代码导读_零基础版.md', 'docs/演示视频录制脚本.md',
    'docs/Windows使用说明.md', 'docs/比赛演示与答辩.md', 'docs/数据字典.md', 'docs/参赛交付说明.md',
]
files = {root / p for p in explicit if (root / p).is_file()}
for folder in folders:
    if folder.exists():
        for path in folder.rglob('*'):
            rel = path.relative_to(root)
            runtime_data = rel.parts[:3] == ('apps', 'api', 'data')
            if path.is_file() and not runtime_data and not any(part in {'node_modules', '__pycache__', '.venv', 'dist'} or part.startswith('.') for part in rel.parts) and path.suffix not in {'.pyc', '.zip'}:
                files.add(path)
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, 'platform/' + path.relative_to(root).as_posix())
with zipfile.ZipFile(output) as archive:
    assert not any('settings.json' in name or '/apps/api/data/' in name or '/.env' in name for name in archive.namelist())
print(f'{output} · {len(files)} files · {output.stat().st_size} bytes')
