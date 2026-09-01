-- =====================================================================
-- Reusable search functions for the quote-tool backend (schema "TM").
-- Run once against your DB:  psql "$DATABASE_URL" -f search_functions.sql
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Only ONE new index is needed: the trigram index on title (for fuzzy search).
-- events(start_date) and seats(event_id, section_name) are ALREADY indexed by the
-- schema (idx_events_start_date, idx_seats_event_section), so no extras here.
CREATE INDEX IF NOT EXISTS events_title_trgm
  ON "TM".events USING gin (title gin_trgm_ops);


-- ---------------------------------------------------------------------
-- 1) search_events(query, limit)
--    Fuzzy, typo-tolerant search on event NAME. FUTURE events only.
--    Usage:  SELECT * FROM "TM".search_events('raiders');
--            SELECT * FROM "TM".search_events('taylr swft', 30);
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION "TM".search_events(p_query text, p_limit int DEFAULT 20)
RETURNS TABLE (
    event_id    varchar,
    title       varchar,
    start_date  timestamp,
    url         varchar,
    venue_name  varchar,
    city        varchar,
    state       varchar,
    score       real
)
LANGUAGE sql STABLE
AS $$
    SELECT e.id, e.title, e.start_date, e.url,
           v.name, v.city, v.state,
           word_similarity(p_query, e.title) AS score
    FROM "TM".events e
    JOIN "TM".venues v ON v.id = e.venue_id
    WHERE e.start_date >= (now() AT TIME ZONE 'UTC')          -- future only (dates are UTC)
      AND (e.title %> p_query OR e.title ILIKE '%' || p_query || '%')
    ORDER BY word_similarity(p_query, e.title) DESC, e.start_date
    LIMIT p_limit;
$$;


-- ---------------------------------------------------------------------
-- 2) get_event_sections(event_id)
--    All sections for a chosen event (NO rows yet).
--    Usage:  SELECT * FROM "TM".get_event_sections('1700646CC3B3C3BF');
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION "TM".get_event_sections(p_event_id varchar)
RETURNS TABLE (
    section_name  varchar,
    seating_type  varchar,
    num_seats     int
)
LANGUAGE sql STABLE
AS $$
    SELECT s.name, s.seating_type, s.num_seats
    FROM "TM".sections s
    WHERE s.event_id = p_event_id
    ORDER BY s.name;
$$;


-- ---------------------------------------------------------------------
-- 3) get_section_rows(event_id, section)
--    Rows in a section that currently have available seats.
--    Usage:  SELECT * FROM "TM".get_section_rows('1700646CC3B3C3BF', '319');
--    NOTE: 'available'-only scrape -> returns only rows with seats for sale.
--          (Full row list needs the future `rows` table — see future_improvements.md)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION "TM".get_section_rows(p_event_id varchar, p_section varchar)
RETURNS TABLE (row_name varchar)
LANGUAGE sql STABLE
AS $$
    SELECT DISTINCT s.row_name
    FROM "TM".seats s
    WHERE s.event_id = p_event_id
      AND s.section_name = p_section
      AND s.row_name IS NOT NULL
    ORDER BY s.row_name;
$$;
