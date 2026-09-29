-- =====================================================================
-- Pricing functions for the quote tool (schema "TM").
-- Run once:  psql "$DATABASE_URL" -f pricing_functions.sql
--
-- Model (buyback comp, WITH company margin):
--   Every seat carries seats.rank = TM's venue-wide seat-quality rank
--   (manifest seatRanking; 1 = best). Comparable seats = seats of similar
--   quality, wherever they sit -> this captures "nearby sections + comparable
--   rows" in one number.
--
--   To price section+row:
--     1. target_rank = median rank of available seats in that section+row
--        (falls back to the whole section if the row has none).
--     2. candidates = available RESALE seats within +/- rank_window (200) of target.
--     3. restrict to the max_sections sections whose seats are closest in rank
--        (the "related sections"), for safety.
--     4. comp_price = P0 (lowest) of list_price (before fees; = face value) of pool.
--     5. QUOTE (what we offer the seller) = comp_price * (1 - margin).  margin=0.30.
--
-- estimate_seat_price(event, section, [row], [inventory], [pct], [window], [max_sections], [margin])
-- quote(event, section, [row], quantity, [inventory], [pct], [window], [max_sections], [margin])
--
-- basis: 'rank' | 'ga_unsupported' | 'no_data'
-- inventory: 'resale' (default) | 'primary' | NULL (=all)
-- margin: fraction cut from the comp to get our offer (default 0.30 = 30%)
-- =====================================================================

-- Drop any previous overloads so signature changes never leave ambiguous copies.
DO $drop$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT oid::regprocedure AS sig FROM pg_proc
        WHERE pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'TM')
          AND proname IN ('estimate_seat_price', 'quote')
    LOOP
        EXECUTE 'DROP FUNCTION ' || r.sig;
    END LOOP;
END
$drop$;


CREATE OR REPLACE FUNCTION "TM".estimate_seat_price(
    p_event_id       varchar,
    p_section        varchar,
    p_row            varchar  DEFAULT NULL,
    p_inventory_type varchar  DEFAULT 'resale',
    p_percentile     numeric  DEFAULT 0,
    p_rank_window    integer  DEFAULT 200,
    p_max_sections   integer  DEFAULT 7,
    p_margin         numeric  DEFAULT 0.30
)
RETURNS TABLE (
    basis            text,
    target_rank      integer,
    seats_considered integer,
    sections_used    integer,
    related_sections text,
    price            numeric,   -- our QUOTE = comp_price * (1 - margin)
    median_price     numeric,   -- market comp: median list_price of the pool
    low_price        numeric,   -- market comp: min list_price of the pool
    comp_price       numeric,   -- market comp used for the quote (P<pct> list_price)
    margin           numeric    -- the cut applied
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_is_ga boolean;
    v_rank  numeric;
BEGIN
    SELECT (s.seating_type = 'general') INTO v_is_ga
    FROM "TM".sections s
    WHERE s.event_id = p_event_id AND s.name = p_section;

    IF v_is_ga THEN
        RETURN QUERY SELECT 'ga_unsupported'::text, NULL::int, 0, 0, NULL::text,
                            NULL::numeric, NULL::numeric, NULL::numeric,
                            NULL::numeric, p_margin;
        RETURN;
    END IF;

    -- 1) target rank: median rank of available seats in the section+row
    SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY s.rank) INTO v_rank
    FROM "TM".seat_offers so
    JOIN "TM".seats s ON s.event_id = so.event_id AND s.place_id = so.place_id
    WHERE so.event_id = p_event_id AND so.is_available
      AND s.section_name = p_section AND s.rank IS NOT NULL
      AND (p_row IS NULL OR s.row_name = p_row);

    -- fall back to the whole section if that row has no available seats
    IF v_rank IS NULL AND p_row IS NOT NULL THEN
        SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY s.rank) INTO v_rank
        FROM "TM".seat_offers so
        JOIN "TM".seats s ON s.event_id = so.event_id AND s.place_id = so.place_id
        WHERE so.event_id = p_event_id AND so.is_available
          AND s.section_name = p_section AND s.rank IS NOT NULL;
    END IF;

    IF v_rank IS NULL THEN
        RETURN QUERY SELECT 'no_data'::text, NULL::int, 0, 0, NULL::text,
                            NULL::numeric, NULL::numeric, NULL::numeric,
                            NULL::numeric, p_margin;
        RETURN;
    END IF;

    -- 2-5) comparable pool -> comp price -> quote after margin
    RETURN QUERY
    WITH cand AS (
        SELECT s.section_name, s.rank, o.list_price
        FROM "TM".seat_offers so
        JOIN "TM".seats s ON s.event_id = so.event_id AND s.place_id = so.place_id
        JOIN "TM".offers o ON o.event_id = so.event_id AND o.offer_id = so.offer_id
        WHERE so.event_id = p_event_id AND so.is_available
          AND s.rank IS NOT NULL AND abs(s.rank - v_rank) <= p_rank_window
          AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type)
    ),
    s7 AS (
        SELECT section_name
        FROM cand
        GROUP BY section_name
        ORDER BY min(abs(rank - v_rank))
        LIMIT p_max_sections
    ),
    pool AS (
        SELECT c.list_price FROM cand c JOIN s7 USING (section_name)
    ),
    agg AS (
        SELECT count(*)::int AS n,
               (SELECT count(*)::int FROM s7) AS nsec,
               (SELECT string_agg(section_name, ',' ORDER BY section_name) FROM s7) AS secs,
               round((percentile_cont(p_percentile) WITHIN GROUP (ORDER BY list_price))::numeric, 2) AS comp,
               round((percentile_cont(0.5)          WITHIN GROUP (ORDER BY list_price))::numeric, 2) AS med,
               min(list_price) AS lo
        FROM pool
    )
    SELECT CASE WHEN a.n > 0 THEN 'rank' ELSE 'no_data' END,
           v_rank::int,
           a.n,
           a.nsec,
           a.secs,
           round(a.comp * (1 - p_margin), 2),   -- price = our quote after the cut
           a.med,
           a.lo,
           a.comp,                               -- comp_price (pre-cut market comp)
           p_margin
    FROM agg a;
END;
$$;


-- Convenience: quote per seat (after margin) * quantity = subtotal.
CREATE OR REPLACE FUNCTION "TM".quote(
    p_event_id       varchar,
    p_section        varchar,
    p_row            varchar,
    p_quantity       integer,
    p_inventory_type varchar DEFAULT 'resale',
    p_percentile     numeric DEFAULT 0,
    p_rank_window    integer DEFAULT 200,
    p_max_sections   integer DEFAULT 7,
    p_margin         numeric DEFAULT 0.30
)
RETURNS TABLE (
    basis               text,
    target_rank         integer,
    seats_considered    integer,
    sections_used       integer,
    related_sections    text,
    price_per_seat      numeric,   -- our quote per seat (after margin)
    quantity            integer,
    subtotal            numeric,   -- price_per_seat * quantity
    comp_price_per_seat numeric,   -- market comp per seat (pre-cut)
    margin              numeric
)
LANGUAGE sql STABLE
AS $$
    SELECT e.basis, e.target_rank, e.seats_considered, e.sections_used,
           e.related_sections,
           e.price          AS price_per_seat,
           p_quantity       AS quantity,
           round(e.price * p_quantity, 2) AS subtotal,
           e.comp_price     AS comp_price_per_seat,
           e.margin         AS margin
    FROM "TM".estimate_seat_price(p_event_id, p_section, p_row, p_inventory_type,
                                  p_percentile, p_rank_window, p_max_sections, p_margin) e;
$$;
