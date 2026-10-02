import { pool } from "../../../../lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// aceify customer -> available rows in a section
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const eventId = (searchParams.get("eventId") || "").trim();
  const section = (searchParams.get("section") || "").trim();
  if (!eventId || !section) return Response.json([]);
  try {
    const { rows } = await pool.query(
      'SELECT * FROM "TM".aceify_get_section_rows($1, $2)',
      [eventId, section]
    );
    return Response.json(rows);
  } catch (e) {
    console.error("aceify rows error:", e.message);
    return Response.json({ error: "rows failed" }, { status: 500 });
  }
}
