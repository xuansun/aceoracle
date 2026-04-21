#!/bin/bash
# AceOracle — Step 3: Scaffold the Hono x402 API
# ================================================
# Run after Step 1 & 2. This creates the Node.js project
# with Hono + x402 middleware, ready to deploy to Cloudflare Workers.
#
# Usage:
#   chmod +x 03_setup_api.sh && ./03_setup_api.sh

set -e

API_DIR="$(dirname "$0")/../api"
echo "🏗️  Setting up AceOracle API in $API_DIR"

cd "$API_DIR"

# Initialize Node project
echo "📦 Initializing package.json..."
npm init -y > /dev/null 2>&1

# Install dependencies
echo "📥 Installing dependencies..."
npm install hono @hono/node-server @x402/hono @x402/evm @x402/core > /dev/null 2>&1
npm install -D typescript wrangler @cloudflare/workers-types > /dev/null 2>&1

# Create tsconfig
cat > tsconfig.json << 'TSCONFIG'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "outDir": "dist",
    "rootDir": "src",
    "types": ["@cloudflare/workers-types"]
  },
  "include": ["src"]
}
TSCONFIG

# Create wrangler config for Cloudflare Workers
cat > wrangler.toml << 'WRANGLER'
name = "aceoracle"
main = "src/index.ts"
compatibility_date = "2024-01-01"

[vars]
WALLET_ADDRESS = "0xYOUR_BASE_MAINNET_WALLET"
FACILITATOR_URL = "https://facilitator.xpay.sh"

# KV namespace for caching predictions (create via wrangler)
# [[kv_namespaces]]
# binding = "CACHE"
# id = "your-kv-namespace-id"
WRANGLER

# Create source directory
mkdir -p src

echo "✅ API scaffolded! Edit src/index.ts then deploy with: npx wrangler deploy"
echo ""
echo "📋 Next steps:"
echo "  1. Replace WALLET_ADDRESS in wrangler.toml with your Base wallet"
echo "  2. Copy player_elo.csv and model outputs to a data source (Supabase, D1, or KV)"
echo "  3. Edit src/index.ts with your endpoint logic"
echo "  4. Test locally: npx wrangler dev"
echo "  5. Deploy: npx wrangler deploy"
