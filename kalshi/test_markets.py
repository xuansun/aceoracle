from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent / ".env")
key_id = os.getenv("KALSHI_KEY_ID")
key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private.pem")
if not os.path.isabs(key_path):
    key_path = str(Path(__file__).parent / key_path)

from client import KalshiClient
c = KalshiClient(key_id, key_path)

print("=== All open markets (first 20) ===")
data = c.get_markets(limit=20)
markets = data.get("markets", [])
for m in markets:
    print(f"  {m.get('ticker','?')[:40]:<40} {m.get('title','')[:60]}")

print(f"\nTotal returned: {len(markets)}")

print("\n=== Tennis filter result ===")
tennis = c.get_tennis_markets()
print(f"Tennis markets found: {len(tennis)}")
for m in tennis:
    print(f"  {m.get('ticker','?')[:40]:<40} {m.get('title','')[:60]}")
