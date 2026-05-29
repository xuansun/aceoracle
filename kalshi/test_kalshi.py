import base64
import time
import requests
from pathlib import Path
from dotenv import load_dotenv
import os
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

load_dotenv(Path(__file__).parent / ".env")
key_id = os.getenv("KALSHI_KEY_ID")
key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private.pem")
if key_path and not os.path.isabs(key_path):
    key_path = str(Path(__file__).parent / key_path)

with open(key_path, "rb") as f:
    private_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

BASE = "https://api.elections.kalshi.com/trade-api/v2"
endpoint = "/portfolio/balance"

def try_auth(label, ts, path):
    msg = f"{ts}GET{path}"
    sig = private_key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(sig).decode()
    headers = {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
    }
    resp = requests.get(f"{BASE}{endpoint}", headers=headers, timeout=10)
    print(f"{label}: {resp.status_code} — {resp.text[:120]}")
    return resp.status_code == 200

# Try 1: milliseconds + full path with /trade-api/v2
ts_ms = str(int(time.time() * 1000))
if try_auth("ms + full path", ts_ms, f"/trade-api/v2{endpoint}"):
    print("-> SUCCESS: use milliseconds + /trade-api/v2 prefix")
    exit()

# Try 2: milliseconds + just the endpoint path
if try_auth("ms + short path", ts_ms, endpoint):
    print("-> SUCCESS: use milliseconds + short path")
    exit()

# Try 3: seconds + full path
ts_s = str(int(time.time()))
if try_auth("s + full path", ts_s, f"/trade-api/v2{endpoint}"):
    print("-> SUCCESS: use seconds + /trade-api/v2 prefix")
    exit()

# Try 4: seconds + short path
if try_auth("s + short path", ts_s, endpoint):
    print("-> SUCCESS: use seconds + short path")
    exit()

print("\nAll attempts failed. Check that key_id matches the private key in your Kalshi dashboard.")
print("key_id used:", key_id)
