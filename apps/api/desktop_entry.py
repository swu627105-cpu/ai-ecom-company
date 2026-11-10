"""桌面版后端入口；路径参数必须在导入应用前设置。"""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--instance', required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not (args.web / 'index.html').is_file():
        parser.error('端口无效或缺少前端构建文件')
    args.data.mkdir(parents=True, exist_ok=True)
    os.environ['AIECOM_DATA_DIR'] = str(args.data.resolve())
    os.environ['AIECOM_WEB_DIR'] = str(args.web.resolve())
    os.environ['AIECOM_DESKTOP_INSTANCE'] = args.instance
    os.environ['AIECOM_SKIP_DOTENV'] = '1'
    os.environ['PYTHONUTF8'] = '1'
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, workers=1, log_level='info')


if __name__ == '__main__':
    main()
