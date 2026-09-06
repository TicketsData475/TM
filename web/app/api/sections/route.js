import { pool } from "../../../lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const eventId = (searchParams.get("eventId") || "").trim();
  if (!eventId) return Response.json([]);
  try {
    const { rows } = await pool.query(
      'SELECT * FROM "TM".get_event_sections($1)',
      [eventId]
    );
    return Response.json(rows);
  } catch (e) {
    console.error("sections error:", e.message);
    return Response.json({ error: "sections failed" }, { status: 500 });
  }
}
