import { pool } from "../../../lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const eventId = (searchParams.get("eventId") || "").trim();
  const section = (searchParams.get("section") || "").trim();
  const row = (searchParams.get("row") || "").trim() || null; // optional
  const quantity = Math.max(1, Math.min(Number(searchParams.get("quantity")) || 1, 100));
  // default to resale (the right comp for a buyback); ?inventory=all uses everything
  const inv = (searchParams.get("inventory") || "resale").toLowerCase();
  const invArg = inv === "all" ? null : "resale";

  if (!eventId || !section) return Response.json(null);
  try {
    const { rows } = await pool.query(
      'SELECT * FROM "TM".quote($1, $2, $3, $4, $5)',
      [eventId, section, row, quantity, invArg]
    );
    return Response.json(rows[0] || null);
  } catch (e) {
    console.error("quote error:", e.message);
    return Response.json({ error: "quote failed" }, { status: 500 });
  }
}
