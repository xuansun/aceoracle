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
    },
  });
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

export default app;
