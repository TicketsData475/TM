-- =====================================================================
-- Pricing functions for the quote tool (schema "TM").
-- Run once:  psql "$DATABASE_URL" -f pricing_functions.sql
--
-- estimate_seat_price(event, section, [row], [inventory_type])
--   -> per-seat price stats + which basis was used.
-- quote(event, section, [row], quantity, [inventory_type])
--   -> per-seat price * quantity = subtotal.
--
-- Price basis:
--   'row'            -> averaged the available seats in that section+row
--   'section'        -> row missing/absent -> averaged the whole section
--   'ga_unsupported' -> GA section: no per-seat price stored yet (see
--                       future_improvements.md #3). Returns NULL price.
--   'no_data'        -> section has no available seats right now.
--
-- inventory_type: NULL = all seats; 'resale' or 'primary' to restrict.
--                 For a BUYBACK comp, 'resale' is usually the right market.
-- Prices use available seats only (is_available = true).
-- =====================================================================

CREATE OR REPLACE FUNCTION "TM".estimate_seat_price(
    p_event_id       varchar,
    p_section        varchar,
    p_row            varchar DEFAULT NULL,
    p_inventory_type varchar DEFAULT NULL
)
RETURNS TABLE (
    basis            text,
    seats_considered integer,
    avg_list_price   numeric,
    avg_total_price  numeric,
    min_list_price   numeric,
    max_list_price   numeric
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_is_ga     boolean;
    v_row_count integer;
BEGIN
    -- GA sections have no per-seat data in this schema yet.
    SELECT (s.seating_type = 'general') INTO v_is_ga
    FROM "TM".sections s
    WHERE s.event_id = p_event_id AND s.name = p_section;

    IF v_is_ga THEN
        RETURN QUERY SELECT 'ga_unsupported'::text, 0,
               NULL::numeric, NULL::numeric, NULL::numeric, NULL::numeric;
        RETURN;
    END IF;

    -- 1) If a row is supplied AND we actually have available seats there -> price the row.
    IF p_row IS NOT NULL THEN
        SELECT count(*) INTO v_row_count
        FROM "TM".seat_offers so
        JOIN "TM".seats s
          ON s.event_id = so.event_id AND s.place_id = so.place_id
        JOIN "TM".offers o
          ON o.event_id = so.event_id AND o.offer_id = so.offer_id
        WHERE so.event_id = p_event_id
          AND s.section_name = p_section
          AND s.row_name = p_row
          AND so.is_available
          AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type);

        IF v_row_count > 0 THEN
            RETURN QUERY
            SELECT 'row'::text, v_row_count,
                   round(avg(o.list_price), 2), round(avg(o.total_price), 2),
                   min(o.list_price), max(o.list_price)
            FROM "TM".seat_offers so
            JOIN "TM".seats s
              ON s.event_id = so.event_id AND s.place_id = so.place_id
            JOIN "TM".offers o
              ON o.event_id = so.event_id AND o.offer_id = so.offer_id
            WHERE so.event_id = p_event_id
              AND s.section_name = p_section
              AND s.row_name = p_row
              AND so.is_available
              AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type);
            RETURN;
        END IF;
    END IF;

    -- 2) Otherwise average the whole section.
    RETURN QUERY
    SELECT CASE WHEN count(*) > 0 THEN 'section' ELSE 'no_data' END,
           count(*)::integer,
           round(avg(o.list_price), 2), round(avg(o.total_price), 2),
           min(o.list_price), max(o.list_price)
    FROM "TM".seat_offers so
    JOIN "TM".seats s
      ON s.event_id = so.event_id AND s.place_id = so.place_id
    JOIN "TM".offers o
      ON o.event_id = so.event_id AND o.offer_id = so.offer_id
    WHERE so.event_id = p_event_id
      AND s.section_name = p_section
      AND so.is_available
      AND (p_inventory_type IS NULL OR o.inventory_type = p_inventory_type);
END;
$$;


-- Convenience: per-seat price * quantity = subtotal.
-- Uses total_price (price incl. fees = the real market number), not list_price/face value.
CREATE OR REPLACE FUNCTION "TM".quote(
    p_event_id       varchar,
    p_section        varchar,
    p_row            varchar,
    p_quantity       integer,
    p_inventory_type varchar DEFAULT NULL
)
RETURNS TABLE (
    basis            text,
    seats_considered integer,
    price_per_seat   numeric,
    quantity         integer,
    subtotal         numeric
)
LANGUAGE sql STABLE
AS $$
    SELECT e.basis, e.seats_considered,
           e.avg_total_price AS price_per_seat,
           p_quantity         AS quantity,
           round(e.avg_total_price * p_quantity, 2) AS subtotal
    FROM "TM".estimate_seat_price(p_event_id, p_section, p_row, p_inventory_type) e;
$$;
