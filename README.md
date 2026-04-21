# 🎾 AceOracle — Tennis Intelligence via x402

Pay-per-call tennis analytics API. Elo ratings, match predictions, and betting edge detection. Powered by 25+ years of ATP data. Paid in USDC on Base via the x402 protocol.

## Quick Start

### Prerequisites
- Python 3.10+ (for data pipeline)
- Node.js 18+ (for API server)
- Git
- A Base wallet with some USDC (for testing payments)

### Step 1: Build the data foundation
```bash
cd scripts
pip install pandas numpy
python 01_download_and_build_elo.py
```
This will:
- Clone Jeff Sackmann's ATP dataset from GitHub
- Parse ~60,000+ matches from 2000–present
- Compute Elo ratings (overall + per surface) for all players
- Output `data/processed/player_elo.csv` and `data/processed/matches_enriched.csv`
- Print a backtest showing ~65%+ accuracy

### Step 2: Train the prediction model
```bash
pip install scikit-learn xgboost
python 02_train_prediction_model.py
```
This will:
- Engineer features (Elo delta, H2H, form, fatigue, surface)
- Train an XGBoost classifier on 2005–2023 data
- Validate on 2024–2025 matches
- Output `models/xgb_match_predictor.json`

### Step 3: Set up the API
```bash
chmod +x 03_setup_api.sh
./03_setup_api.sh
```
This scaffolds the Hono project with x402 middleware.

### Step 4: Configure and deploy
```bash
cd api

# Edit wrangler.toml — set your Base wallet address
# The facilitator is already set to xpay.sh (free, no auth needed)

# Test locally
npx wrangler dev

# Deploy to Cloudflare Workers
npx wrangler deploy
```

### Step 5: Test a payment
```bash
# Your API is now live! Test the free discovery endpoint:
curl https://aceoracle.YOUR_SUBDOMAIN.workers.dev/

# To test a paid endpoint, use an x402 client:
# npm install @x402/fetch
# See docs at https://docs.x402.org
```

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Data Layer (Python, runs nightly via cron)       │
│  ├── Sackmann GitHub → parse CSVs                │
│  ├── Compute Elo ratings (overall + surface)     │
│  ├── Train/update XGBoost model                  │
│  └── Push to Supabase / Cloudflare D1 / KV       │
├──────────────────────────────────────────────────┤
│  API Layer (Hono on Cloudflare Workers)           │
│  ├── @x402/hono middleware (xpay.sh facilitator) │
│  ├── USDC on Base mainnet                        │
│  ├── Cached predictions in KV                    │
│  └── 6 endpoints: $0.01 – $0.50 per call        │
├──────────────────────────────────────────────────┤
│  Consumers                                        │
│  ├── AI agents (Claude, ChatGPT, custom)         │
│  ├── Betting bots                                │
│  ├── Fantasy tennis apps                         │
│  └── Sports analytics platforms                  │
└──────────────────────────────────────────────────┘
```

## Endpoints

| Endpoint              | Price  | Description                         |
|-----------------------|--------|-------------------------------------|
| `GET /`               | Free   | API discovery + schema              |
| `GET /player/:id`     | $0.01  | Player profile + Elo ratings        |
| `GET /matchup`        | $0.05  | Head-to-head analysis               |
| `GET /predict`        | $0.10  | Match outcome prediction            |
| `GET /tournament/:slug` | $0.25 | Tournament bracket predictions     |
| `GET /edge-finder`    | $0.50  | Betting edge detection              |

## Data Sources

- **Match data**: [Jeff Sackmann / Tennis Abstract](https://github.com/JeffSackmann/tennis_atp) (CC-BY-NC-SA 4.0)
- **Odds data**: The Odds API (free tier, Phase 4)
- **Live scores**: TBD (Phase 5)

## Facilitator

Using **xpay.sh** (`https://facilitator.xpay.sh`):
- Free, no API key required
- Supports Base mainnet + Base Sepolia testnet
- Sponsors gas for settlement transactions
- Zero fees for merchants

To switch to CDP Facilitator later (for Bazaar auto-listing + OFAC screening):
1. Sign up at cdp.coinbase.com
2. Create API key
3. Update `FACILITATOR_URL` in wrangler.toml

## License

API code: MIT
Tennis data: CC-BY-NC-SA 4.0 (per Sackmann's license — commercial use of the raw data requires permission; your derived predictions and Elo ratings as an API service should be reviewed against the license terms)
