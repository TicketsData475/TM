-- =====================================================================
-- Pricing functions for the quote tool (schema "TM").
-- Run once:  psql "$DATABASE_URL" -f pricing_functions.sql
--
-- Model (buyback comp), no margin yet:
--   RESALE inventory, total_price (fees included), 10th percentile (P10) = the
--   competitive get-in, robust to greedy outlier listings.
--
--   Comparable pool (finest available):
--     row given  -> the row's 4-ROW BAND of the section (rows grouped in
--                   consecutive blocks of 4 by physical position)
--     else       -> the whole section
--     GA section -> not priced yet
--
-- estimate_seat_price(event, section, [row], [inventory_type], [percentile])
-- quote(event, section, [row], quantity, [inventory_type], [percentile])
--
-- basis:  'band' (band col = the row-name range, e.g. 'A-D') | 'section'
--         | 'ga_unsupported' | 'no_data'
-- inventory_type: 'resale' (default) | 'primary' | NULL (=all)
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
    p_percentile     numeric  DEFAULT 0.10
)
RETURNS TABLE (
    basis            text,
    band             text,
    seats_considered integer,
    price            numeric,   -- p_percentile of total_price (the quote reference)
    median_price     numeric,   -- 50th percentile of total_price (context)
    low_price        numeric    -- cheapest available (context)
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    band_size constant integer := 4;   -- rows per band
    v_is_ga   boolean;
    v_total   integer;
    v_pos     integer;
    v_lo      integer;
    v_hi      integer;
    v_band    text;
    v_first   text;
    v_last    text;
    v_count   integer;
BEGIN
    -- GA sections have no per-seat data in this schema yet.
    SELECT (s.seating_type = 'general') INTO v_is_ga
    FROM "TM".sections s
    WHERE s.event_id = p_event_id AND s.name = p_section;

    IF v_is_ga THEN
        RETURN QUERY SELECT 'ga_unsupported'::text, NULL::text, 0,
                            NULL::numeric, NULL::numeric, NULL::numeric;
        RETURN;
    END IF;

    -- ---- 1) Row given: price the row's 4-row band ----
    IF p_row IS NOT NULL THEN
        SELECT count(*) INTO v_total
        FROM "TM".section_rows sr
        WHERE sr.event_id = p_event_id AND sr.section_name = p_section;

        SELECT sr.position INTO v_pos
        FROM "TM".section_rows sr
        WHERE sr.event_id = p_event_id AND sr.section_name = p_section
          AND sr.row_name = p_row;

        IF v_pos IS NOT NULL AND v_total > 0 THEN
            v_lo := (v_pos / band_size) * band_size;      -- block start (0,4,8,...)
            v_hi := v_lo + band_size - 1;

            -- label the band by its first..last actual row names
            SELECT sr.row_name INTO v_first FROM "TM".section_rows sr
            WHERE sr.event_id = p_event_id AND sr.section_name = p_section
              AND sr.position = v_lo;
            SELECT sr.row_name INTO v_last FROM "TM".section_rows sr
            WHERE sr.event_id = p_event_id AND sr.section_name = p_section
              AND sr.position = LEAST(v_hi, v_total - 1);
            v_band := CASE WHEN v_first = v_last THEN v_first ELSE v_first || '-' || v_last END;

            SELECT count(*) INTO v_count
            FROM "TM".seat_offers so
            JOIN "TM".seats s
              ON s.event_id = so.event_id AND s.place_id = so.place_id
            JOIN "TM".section_rows sr
              ON sr.event_id = s.event_id AND sr.section_name = s.section_name
             AND sr.row_name = s.row_name
            JOIN "TM".offers o
              ON o.event_id = so.event_id AND o.offer_id = so.offer_id
            WHERE so.event_id = p_event_id AND s.section_name = p_section
              AND so.is_available
              AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type)
              AND sr.position BETWEEN v_lo AND v_hi;

            IF v_count > 0 THEN
                RETURN QUERY
                SELECT 'band'::text, v_band, v_count,
                       round((percentile_cont(p_percentile) WITHIN GROUP (ORDER BY o.total_price))::numeric, 2),
                       round((percentile_cont(0.5)          WITHIN GROUP (ORDER BY o.total_price))::numeric, 2),
                       min(o.total_price)
                FROM "TM".seat_offers so
                JOIN "TM".seats s
                  ON s.event_id = so.event_id AND s.place_id = so.place_id
                JOIN "TM".section_rows sr
                  ON sr.event_id = s.event_id AND sr.section_name = s.section_name
                 AND sr.row_name = s.row_name
                JOIN "TM".offers o
                  ON o.event_id = so.event_id AND o.offer_id = so.offer_id
                WHERE so.event_id = p_event_id AND s.section_name = p_section
                  AND so.is_available
                  AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type)
                  AND sr.position BETWEEN v_lo AND v_hi;
                RETURN;
            END IF;
            -- band had no available comps -> fall through to whole section
        END IF;
    END IF;

    -- ---- 2) Whole-section fallback ----
    RETURN QUERY
    SELECT CASE WHEN count(*) > 0 THEN 'section' ELSE 'no_data' END,
           NULL::text, count(*)::integer,
           round((percentile_cont(p_percentile) WITHIN GROUP (ORDER BY o.total_price))::numeric, 2),
           round((percentile_cont(0.5)          WITHIN GROUP (ORDER BY o.total_price))::numeric, 2),
           min(o.total_price)
    FROM "TM".seat_offers so
    JOIN "TM".seats s
      ON s.event_id = so.event_id AND s.place_id = so.place_id
    JOIN "TM".offers o
      ON o.event_id = so.event_id AND o.offer_id = so.offer_id
    WHERE so.event_id = p_event_id AND s.section_name = p_section
      AND so.is_available
      AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type);
END;
$$;


-- Convenience: reference price * quantity = subtotal. (No margin yet.)
CREATE OR REPLACE FUNCTION "TM".quote(
    p_event_id       varchar,
    p_section        varchar,
    p_row            varchar,
    p_quantity       integer,
    p_inventory_type varchar DEFAULT 'resale',
    p_percentile     numeric DEFAULT 0.10
)
RETURNS TABLE (
    basis            text,
    band             text,
    seats_considered integer,
    price_per_seat   numeric,
    quantity         integer,
    subtotal         numeric
)
LANGUAGE sql STABLE
AS $$
    SELECT e.basis, e.band, e.seats_considered,
           e.price      AS price_per_seat,
           p_quantity   AS quantity,
           round(e.price * p_quantity, 2) AS subtotal
    FROM "TM".estimate_seat_price(p_event_id, p_section, p_row, p_inventory_type, p_percentile) e;
$$;
