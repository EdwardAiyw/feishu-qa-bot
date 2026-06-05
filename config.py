import os
from dotenv import load_dotenv

load_dotenv()

# 飞书应用凭证
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

# 飞书 API 地址
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"

# 服务配置
PORT = int(os.getenv("PORT", 5000))
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
