// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

// Collector for ISP's anonymous telemetry (backend/src/isp/services/infra/telemetry.py).
// Accepts GET /ping?<fields>, stores only the whitelisted fields plus the
// network organisation / country Cloudflare attaches to the request.
// The raw IP address is never stored.

const FIELDS = [
  "event", "install_id", "version", "os", "arch", "python",
  "typesense_version", "embedding_provider", "chat_provider", "index_count_bucket",
];
const EVENTS = new Set(["platform_start", "heartbeat"]);
const MAX_LEN = 64;

const SCHEMA = `CREATE TABLE IF NOT EXISTS pings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL,
  ${FIELDS.map((f) => `${f} TEXT`).join(",\n  ")},
  org TEXT, asn INTEGER, country TEXT
)`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/ping") {
      return new Response("Not found", { status: 404 });
    }

    const values = FIELDS.map((f) => (url.searchParams.get(f) || "").slice(0, MAX_LEN));
    const event = values[0];
    if (!EVENTS.has(event) || !values[1]) {
      return new Response("Bad request", { status: 400 });
    }

    const cf = request.cf || {};
    await env.DB.prepare(SCHEMA).run();
    await env.DB.prepare(
      `INSERT INTO pings (received_at, ${FIELDS.join(", ")}, org, asn, country)
       VALUES (?, ${FIELDS.map(() => "?").join(", ")}, ?, ?, ?)`,
    ).bind(
      new Date().toISOString(), ...values,
      cf.asOrganization || null, cf.asn || null, cf.country || null,
    ).run();

    return new Response(null, { status: 204 });
  },
};
