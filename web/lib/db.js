import { Pool } from "pg";

// Reuse a single pool across hot-reloads / serverless invocations.
const g = globalThis;

// Connecting from Vercel to a remote Postgres goes over the public internet.
// Enable TLS by setting PGSSL=require (or true) in the Vercel env. Default off
// so local dev keeps working unchanged.
const useSsl = /^(require|true|1)$/i.test(process.env.PGSSL || "");

export const pool =
  g._pgPool ||
  new Pool({
    host: process.env.PGHOST,
    port: process.env.PGPORT ? Number(process.env.PGPORT) : 5432,
    user: process.env.PGUSER,
    password: process.env.PGPASSWORD,
    database: process.env.PGDATABASE,
    // Serverless spins up many isolated instances, each with its own pool —
    // keep per-instance connections small so we don't exhaust Postgres.
    max: process.env.PGPOOLMAX ? Number(process.env.PGPOOLMAX) : 3,
    idleTimeoutMillis: 10_000,
    connectionTimeoutMillis: 10_000,
    ...(useSsl ? { ssl: { rejectUnauthorized: false } } : {}),
  });
if (!g._pgPool) g._pgPool = pool;
