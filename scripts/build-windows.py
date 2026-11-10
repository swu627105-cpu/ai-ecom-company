"""跨平台构建 Windows x64 便携 APP，不带入开发机密钥或运行状态。"""
from __future__ import annotations

import argparse
import base64
import email
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'build' / 'windows-cache'
RELEASE = ROOT / 'release'
PYTHON_VERSION = '3.13.9'
PYTHON_TAG = '313'
PRODUCT = 'AI-Ecom-Company'


def digest(file):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    print('Downloading:', url, flush=True)
    partial = target.with_suffix(target.suffix + '.part')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'AI-Ecom-Company-Build/1.0'})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def safe_members(archive):
    for member in archive.infolist():
        name = PurePosixPath(member.filename)
        if name.is_absolute() or '..' in name.parts or '\\' in member.filename or ':' in member.filename:
            raise ValueError(f'Unsafe archive entry: {member.filename}')
        yield member


def extract(archive_path, target):
    with zipfile.ZipFile(archive_path) as archive:
        for member in safe_members(archive):
            archive.extract(member, target)


def install_wheel(wheel, site_packages, python_root):
    with zipfile.ZipFile(wheel) as archive:
        for member in safe_members(archive):
            if member.is_dir():
                continue
            parts = PurePosixPath(member.filename).parts
            if parts[0].endswith('.data'):
                if len(parts) < 3:
                    continue
                if parts[1] in ('purelib', 'platlib'):
                    dest = site_packages.joinpath(*parts[2:])
                elif parts[1] == 'data':
                    dest = python_root.joinpath(*parts[2:])
                else:
                    # 应用使用 python -m 入口，不安装第三方 console_scripts。
                    continue
            else:
                dest = site_packages.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, dest.open('wb') as output:
                shutil.copyfileobj(source, output)


def pe_machine(file):
    with file.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError(f'Not a Windows executable: {file}')
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b'PE\x00\x00':
            raise ValueError(f'Invalid PE signature: {file}')
        return struct.unpack('<H', stream.read(2))[0]


def build(skip_web=False):
    if not skip_web:
        subprocess.run(['npm.cmd' if os.name == 'nt' else 'npm', 'run', 'build'], cwd=ROOT, check=True)
    if not (ROOT / 'apps/web/dist/index.html').exists():
        raise ValueError('Missing web build; run npm ci and npm run build first')
    package = json.loads((ROOT / 'apps/desktop/package.json').read_text())
    electron_version = package['devDependencies']['electron']
    if not electron_version[0].isdigit():
        raise ValueError('Electron must use an exact pinned version')
    electron_file = f'electron-v{electron_version}-win32-x64.zip'
    electron_base = f'https://github.com/electron/electron/releases/download/v{electron_version}'
    sums = download(electron_base + '/SHASUMS256.txt', CACHE / f'electron-{electron_version}-SHASUMS256.txt')
    hashes = {line.split()[-1].lstrip('*'): line.split()[0] for line in sums.read_text().splitlines() if len(line.split()) == 2}
    electron_zip = download(electron_base + '/' + electron_file, CACHE / electron_file)
    if digest(electron_zip) != hashes[electron_file]:
        raise ValueError('Electron SHA256 mismatch; remove cached archive and retry')
    python_file = f'python-{PYTHON_VERSION}-embed-amd64.zip'
    python_url = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/{python_file}'
    python_zip = download(python_url, CACHE / python_file)
    signature = download(python_url + '.sigstore', CACHE / (python_file + '.sigstore'))
    sig = json.loads(signature.read_text())
    expected = base64.b64decode(sig['messageSignature']['messageDigest']['digest']).hex()
    if digest(python_zip) != expected:
        raise ValueError('Python SHA256 mismatch; remove cached archive and retry')
    wheels = CACHE / 'wheels'
    wheels.mkdir(parents=True, exist_ok=True)
    lock = ROOT / 'scripts/windows/requirements-runtime.lock.txt'
    requirements = lock if lock.exists() else ROOT / 'apps/api/requirements-windows.txt'
    command = [sys.executable, '-m', 'pip', 'download', '--dest', str(wheels), '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '3.13', '--implementation', 'cp', '--abi', 'cp313', '--index-url', 'https://pypi.org/simple', '-r', str(requirements)]
    if lock.exists(): command.append('--require-hashes')
    expected_wheels = {line.split('--hash=sha256:')[1].strip() for line in lock.read_text().splitlines() if '--hash=sha256:' in line} if lock.exists() else set()
    cached_hashes = {digest(wheel) for wheel in wheels.glob('*.whl')}
    if expected_wheels and expected_wheels <= cached_hashes:
        print('Reusing hash-verified locked wheel cache', flush=True)
    else:
        subprocess.run(command, check=True)
    dependencies = []
    selected = []
    if lock.exists():
        # 锁文件中的哈希决定选用哪个 wheel；忽略缓存中其他版本。
        allowed = {line.split('--hash=sha256:')[1].strip() for line in lock.read_text().splitlines() if '--hash=sha256:' in line}
    else: allowed = None
    for wheel in sorted(wheels.glob('*.whl')):
        sha = digest(wheel)
        if allowed is not None and sha not in allowed: continue
        with zipfile.ZipFile(wheel) as archive:
            metadata = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            info = email.message_from_bytes(archive.read(metadata))
        dependencies.append({'name': info['Name'], 'version': info['Version'], 'wheel': wheel.name, 'sha256': sha})
        selected.append(wheel)
    if not dependencies or (allowed is not None and len(selected) != len(allowed)):
        raise ValueError('Wheel cache incomplete or inconsistent with runtime lock')
    if not lock.exists():
        lock.write_text('# Windows x64 / CPython 3.13; generated from verified wheel archives.\n' + '\n'.join(f"{d['name']}=={d['version']} --hash=sha256:{d['sha256']}" for d in sorted(dependencies, key=lambda d: d['name'].lower())) + '\n', encoding='utf-8')
    bundle = RELEASE / f'{PRODUCT}-Windows-x64'
    if bundle.exists(): shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    extract(electron_zip, bundle)
    (bundle / 'electron.exe').rename(bundle / f'{PRODUCT}.exe')
    (bundle / 'resources/default_app.asar').unlink(missing_ok=True)
    desktop = bundle / 'resources/app'
    desktop.mkdir(parents=True)
    for name in ('main.cjs', 'preload.cjs', 'backend.cjs'):
        shutil.copy2(ROOT / 'apps/desktop' / name, desktop / name)
    (desktop / 'package.json').write_text(json.dumps({'name': 'ai-ecom-company', 'productName': 'AI 电商公司', 'version': package['version'], 'main': 'main.cjs'}, ensure_ascii=False), encoding='utf-8')
    api = bundle / 'resources/api'
    api.mkdir()
    shutil.copytree(ROOT / 'apps/api/app', api / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'apps/api/desktop_entry.py', api / 'desktop_entry.py')
    shutil.copytree(ROOT / 'apps/web/dist', bundle / 'resources/web')
    python_root = bundle / 'resources/python'
    python_root.mkdir()
    extract(python_zip, python_root)
    site = python_root / 'Lib/site-packages'
    site.mkdir(parents=True)
    for wheel in selected: install_wheel(wheel, site, python_root)
    (python_root / f'python{PYTHON_TAG}._pth').write_text(f'python{PYTHON_TAG}.zip\n.\nLib/site-packages\n../api\nimport site\n', encoding='ascii')
    for binary in [bundle / f'{PRODUCT}.exe', python_root / 'python.exe', *site.rglob('*.pyd')]:
        if pe_machine(binary) != 0x8664: raise ValueError(f'Non-x64 binary: {binary}')
    for name in ('Create-Shortcut.ps1', 'Create-Shortcut.cmd', 'Start-App.cmd'):
        shutil.copy2(ROOT / 'scripts/windows' / name, bundle / name)
    for name in ('Windows使用说明.md', '比赛演示与答辩.md'):
        shutil.copy2(ROOT / 'docs' / name, bundle / name)
    shutil.copy2(ROOT / 'scripts/windows/Smoke-Test.ps1', bundle / 'Smoke-Test.ps1')
    evidence = bundle / '演示证据'
    evidence.mkdir()
    # 仅随包带只读合成案例证据，不导入到用户运行状态。
    for name in ('DEMO-a470c173.json', 'DEMO-bf599c85.json', '最终闭环-原平台.png'):
        source = ROOT / 'docs/demo-evidence' / name
        if source.exists(): shutil.copy2(source, evidence / name)
    manifest = {'product': PRODUCT, 'version': package['version'], 'platform': 'win32', 'arch': 'x64', 'electron': electron_version, 'electron_sha256': digest(electron_zip), 'python': PYTHON_VERSION, 'python_sha256': digest(python_zip), 'dependencies': dependencies, 'validation': 'PE architecture / checksums / bundle contents checked. Run Smoke-Test.ps1 on Windows for runtime validation.'}
    (bundle / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    forbidden = [p for p in bundle.rglob('*') if p.name in ('settings.json', 'state.json', '.env') or '__pycache__' in p.parts]
    if forbidden: raise ValueError(f'Unexpected developer runtime data: {forbidden}')
    target = RELEASE / f'{PRODUCT}-{package["version"]}-Windows-x64.zip'
    print('Creating ZIP:', target, flush=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(bundle.rglob('*')):
            if file.is_file(): archive.write(file, file.relative_to(RELEASE))
    checksum = target.with_suffix('.zip.sha256')
    checksum.write_text(f'{digest(target)}  {target.name}\n', encoding='ascii')
    print(f'Windows APP ready: {target} ({target.stat().st_size / 1024**2:.1f} MB)', flush=True)
    print('Not executed on Windows by this build. Use bundled Smoke-Test.ps1 for target-host validation.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-web-build', action='store_true')
    args = parser.parse_args()
    build(args.skip_web_build)
