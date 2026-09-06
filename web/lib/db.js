import { Pool } from "pg";

// Reuse a single pool across hot-reloads / requests.
const g = globalThis;
export const pool =
  g._pgPool ||
  new Pool({
    host: process.env.PGHOST,
    port: process.env.PGPORT ? Number(process.env.PGPORT) : 5432,
    user: process.env.PGUSER,
    password: process.env.PGPASSWORD,
    database: process.env.PGDATABASE,
    max: 5,
  });
if (!g._pgPool) g._pgPool = pool;
