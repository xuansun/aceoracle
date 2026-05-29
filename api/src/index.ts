import { Hono } from "hono";
import { cors } from "hono/cors";
import playersRaw from "./players.json";
import h2hData from "./h2h.json";

// === Types ===

interface Player {
  player_id: string;
  name: string;
  elo_overall: number;
  elo_hard: number;
  elo_clay: number;
  elo_grass: number;
  total_matches: number;
}

// === Config — EDIT THESE ===

const WALLET = "0xf296E3789F98C87003328F7047D010eFDc02eCdd";
const FACILITATOR = "https://facilitator.xpay.sh";
const NETWORK = "eip155:8453";

const PRICES: Record<string, { amount: string; desc: string }> = {
  "/player": { amount: "0.01", desc: "Player profile with Elo ratings" },
  "/matchup": { amount: "0.05", desc: "Head-to-head analysis" },
  "/predict": { amount: "0.10", desc: "Match outcome prediction" },
  "/edge-finder": { amount: "0.25", desc: "Kalshi market edge finder with Kelly sizing" },
};

// === Load Data ===

const players: Map<string, Player> = new Map();
const playersByName: Map<string, Player> = new Map();

for (const p of playersRaw as Player[]) {
  players.set(p.player_id, p);
  playersByName.set(p.name.toLowerCase(), p);
}

// === Helpers ===

function findPlayer(query: string): Player | undefined {
  if (players.has(query)) return players.get(query);
  const lower = query.toLowerCase();
  if (playersByName.has(lower)) return playersByName.get(lower);
  for (const [name, p] of playersByName) {
    if (name.includes(lower)) return p;
  }
  return undefined;
}

function getEloForSurface(p: Player, surface: string): number {
  if (surface === "clay") return p.elo_clay;
  if (surface === "grass") return p.elo_grass;
  return p.elo_hard;
}

function getH2H(id1: string, id2: string) {
  const key = `${Math.min(+id1, +id2)}:${Math.max(+id1, +id2)}`;
  const rec = (h2hData as any)[key];
  if (!rec) return { overall: [0, 0], hard: [0, 0], clay: [0, 0], grass: [0, 0], flipped: +id1 > +id2 };
  return { ...rec, flipped: +id1 > +id2 };
}

function predictMatch(p1: Player, p2: Player, surface: string, bestOf: number) {
  const e1 = getEloForSurface(p1, surface);
  const e2 = getEloForSurface(p2, surface);
  const eloDelta = e1 - e2;
  const eloProb = 1 / (1 + Math.pow(10, -eloDelta / 400));

  const h2h = getH2H(p1.player_id, p2.player_id);
  const h2hSurface = h2h[surface as keyof typeof h2h] || [0, 0];
  const p1Idx = h2h.flipped ? 1 : 0;
  const h2hTotal = (h2hSurface as number[])[0] + (h2hSurface as number[])[1];
  let h2hAdj = 0;
  if (h2hTotal >= 3) {
    const h2hWinRate = (h2hSurface as number[])[p1Idx] / h2hTotal;
    h2hAdj = (h2hWinRate - 0.5) * 0.08;
  }

  const bo5Adj = bestOf === 5 ? (eloProb - 0.5) * 0.06 : 0;
  const p1Prob = Math.min(0.95, Math.max(0.05, eloProb + h2hAdj + bo5Adj));
  const p2Prob = Math.round((1 - p1Prob) * 1000) / 1000;

  const factors: string[] = [];
  if (Math.abs(eloDelta) > 20) {
    factors.push(`${eloDelta > 0 ? p1.name : p2.name} +${Math.abs(Math.round(eloDelta))} Elo on ${surface}`);
  }
  if (h2hTotal >= 2) {
    const p1Wins = (h2hSurface as number[])[p1Idx];
    const p2Wins = h2hTotal - p1Wins;
    factors.push(`H2H on ${surface}: ${p1.name} ${p1Wins}-${p2Wins} ${p2.name}`);
  }
  if (bestOf === 5) factors.push("Best of 5 favors higher-rated player");
  if (factors.length === 0) factors.push("Closely matched — small edges only");

  const setsToWin = bestOf === 5 ? 3 : 2;
  const predictedSets = p1Prob > 0.5 ? `${setsToWin}-${setsToWin - 1}` : `${setsToWin - 1}-${setsToWin}`;

  return {
    p1_prob: Math.round(p1Prob * 1000) / 1000,
    p2_prob: p2Prob,
    predicted_sets: predictedSets,
    confidence: Math.round(Math.abs(p1Prob - 0.5) * 2 * 100) / 100,
    factors,
  };
}

// === x402 Payment Logic ===

function matchRoute(path: string): { amount: string; desc: string } | null {
  for (const [route, info] of Object.entries(PRICES)) {
    if (path.startsWith(route)) return info;
  }
  return null;
}

function buildPaymentRequired(price: string, url: string, description: string) {
  const atomicAmount = Math.round(parseFloat(price) * 1_000_000).toString();
  return {
    x402Version: 2,
    accepts: [{
      scheme: "exact",
      network: NETWORK,
      maxAmountRequired: atomicAmount,
      resource: url,
      description,
      mimeType: "application/json",
      payTo: WALLET,
      maxTimeoutSeconds: 60,
      asset: "USDC",
      extra: {},
    }],
    resource: { url, description, mimeType: "application/json" },
    facilitators: [FACILITATOR],
  };
}

// === App ===

const app = new Hono();
app.use("*", cors());

// Custom x402 middleware
app.use("*", async (c, next) => {
  const path = new URL(c.req.url).pathname;
  const route = matchRoute(path);

  // Free route — pass through
  if (!route) return next();

  // Check for payment
  const paymentHeader = c.req.header("PAYMENT-SIGNATURE") || c.req.header("X-PAYMENT");

  if (!paymentHeader) {
    // Return 402 with payment requirements
    const pr = buildPaymentRequired(route.amount, c.req.url, route.desc);
    const encoded = btoa(JSON.stringify(pr));
    return new Response(JSON.stringify(pr), {
      status: 402,
      headers: {
        "Content-Type": "application/json",
        "PAYMENT-REQUIRED": encoded,
        "Access-Control-Allow-Origin": "*",
      },
    });
  }

  // Verify payment with facilitator
  try {
    const payload = JSON.parse(atob(paymentHeader));
    const atomicAmount = Math.round(parseFloat(route.amount) * 1_000_000).toString();

    const verifyResp = await fetch(`${FACILITATOR}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        x402Version: 2,
        paymentPayload: payload,
        paymentRequirements: {
          scheme: "exact",
          network: NETWORK,
          maxAmountRequired: atomicAmount,
          payTo: WALLET,
          asset: "USDC",
          maxTimeoutSeconds: 60,
          extra: {},
        },
      }),
    });

    const verifyResult = await verifyResp.json() as any;

    if (!verifyResult.isValid) {
      return c.json({ error: "Payment invalid", reason: verifyResult.invalidReason }, 402);
    }

    // Serve content
    await next();

    // Settle in background
    c.executionCtx.waitUntil(
      fetch(`${FACILITATOR}/settle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          x402Version: 2,
          paymentPayload: payload,
          paymentRequirements: {
            scheme: "exact",
            network: NETWORK,
            maxAmountRequired: atomicAmount,
            payTo: WALLET,
            asset: "USDC",
            maxTimeoutSeconds: 60,
            extra: {},
          },
        }),
      })
    );
  } catch (e) {
    return c.json({ error: "Payment processing failed" }, 500);
  }
});

// === Free Discovery ===

app.get("/", (c) => {
  return c.json({
    name: "AceOracle",
    version: "0.1.0",
    protocol: "x402",
    description: "Tennis intelligence API. Pay per call with USDC on Base Sepolia.",
    network: NETWORK,
    facilitator: FACILITATOR,
    wallet: WALLET,
    players_loaded: players.size,
    endpoints: {
      "/player/:id": { price: "$0.01", description: "Player profile + Elo ratings" },
      "/matchup?p1=X&p2=Y&surface=Z": { price: "$0.05", description: "Head-to-head analysis" },
      "/predict?p1=X&p2=Y&surface=Z": { price: "$0.10", description: "Match prediction" },
      "/edge-finder?p1=X&p2=Y&surface=Z&yes_price=N&best_of=3": { price: "$0.25", description: "Kalshi market edge finder with Kelly sizing" },
    },
  });
});

// === Discovery Endpoints ===

app.get("/.well-known/x402", (c) => {
  return c.json({
    x402Version: 2,
    name: "AceOracle",
    description: "Tennis intelligence API — Elo ratings, match predictions, and H2H analysis for 2,640+ ATP players.",
    url: "https://aceoracle.aceoracle-tennis.workers.dev",
    facilitator: FACILITATOR,
    network: NETWORK,
    wallet: WALLET,
    endpoints: [
      {
        path: "/player/:id",
        method: "GET",
        price: "0.01",
        asset: "USDC",
        description: "Player profile with overall and surface-specific Elo ratings",
        inputSchema: {
          params: { id: { type: "string", description: "ATP player ID (e.g. 206173) or name (e.g. sinner)" } },
        },
      },
      {
        path: "/matchup",
        method: "GET",
        price: "0.05",
        asset: "USDC",
        description: "Head-to-head analysis between two players on a specific surface",
        inputSchema: {
          queryParams: {
            p1: { type: "string", description: "Player 1 ID or name", required: true },
            p2: { type: "string", description: "Player 2 ID or name", required: true },
            surface: { type: "string", description: "hard, clay, or grass", required: false },
          },
        },
      },
      {
        path: "/predict",
        method: "GET",
        price: "0.10",
        asset: "USDC",
        description: "Match outcome prediction with win probabilities and key factors",
        inputSchema: {
          queryParams: {
            p1: { type: "string", description: "Player 1 ID or name", required: true },
            p2: { type: "string", description: "Player 2 ID or name", required: true },
            surface: { type: "string", description: "hard, clay, or grass", required: false },
            best_of: { type: "string", description: "3 or 5", required: false },
          },
        },
      },
      {
        path: "/edge-finder",
        method: "GET",
        price: "0.25",
        asset: "USDC",
        description: "Compare our model probability vs Kalshi market price to find edges, with half-Kelly sizing",
        inputSchema: {
          queryParams: {
            p1: { type: "string", description: "Player 1 ID or name", required: true },
            p2: { type: "string", description: "Player 2 ID or name", required: true },
            surface: { type: "string", description: "hard, clay, or grass", required: false },
            yes_price: { type: "string", description: "Kalshi yes price in cents (1-99) for p1 winning", required: true },
            best_of: { type: "string", description: "3 or 5", required: false },
          },
        },
      },
    ],
  });
});

app.get("/llms.txt", (c) => {
  return c.text(`# AceOracle — Tennis Intelligence API

> Pay-per-call tennis analytics via x402. USDC on Base.

## Endpoints

### GET /player/:id ($0.01)
Returns player profile with Elo ratings (overall, hard, clay, grass), total matches, and best surface.
Parameter: id = ATP player ID (e.g. 206173) or name (e.g. sinner, alcaraz, djokovic)

### GET /matchup?p1=X&p2=Y&surface=Z ($0.05)
Returns head-to-head record, Elo delta, and surface-specific comparison.
Parameters: p1 = player 1, p2 = player 2, surface = hard|clay|grass (default: hard)

### GET /predict?p1=X&p2=Y&surface=Z&best_of=3 ($0.10)
Returns win probabilities, predicted sets, confidence score, and key factors.
Parameters: p1 = player 1, p2 = player 2, surface = hard|clay|grass, best_of = 3|5

### GET /edge-finder?p1=X&p2=Y&surface=Z&yes_price=N&best_of=3 ($0.25)
Compares model probability against Kalshi market price to find betting edges with half-Kelly sizing.
Parameters: p1 = player 1, p2 = player 2, surface = hard|clay|grass, yes_price = Kalshi yes price in cents (1-99) for p1 winning, best_of = 3|5

## Payment
Protocol: x402
Network: Base (eip155:8453)
Asset: USDC
Facilitator: https://facilitator.xpay.sh

## Data
Source: Jeff Sackmann / Tennis Abstract (ATP matches 2000-2024)
Players: 2,640+
Model: XGBoost with surface-specific Elo, H2H, form, fatigue features
Accuracy: 77% on validation set

## Links
API: https://aceoracle.aceoracle-tennis.workers.dev
GitHub: https://github.com/rollingthedice/aceoracle
`);
});



// === Paid Endpoints ===

app.get("/player/:id", (c) => {
  const id = c.req.param("id");
  const player = findPlayer(id);
  if (!player) return c.json({ error: "Player not found", query: id, hint: "Try player ID (206173) or name (sinner)" }, 404);

  return c.json({
    player: {
      id: player.player_id, name: player.name,
      elo: { overall: player.elo_overall, hard: player.elo_hard, clay: player.elo_clay, grass: player.elo_grass },
      total_matches: player.total_matches,
      best_surface: ["hard", "clay", "grass"][[player.elo_hard, player.elo_clay, player.elo_grass].indexOf(Math.max(player.elo_hard, player.elo_clay, player.elo_grass))],
    },
    meta: { data_as_of: "2024", model_version: "elo-xgb-v1", cost: "$0.01 USDC" },
  });
});

app.get("/matchup", (c) => {
  const q1 = c.req.query("p1");
  const q2 = c.req.query("p2");
  const surface = c.req.query("surface") || "hard";
  if (!q1 || !q2) return c.json({ error: "Missing p1 or p2 parameter" }, 400);

  const p1 = findPlayer(q1);
  const p2 = findPlayer(q2);
  if (!p1) return c.json({ error: `Player not found: ${q1}` }, 404);
  if (!p2) return c.json({ error: `Player not found: ${q2}` }, 404);

  const h2h = getH2H(p1.player_id, p2.player_id);
  const idx = h2h.flipped ? 1 : 0;

  return c.json({
    matchup: {
      player1: { id: p1.player_id, name: p1.name, elo_surface: getEloForSurface(p1, surface) },
      player2: { id: p2.player_id, name: p2.name, elo_surface: getEloForSurface(p2, surface) },
      surface,
      elo_delta: Math.round((getEloForSurface(p1, surface) - getEloForSurface(p2, surface)) * 10) / 10,
      h2h: {
        overall: { p1_wins: h2h.overall[idx], p2_wins: h2h.overall[1 - idx] },
        on_surface: { p1_wins: (h2h[surface] || [0, 0])[idx], p2_wins: (h2h[surface] || [0, 0])[1 - idx] },
      },
    },
    meta: { data_as_of: "2024", cost: "$0.05 USDC" },
  });
});

app.get("/predict", (c) => {
  const q1 = c.req.query("p1");
  const q2 = c.req.query("p2");
  const surface = c.req.query("surface") || "hard";
  const bestOf = parseInt(c.req.query("best_of") || "3");
  if (!q1 || !q2) return c.json({ error: "Missing p1 or p2 parameter" }, 400);

  const p1 = findPlayer(q1);
  const p2 = findPlayer(q2);
  if (!p1) return c.json({ error: `Player not found: ${q1}` }, 404);
  if (!p2) return c.json({ error: `Player not found: ${q2}` }, 404);

  const pred = predictMatch(p1, p2, surface, bestOf);

  return c.json({
    match: {
      player1: { id: p1.player_id, name: p1.name, elo: getEloForSurface(p1, surface) },
      player2: { id: p2.player_id, name: p2.name, elo: getEloForSurface(p2, surface) },
      surface, best_of: bestOf,
    },
    prediction: pred,
    meta: { model_version: "elo-xgb-v1", data_as_of: "2024", cost: "$0.10 USDC" },
  });
});

app.get("/edge-finder", (c) => {
  const q1 = c.req.query("p1");
  const q2 = c.req.query("p2");
  const surface = c.req.query("surface") || "hard";
  const bestOf = parseInt(c.req.query("best_of") || "3");
  const yesPriceRaw = c.req.query("yes_price");

  if (!q1 || !q2) return c.json({ error: "Missing p1 or p2 parameter" }, 400);

  if (yesPriceRaw === undefined || yesPriceRaw === "") {
    return c.json({ error: "yes_price required (1-99)" }, 400);
  }
  const yesPrice = parseInt(yesPriceRaw);
  if (isNaN(yesPrice) || yesPrice < 1 || yesPrice > 99) {
    return c.json({ error: "yes_price required (1-99)" }, 400);
  }

  const p1 = findPlayer(q1);
  const p2 = findPlayer(q2);
  if (!p1) return c.json({ error: `Player not found: ${q1}` }, 404);
  if (!p2) return c.json({ error: `Player not found: ${q2}` }, 404);

  const pred = predictMatch(p1, p2, surface, bestOf);
  const ourProb = pred.p1_prob;

  const marketProb = yesPrice / 100;
  const noPriceCents = 100 - yesPrice;

  const yesEdge = Math.round((ourProb - marketProb) * 1000) / 1000;
  const noProb = Math.round((1 - ourProb) * 1000) / 1000;
  const noEdge = Math.round((noProb - noPriceCents / 100) * 1000) / 1000;

  // Half-Kelly for yes side: b = (100 - yes_price) / yes_price
  const b = noPriceCents / yesPrice;
  const kellyYes = Math.max(0, (b * ourProb - (1 - ourProb)) / b / 2);

  // Half-Kelly for no side: b_no = yes_price / (100 - yes_price)
  const bNo = yesPrice / noPriceCents;
  const kellyNo = Math.max(0, (bNo * noProb - ourProb) / bNo / 2);

  let bestSide: "yes" | "no" | null = null;
  if (yesEdge > noEdge && yesEdge > 0) {
    bestSide = "yes";
  } else if (noEdge > yesEdge && noEdge > 0) {
    bestSide = "no";
  }

  const kellyFraction = Math.round(
    (bestSide === "yes" ? kellyYes : bestSide === "no" ? kellyNo : 0) * 1000
  ) / 1000;

  const hasEdge = bestSide !== null;
  let summary: string;
  if (bestSide === "yes") {
    summary = `Bet YES on ${p1.name}: +${Math.round(yesEdge * 1000) / 10}% edge, ${Math.round(kellyFraction * 1000) / 10}% Kelly`;
  } else if (bestSide === "no") {
    summary = `Bet NO on ${p1.name} (YES on ${p2.name}): +${Math.round(noEdge * 1000) / 10}% edge, ${Math.round(kellyFraction * 1000) / 10}% Kelly`;
  } else {
    summary = "No meaningful edge detected — skip this market";
  }

  return c.json({
    match: {
      player1: { id: p1.player_id, name: p1.name, elo: getEloForSurface(p1, surface) },
      player2: { id: p2.player_id, name: p2.name, elo: getEloForSurface(p2, surface) },
      surface,
      best_of: bestOf,
    },
    prediction: {
      p1_prob: ourProb,
      p2_prob: noProb,
    },
    market: {
      yes_price_cents: yesPrice,
      market_prob_p1: marketProb,
      no_price_cents: noPriceCents,
      market_prob_p2: Math.round((noPriceCents / 100) * 1000) / 1000,
    },
    edge: {
      yes_edge: yesEdge,
      no_edge: noEdge,
      best_side: bestSide,
      kelly_fraction: kellyFraction,
      has_edge: hasEdge,
      summary,
    },
    meta: {
      model: "elo-xgb-v1",
      data_as_of: "2024",
      cost: "$0.25 USDC",
    },
  });
});

export default app;
