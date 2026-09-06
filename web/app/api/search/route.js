import { pool } from "../../../lib/db";

export const runtime = "nodejs";        // pg needs Node, not Edge
export const dynamic = "force-dynamic"; // never cache search results

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const q = (searchParams.get("q") || "").trim();
  const limit = Math.min(Number(searchParams.get("limit")) || 20, 50);
  if (q.length < 2) return Response.json([]);
  try {
    const { rows } = await pool.query(
      'SELECT * FROM "TM".search_events($1, $2)',
      [q, limit]
    );
    return Response.json(rows);
  } catch (e) {
    console.error("search error:", e.message);
    return Response.json({ error: "search failed" }, { status: 500 });
  }
}
