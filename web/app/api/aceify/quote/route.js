import { pool } from "../../../../lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/* Parse a number from the query, clamp to [min,max]; fall back to `dflt`
   when missing or not a number. */
function num(sp, key, dflt, min, max) {
  const raw = sp.get(key);
  if (raw === null || raw === "") return dflt;
  const n = Number(raw);
  if (!Number.isFinite(n)) return dflt;
  return Math.min(Math.max(n, min), max);
}

// aceify customer quote -> "TM".aceify_quote(...) with overridable params.
// Defaults chosen for tennis / Australian Open:
//   inventory = 'primary'  (per-seat face-value inventory, not resale)
//   percentile 0 (lowest comp)  rank window 200  max sections 7  margin 30%
export async function GET(request) {
  const { searchParams: sp } = new URL(request.url);
  const eventId = (sp.get("eventId") || "").trim();
  const section = (sp.get("section") || "").trim();
  const row = (sp.get("row") || "").trim() || null; // optional
  const quantity = Math.max(1, Math.min(Number(sp.get("quantity")) || 1, 100));

  // inventory basis: primary (default) | resale | all(=null -> both)
  const inv = (sp.get("inventory") || "primary").toLowerCase();
  const invArg = inv === "all" ? null : inv === "resale" ? "resale" : "primary";

  // overridable tuning params (UI sends percentile & margin as PERCENTS)
  const percentile = num(sp, "percentile", 0, 0, 100) / 100;      // -> fraction 0..1
  const rankWindow = Math.round(num(sp, "rankWindow", 200, 1, 100000));
  const maxSections = Math.round(num(sp, "maxSections", 7, 1, 100));
  const margin = num(sp, "margin", 30, 0, 95) / 100;              // -> fraction 0..0.95

  if (!eventId || !section) return Response.json(null);
  try {
    const { rows } = await pool.query(
      'SELECT * FROM "TM".aceify_quote($1, $2, $3, $4, $5, $6, $7, $8, $9)',
      [eventId, section, row, quantity, invArg, percentile, rankWindow, maxSections, margin]
    );
    return Response.json(rows[0] || null);
  } catch (e) {
    console.error("aceify quote error:", e.message);
    return Response.json({ error: "quote failed" }, { status: 500 });
  }
}
