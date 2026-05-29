import base64
import time
import requests
from pathlib import Path
from dotenv import load_dotenv
import os
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
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
method = "GET"

ts_ms = str(int(time.time() * 1000))
ts_s  = str(int(time.time()))

def sign(msg: str, pad=None) -> str:
    p = pad or asym_padding.PKCS1v15()
    sig = private_key.sign(msg.encode("utf-8"), p, hashes.SHA256())
    return base64.b64encode(sig).decode()

def attempt(label, ts, msg_path, pad=None):
    msg = f"{ts}{method}{msg_path}"
    headers = {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sign(msg, pad),
    }
    r = requests.get(f"{BASE}{endpoint}", headers=headers, timeout=10)
    status = "OK" if r.ok else r.status_code
    detail = r.json().get("error", {}).get("details", r.text[:80]) if not r.ok else "SUCCESS"
    print(f"  [{status}] {label}")
    if r.ok:
        print(f"         -> balance: {r.json()}")
    else:
        print(f"         -> {detail}")
    return r.ok

pss = asym_padding.PSS(mgf=asym_padding.MGF1(hashes.SHA256()), salt_length=asym_padding.PSS.MAX_LENGTH)

print("=== Trying message formats ===\n")
for ts_label, ts in [("ms", ts_ms), ("s", ts_s)]:
    for path_label, msg_path in [
        ("full", f"/trade-api/v2{endpoint}"),
        ("short", endpoint),
    ]:
        for pad_label, pad in [("pkcs1v15", None), ("pss", pss)]:
            label = f"{ts_label} | {path_label} path | {pad_label}"
            if attempt(label, ts, msg_path, pad):
                print(f"\n✓ WORKING FORMAT: {label}")
                exit(0)

# Also try without method in message
print("\n=== Trying without method in message ===\n")
for ts_label, ts in [("ms", ts_ms), ("s", ts_s)]:
    for path_label, msg_path in [
        ("full", f"/trade-api/v2{endpoint}"),
        ("short", endpoint),
    ]:
        msg = f"{ts}{msg_path}"
        headers = {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign(msg),
        }
        r = requests.get(f"{BASE}{endpoint}", headers=headers, timeout=10)
        label = f"no method | {ts_label} | {path_label}"
        print(f"  [{'OK' if r.ok else r.status_code}] {label}")
        if r.ok:
            print(f"✓ WORKING FORMAT: {label}")
            exit(0)

print("\nAll formats failed — the key itself may not match the key_id in Kalshi's system.")
print("Try: delete the API key in Kalshi dashboard, create a new one, and re-download the private key.")
