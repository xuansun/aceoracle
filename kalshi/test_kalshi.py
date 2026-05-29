from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(Path(__file__).parent / ".env")
key_id = os.getenv("KALSHI_KEY_ID")
key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private.pem")
if key_path and not os.path.isabs(key_path):
    key_path = str(Path(__file__).parent / key_path)

print("key_id:", key_id)
print("key_path:", key_path)
print("pem exists:", Path(key_path).exists())

from client import KalshiClient
c = KalshiClient(key_id, key_path)
print("client created OK")
b = c.get_balance()
print("balance:", b)
