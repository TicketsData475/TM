-- =====================================================================
-- Reusable search functions for the quote-tool backend (schema "TM").
-- Run once against your DB:  psql "$DATABASE_URL" -f search_functions.sql
--
-- Per-customer: search_events returns ONE customer's events (default 'TC').
-- aceify_* wrappers are the aceify customer's API (customer='aceify' = tennis).
-- The detail/row functions are event-id driven (shared logic; aceify_* just
-- namespaces them for the aceify backend).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS events_title_trgm
  ON "TM".events USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_events_customer ON "TM".events ("customer");

-- drop old overloads so signature changes never leave ambiguous copies
DO $drop$
DECLARE r record;
BEGIN
    FOR r IN SELECT oid::regprocedure AS sig FROM pg_proc
             WHERE pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'TM')
               AND proname IN ('search_events', 'aceify_search_events',
                               'aceify_get_event_sections', 'aceify_get_section_rows')
    LOOP EXECUTE 'DROP FUNCTION ' || r.sig; END LOOP;
END $drop$;


-- ---------------------------------------------------------------------
-- 1) search_events(query, limit, customer, offset)
--    Fuzzy, typo-tolerant search on event NAME, FUTURE events only, scoped to
--    one customer. Default customer 'TC' -> the existing league client.
--    p_offset supports "Load more" paging (skip the first N already shown).
--    Usage:  SELECT * FROM "TM".search_events('raiders');
--            SELECT * FROM "TM".search_events('raiders', 20, 'TC', 20); -- page 2
-- ---------------------------------------------------------------------
CREATE FUNCTION "TM".search_events(p_query text, p_limit int DEFAULT 20,
                                   p_customer varchar DEFAULT 'TC',
                                   p_offset int DEFAULT 0)
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
    WHERE e.customer = p_customer
      AND e.start_date >= (now() AT TIME ZONE 'UTC')          -- future only (dates are UTC)
      AND (e.title %> p_query OR e.title ILIKE '%' || p_query || '%')
    ORDER BY word_similarity(p_query, e.title) DESC, e.start_date, e.id
    LIMIT p_limit OFFSET greatest(p_offset, 0);
$$;

-- aceify customer's search -> only tennis (customer='aceify')
CREATE FUNCTION "TM".aceify_search_events(p_query text, p_limit int DEFAULT 20,
                                          p_offset int DEFAULT 0)
RETURNS TABLE (
    event_id varchar, title varchar, start_date timestamp, url varchar,
    venue_name varchar, city varchar, state varchar, score real
)
LANGUAGE sql STABLE
AS $$ SELECT * FROM "TM".search_events(p_query, p_limit, 'aceify', p_offset); $$;


-- ---------------------------------------------------------------------
-- 2) get_event_sections(event_id)  (+ aceify_ alias)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION "TM".get_event_sections(p_event_id varchar)
RETURNS TABLE (section_name varchar, seating_type varchar, num_seats int)
LANGUAGE sql STABLE
AS $$
    SELECT s.name, s.seating_type, s.num_seats
    FROM "TM".sections s
    WHERE s.event_id = p_event_id
    ORDER BY s.name;
$$;

CREATE FUNCTION "TM".aceify_get_event_sections(p_event_id varchar)
RETURNS TABLE (section_name varchar, seating_type varchar, num_seats int)
LANGUAGE sql STABLE
AS $$ SELECT * FROM "TM".get_event_sections(p_event_id); $$;


-- ---------------------------------------------------------------------
-- 3) get_section_rows(event_id, section)  (+ aceify_ alias)
--    Rows in a section that currently have available seats.
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

CREATE FUNCTION "TM".aceify_get_section_rows(p_event_id varchar, p_section varchar)
RETURNS TABLE (row_name varchar)
LANGUAGE sql STABLE
AS $$ SELECT * FROM "TM".get_section_rows(p_event_id, p_section); $$;
