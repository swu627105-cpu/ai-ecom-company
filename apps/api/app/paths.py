"""可写运行数据与只读程序资源分离，桌面版使用用户目录。"""
import os
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AIECOM_DATA_DIR", str(API_ROOT / "data"))).expanduser().resolve()
