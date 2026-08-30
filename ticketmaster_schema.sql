-- =====================================================================
-- Ticketmaster scraper schema  (schema: "TM")
-- Copy-paste runnable; safe to run more than once.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS "TM";

CREATE TABLE IF NOT EXISTS "TM"."venues" (
  "id" varchar PRIMARY KEY,
  "name" varchar,
  "city" varchar,
  "state" varchar,
  "country" varchar,
  "country_code" varchar,
  "postal_code" varchar,
  "address_line_one" varchar,
  "latitude" decimal(10,7),
  "longitude" decimal(10,7),
  "url" varchar,
  "image_url" varchar,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp
);

CREATE TABLE IF NOT EXISTS "TM"."events" (
  "id" varchar PRIMARY KEY,
  "discovery_id" varchar,
  "title" varchar NOT NULL,
  "venue_id" varchar NOT NULL,
  "start_date" timestamp,
  "end_date" timestamp,
  "onsale_date" timestamp,
  "date_display" varchar,
  "is_span_multiple_days" boolean,
  "url" varchar,
  "upsell_landing_page_url" varchar,
  "seatmap_url" varchar,
  "is_partner_event" boolean,
  "is_partner" boolean,
  "is_tm_button_shown" boolean,
  "timezone" varchar,
  "is_cancelled" boolean,
  "is_postponed" boolean,
  "is_rescheduled" boolean,
  "is_tba" boolean,
  "is_local" boolean,
  "is_same_region" boolean,
  "is_sold_out" boolean,
  "is_limited_availability" boolean,
  "is_virtual" boolean,
  "event_change_status" varchar,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp
);

CREATE TABLE IF NOT EXISTS "TM"."artists" (
  "name" varchar PRIMARY KEY NOT NULL,
  "url" varchar,
  "image_url" varchar,
  "image_url_retina" varchar,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp
);

CREATE TABLE IF NOT EXISTS "TM"."event_artists" (
  "event_id" varchar NOT NULL,
  "artist_id" varchar NOT NULL,
  "created_at" timestamp,
  "last_seen_at" timestamp
);

CREATE TABLE IF NOT EXISTS "TM"."sections" (
  "event_id" varchar NOT NULL,
  "name" varchar NOT NULL,
  "seating_type" varchar,
  "num_seats" int,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp,
  PRIMARY KEY ("event_id", "name")
);

CREATE TABLE IF NOT EXISTS "TM"."seats" (
  "event_id" varchar NOT NULL,
  "place_id" varchar NOT NULL,
  "section_name" varchar NOT NULL,
  "row_name" varchar,
  "seat_number" varchar,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp,
  PRIMARY KEY ("event_id", "place_id")
);

CREATE TABLE IF NOT EXISTS "TM"."offers" (
  "event_id" varchar NOT NULL,
  "offer_id" varchar NOT NULL,
  "inventory_type" varchar NOT NULL,
  "offer_type" varchar,
  "name" varchar,
  "description" varchar,
  "price_level_id" varchar,
  "price_level_name" varchar,
  "face_value" decimal(10,2),
  "list_price" decimal(10,2),
  "total_price" decimal(10,2),
  "no_charges_price" decimal(10,2),
  "sellable_quantities" varchar,
  "rank" int,
  "is_online" boolean,
  "ticket_type_id" varchar,
  "listing_id" varchar,
  "listing_version_id" varchar,
  "seller_notes" varchar,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp,
  PRIMARY KEY ("event_id", "offer_id")
);

CREATE TABLE IF NOT EXISTS "TM"."seat_offers" (
  "event_id" varchar NOT NULL,
  "place_id" varchar NOT NULL,
  "offer_id" varchar NOT NULL,
  "is_available" boolean DEFAULT true,
  "captured_at" timestamp,
  "last_seen_at" timestamp,
  PRIMARY KEY ("event_id", "place_id")
);

CREATE INDEX IF NOT EXISTS "idx_events_discovery_id" ON "TM"."events" ("discovery_id");

CREATE INDEX IF NOT EXISTS "idx_events_venue_id" ON "TM"."events" ("venue_id");

CREATE INDEX IF NOT EXISTS "idx_events_start_date" ON "TM"."events" ("start_date");

CREATE UNIQUE INDEX IF NOT EXISTS "uq_event_artists" ON "TM"."event_artists" ("event_id", "artist_id");

CREATE INDEX IF NOT EXISTS "idx_seats_event_section" ON "TM"."seats" ("event_id", "section_name");

CREATE INDEX IF NOT EXISTS "idx_seat_offers_event_offer" ON "TM"."seat_offers" ("event_id", "offer_id");

COMMENT ON COLUMN "TM"."venues"."id" IS 'events -> venue -> code (Ticketmaster''s own id, not incremental)';

COMMENT ON COLUMN "TM"."venues"."last_seen_at" IS 'last scrape run this venue appeared in';

COMMENT ON COLUMN "TM"."events"."id" IS 'events -> id';

COMMENT ON COLUMN "TM"."events"."discovery_id" IS 'events -> discoveryId';

COMMENT ON COLUMN "TM"."events"."title" IS 'events -> title';

COMMENT ON COLUMN "TM"."events"."venue_id" IS 'events -> venue -> code';

COMMENT ON COLUMN "TM"."events"."start_date" IS 'events -> dates -> startDate';

COMMENT ON COLUMN "TM"."events"."end_date" IS 'events -> dates -> endDate';

COMMENT ON COLUMN "TM"."events"."onsale_date" IS 'events -> dates -> onsaleDate';

COMMENT ON COLUMN "TM"."events"."date_display" IS 'events -> dates -> dateDisplay';

COMMENT ON COLUMN "TM"."events"."is_span_multiple_days" IS 'events -> dates -> spanMultipleDays';

COMMENT ON COLUMN "TM"."events"."url" IS 'events -> url';

COMMENT ON COLUMN "TM"."events"."upsell_landing_page_url" IS 'events -> upsellLandingPageUrl';

COMMENT ON COLUMN "TM"."events"."seatmap_url" IS 'events -> seatmapUrl';

COMMENT ON COLUMN "TM"."events"."is_partner_event" IS 'events -> partnerEvent';

COMMENT ON COLUMN "TM"."events"."is_partner" IS 'events -> isPartner';

COMMENT ON COLUMN "TM"."events"."is_tm_button_shown" IS 'events -> showTmButton';

COMMENT ON COLUMN "TM"."events"."timezone" IS 'events -> timeZone';

COMMENT ON COLUMN "TM"."events"."is_cancelled" IS 'events -> cancelled';

COMMENT ON COLUMN "TM"."events"."is_postponed" IS 'events -> postponed';

COMMENT ON COLUMN "TM"."events"."is_rescheduled" IS 'events -> rescheduled';

COMMENT ON COLUMN "TM"."events"."is_tba" IS 'events -> tba';

COMMENT ON COLUMN "TM"."events"."is_local" IS 'events -> local';

COMMENT ON COLUMN "TM"."events"."is_same_region" IS 'events -> sameRegion';

COMMENT ON COLUMN "TM"."events"."is_sold_out" IS 'events -> soldOut';

COMMENT ON COLUMN "TM"."events"."is_limited_availability" IS 'events -> limitedAvailability';

COMMENT ON COLUMN "TM"."events"."is_virtual" IS 'events -> virtual';

COMMENT ON COLUMN "TM"."events"."event_change_status" IS 'events -> eventChangeStatus';

COMMENT ON COLUMN "TM"."events"."last_seen_at" IS 'last scrape run this event appeared in; if stale -> dropped from feed';

COMMENT ON COLUMN "TM"."artists"."name" IS 'events -> artists -> name';

COMMENT ON COLUMN "TM"."artists"."url" IS 'events -> artists -> url';

COMMENT ON COLUMN "TM"."artists"."image_url" IS 'events -> artists -> imageUrls -> ARTIST_PAGE_3_2';

COMMENT ON COLUMN "TM"."artists"."image_url_retina" IS 'events -> artists -> imageUrls -> RETINA_PORTRAIT_16_9';

COMMENT ON COLUMN "TM"."artists"."last_seen_at" IS 'last scrape run this artist appeared in';

COMMENT ON COLUMN "TM"."event_artists"."last_seen_at" IS 'last scrape run this event<->artist link appeared in';

COMMENT ON COLUMN "TM"."sections"."event_id" IS 'one section set per event (manifest is per-event)';

COMMENT ON COLUMN "TM"."sections"."name" IS 'manifest -> manifestSections[] -> name (e.g. ''319'')';

COMMENT ON COLUMN "TM"."sections"."seating_type" IS 'facets -> facet -> seating (''reserved'' | ''general'')';

COMMENT ON COLUMN "TM"."sections"."num_seats" IS 'manifest -> manifestSections[] -> numSeats (capacity, sold + open)';

COMMENT ON COLUMN "TM"."sections"."last_seen_at" IS 'last scrape run this section appeared in';

COMMENT ON COLUMN "TM"."seats"."place_id" IS 'manifest -> placeIds[] ; JOIN key to facets.places. Unique only within an event';

COMMENT ON COLUMN "TM"."seats"."section_name" IS 'FK part -> sections.name';

COMMENT ON COLUMN "TM"."seats"."row_name" IS 'manifest -> manifestRows[section][] -> name';

COMMENT ON COLUMN "TM"."seats"."seat_number" IS 'manifest -> manifestSeats[] (parallel to placeIds)';

COMMENT ON COLUMN "TM"."seats"."last_seen_at" IS 'last scrape run this seat appeared in the manifest';

COMMENT ON COLUMN "TM"."offers"."offer_id" IS 'facets -> _embedded.offer[] -> offerId (e.g. ''GJ6DC7BXGA'')';

COMMENT ON COLUMN "TM"."offers"."inventory_type" IS 'offer -> inventoryType (''primary'' | ''resale'')';

COMMENT ON COLUMN "TM"."offers"."offer_type" IS 'offer -> offerType (''standard'')';

COMMENT ON COLUMN "TM"."offers"."name" IS 'offer -> name (e.g. ''Verified Buccaneers Ticket''); null for most resale';

COMMENT ON COLUMN "TM"."offers"."description" IS 'offer -> description';

COMMENT ON COLUMN "TM"."offers"."price_level_id" IS 'offer -> priceLevelId (primary only)';

COMMENT ON COLUMN "TM"."offers"."price_level_name" IS 'offer -> priceLevelSecname (e.g. ''P23''; primary only)';

COMMENT ON COLUMN "TM"."offers"."face_value" IS 'offer -> faceValue';

COMMENT ON COLUMN "TM"."offers"."list_price" IS 'offer -> listPrice (before fees)';

COMMENT ON COLUMN "TM"."offers"."total_price" IS 'offer -> totalPrice (incl. fees)';

COMMENT ON COLUMN "TM"."offers"."no_charges_price" IS 'offer -> noChargesPrice';

COMMENT ON COLUMN "TM"."offers"."sellable_quantities" IS 'offer -> sellableQuantities[] as CSV/JSON e.g. ''1,2,3,4,5,6''';

COMMENT ON COLUMN "TM"."offers"."rank" IS 'offer -> rank';

COMMENT ON COLUMN "TM"."offers"."is_online" IS 'offer -> online';

COMMENT ON COLUMN "TM"."offers"."ticket_type_id" IS 'offer -> ticketTypeId';

COMMENT ON COLUMN "TM"."offers"."listing_id" IS 'offer -> listingId (resale only)';

COMMENT ON COLUMN "TM"."offers"."listing_version_id" IS 'offer -> listingVersionId (resale only)';

COMMENT ON COLUMN "TM"."offers"."seller_notes" IS 'offer -> sellerNotes (resale only)';

COMMENT ON COLUMN "TM"."offers"."last_seen_at" IS 'last scrape run this offer was still for sale; if stale -> gone/sold';

COMMENT ON COLUMN "TM"."seat_offers"."place_id" IS 'FK part -> seats.place_id';

COMMENT ON COLUMN "TM"."seat_offers"."offer_id" IS 'FK part -> offers.offer_id';

COMMENT ON COLUMN "TM"."seat_offers"."is_available" IS 'facets fetched with q=available';

COMMENT ON COLUMN "TM"."seat_offers"."captured_at" IS 'when this availability was scraped';

COMMENT ON COLUMN "TM"."seat_offers"."last_seen_at" IS 'last scrape run this seat was still available; if stale -> sold/removed';

ALTER TABLE "TM"."events" ADD FOREIGN KEY ("venue_id") REFERENCES "TM"."venues" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."event_artists" ADD FOREIGN KEY ("event_id") REFERENCES "TM"."events" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."event_artists" ADD FOREIGN KEY ("artist_id") REFERENCES "TM"."artists" ("name") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."sections" ADD FOREIGN KEY ("event_id") REFERENCES "TM"."events" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."seats" ADD FOREIGN KEY ("event_id", "section_name") REFERENCES "TM"."sections" ("event_id", "name") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."offers" ADD FOREIGN KEY ("event_id") REFERENCES "TM"."events" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."seat_offers" ADD FOREIGN KEY ("event_id", "place_id") REFERENCES "TM"."seats" ("event_id", "place_id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "TM"."seat_offers" ADD FOREIGN KEY ("event_id", "offer_id") REFERENCES "TM"."offers" ("event_id", "offer_id") DEFERRABLE INITIALLY IMMEDIATE;
