import os
import sys
from pathlib import Path

os.environ["AIECOM_PERSIST"] = "0"
os.environ["LLM_API_KEY"] = ""  # 测试不连外网
os.environ["AIECOM_NO_SETTINGS"] = "1"  # 不读界面里保存的密钥
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
