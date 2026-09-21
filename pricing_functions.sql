-- =====================================================================
-- Pricing functions for the quote tool (schema "TM").
-- Run once:  psql "$DATABASE_URL" -f pricing_functions.sql
--
-- Model (buyback comp), no margin yet:
--   Every seat carries seats.rank = TM's venue-wide seat-quality rank
--   (manifest seatRanking; 1 = best). Comparable seats = seats of similar
--   quality, wherever they sit -> this captures "nearby sections + comparable
--   rows" in one number.
--
--   To price section+row:
--     1. target_rank = median rank of available seats in that section+row
--        (falls back to the whole section if the row has none).
--     2. candidates = available RESALE seats within +/- rank_window (150) of target.
--     3. restrict to the max_sections sections whose seats are closest in rank
--        (the "related sections"), for safety.
--     4. price = P5 of total_price (fees incl.) of that pool.
--
-- estimate_seat_price(event, section, [row], [inventory], [pct], [window], [max_sections])
-- quote(event, section, [row], quantity, [inventory], [pct], [window], [max_sections])
--
-- basis: 'rank' | 'ga_unsupported' | 'no_data'
-- inventory: 'resale' (default) | 'primary' | NULL (=all)
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
    p_percentile     numeric  DEFAULT 0.05,
    p_rank_window    integer  DEFAULT 150,
    p_max_sections   integer  DEFAULT 7
)
RETURNS TABLE (
    basis            text,
    target_rank      integer,
    seats_considered integer,
    sections_used    integer,
    related_sections text,
    price            numeric,
    median_price     numeric,
    low_price        numeric
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
                            NULL::numeric, NULL::numeric, NULL::numeric;
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
                            NULL::numeric, NULL::numeric, NULL::numeric;
        RETURN;
    END IF;

    -- 2-4) comparable pool: similar-rank resale seats, capped to nearest sections
    RETURN QUERY
    WITH cand AS (
        SELECT s.section_name, s.rank, o.total_price
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
        SELECT c.total_price FROM cand c JOIN s7 USING (section_name)
    )
    SELECT CASE WHEN (SELECT count(*) FROM pool) > 0 THEN 'rank' ELSE 'no_data' END,
           v_rank::int,
           (SELECT count(*)::int FROM pool),
           (SELECT count(*)::int FROM s7),
           (SELECT string_agg(section_name, ',' ORDER BY section_name) FROM s7),
           (SELECT round((percentile_cont(p_percentile) WITHIN GROUP (ORDER BY total_price))::numeric, 2) FROM pool),
           (SELECT round((percentile_cont(0.5)          WITHIN GROUP (ORDER BY total_price))::numeric, 2) FROM pool),
           (SELECT min(total_price) FROM pool);
END;
$$;


-- Convenience: reference price * quantity = subtotal. (No margin yet.)
CREATE OR REPLACE FUNCTION "TM".quote(
    p_event_id       varchar,
    p_section        varchar,
    p_row            varchar,
    p_quantity       integer,
    p_inventory_type varchar DEFAULT 'resale',
    p_percentile     numeric DEFAULT 0.05,
    p_rank_window    integer DEFAULT 150,
    p_max_sections   integer DEFAULT 7
)
RETURNS TABLE (
    basis            text,
    target_rank      integer,
    seats_considered integer,
    sections_used    integer,
    related_sections text,
    price_per_seat   numeric,
    quantity         integer,
    subtotal         numeric
)
LANGUAGE sql STABLE
AS $$
    SELECT e.basis, e.target_rank, e.seats_considered, e.sections_used,
           e.related_sections,
           e.price      AS price_per_seat,
           p_quantity   AS quantity,
           round(e.price * p_quantity, 2) AS subtotal
    FROM "TM".estimate_seat_price(p_event_id, p_section, p_row, p_inventory_type,
                                  p_percentile, p_rank_window, p_max_sections) e;
$$;
