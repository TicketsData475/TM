"""Transform raw API JSON into DB-ready row dicts.

Three inputs feed the tables:
  - search event  -> venues, events, artists, event_artists
  - facets + manifest (per event) -> sections, seats, offers, seat_offers
"""


# --------------------------------------------------------------------------
# facets `places` are compressed with a nested bracket notation.
# --------------------------------------------------------------------------
def decompress_place_ids(compressed_text):
    """'PRE[A,Q]' -> ['PREA','PREQ'] ; nesting like 'PRE[C[M[A,Y],NA]]' supported."""
    place_ids = []

    def parse_branch(shared_prefix, i):
        token = ''
        while i < len(compressed_text):
            ch = compressed_text[i]
            if ch == '[':
                branch_prefix = shared_prefix + token
                i += 1
                while True:
                    i = parse_branch(branch_prefix, i)
                    if i < len(compressed_text) and compressed_text[i] == ',':
                        i += 1
                        continue
                    break
                if i < len(compressed_text) and compressed_text[i] == ']':
                    i += 1
                return i
            elif ch in ',]':
                place_ids.append(shared_prefix + token)
                return i
            else:
                token += ch
                i += 1
        place_ids.append(shared_prefix + token)
        return i

    parse_branch('', 0)
    return place_ids


# --------------------------------------------------------------------------
# manifest = parallel arrays; expand section/row counts to per-seat identity.
# --------------------------------------------------------------------------
def decode_manifest(manifest):
    """Returns (place_to_seat: {placeId: (section, row, seat)}, sections_info list).

    Only NON-GA (reserved) sections have entries in `manifestRows` and consume
    placeIds/seat numbers; GA sections carry `ga: true`, no rows, and no
    individual places. `manifestRows` is ordered over the reserved sections only.
    Pure-GA / parking events may have no placeIds at all.
    """
    section_list = manifest.get('manifestSections') or []
    rows_by_section = manifest.get('manifestRows') or []
    seat_numbers = manifest.get('manifestSeats') or []
    place_ids = manifest.get('placeIds') or []

    place_to_seat = {}
    sections_info = []
    slot = 0
    row_group_index = 0
    for section in section_list:
        name = section['name']
        is_ga = bool(section.get('ga'))
        sections_info.append({'name': name, 'num_seats': section.get('numSeats'), 'ga': is_ga})
        if is_ga or row_group_index >= len(rows_by_section):
            continue
        for row in rows_by_section[row_group_index]:
            row_name = row['name']
            for _ in range(row['numSeats']):
                if slot < len(place_ids):
                    place_to_seat[place_ids[slot]] = (
                        name, row_name,
                        seat_numbers[slot] if slot < len(seat_numbers) else None)
                slot += 1
        row_group_index += 1

    return place_to_seat, sections_info


def build_offer_row(event_id, offer_id, offer, fallback_inventory_type):
    sellable = offer.get('sellableQuantities')
    return {
        'event_id': event_id,
        'offer_id': offer_id,
        'inventory_type': offer.get('inventoryType') or fallback_inventory_type,
        'offer_type': offer.get('offerType'),
        'name': offer.get('name'),
        'description': offer.get('description'),
        'price_level_id': offer.get('priceLevelId'),
        'price_level_name': offer.get('priceLevelSecname'),
        'face_value': offer.get('faceValue'),
        'list_price': offer.get('listPrice'),
        'total_price': offer.get('totalPrice'),
        'no_charges_price': offer.get('noChargesPrice'),
        'sellable_quantities': ','.join(map(str, sellable)) if isinstance(sellable, list) else None,
        'rank': offer.get('rank'),
        'is_online': offer.get('online'),
        'ticket_type_id': offer.get('ticketTypeId'),
        'listing_id': offer.get('listingId'),
        'listing_version_id': offer.get('listingVersionId'),
        'seller_notes': offer.get('sellerNotes'),
    }


def build_seating_rows(event_id, facets_doc, manifest, seat_mode='available'):
    """Produce sections / seats / offers / seat_offers rows for one event."""
    place_to_seat, sections_info = decode_manifest(manifest)
    offers_by_id = {o['offerId']: o for o in facets_doc.get('_embedded', {}).get('offer', [])}

    seating_type_by_section = {}
    offers_rows = {}                 # offer_id -> row
    seats_rows = {}                  # place_id -> row  (available seats)
    seat_offers_rows = {}            # place_id -> row  (dedupe: one offer per seat)

    for facet in facets_doc.get('facets', []):
        section = facet.get('section')
        seating = facet.get('seating')
        inv = (facet.get('inventoryTypes') or [None])[0]
        offer_id = (facet.get('offers') or [None])[0]

        if section and seating:
            seating_type_by_section.setdefault(section, seating)

        if offer_id and offer_id not in offers_rows:
            offers_rows[offer_id] = build_offer_row(
                event_id, offer_id, offers_by_id.get(offer_id, {}), inv)

        for compressed in facet.get('places', []):
            for place_id in decompress_place_ids(compressed):
                loc = place_to_seat.get(place_id)
                if loc is None:
                    continue
                sec, row, seat = loc
                seats_rows.setdefault(place_id, {
                    'event_id': event_id, 'place_id': place_id,
                    'section_name': sec, 'row_name': row, 'seat_number': seat,
                })
                seat_offers_rows.setdefault(place_id, {
                    'event_id': event_id, 'place_id': place_id,
                    'offer_id': offer_id, 'is_available': True,
                })

    # sections: keep ALL manifest sections (small, gives full section list + capacity)
    # seating_type from facets when present, else 'general' for GA sections.
    sections_rows = [
        {'event_id': event_id, 'name': s['name'],
         'seating_type': seating_type_by_section.get(s['name'])
                         or ('general' if s['ga'] else None),
         'num_seats': s['num_seats']}
        for s in sections_info
    ]

    # seats: full manifest, or only the available ones
    if seat_mode == 'full':
        seats_rows = {
            pid: {'event_id': event_id, 'place_id': pid,
                  'section_name': sec, 'row_name': row, 'seat_number': seat}
            for pid, (sec, row, seat) in place_to_seat.items()
        }

    return {
        'sections': sections_rows,
        'seats': list(seats_rows.values()),
        'offers': list(offers_rows.values()),
        'seat_offers': list(seat_offers_rows.values()),
    }


# --------------------------------------------------------------------------
# search results -> event / venue / artist rows
# --------------------------------------------------------------------------
def extract_event_entities(ev):
    """Returns (venue_row, event_row, [artist_rows], [event_artist_rows])."""
    v = ev.get('venue', {}) or {}
    venue = {
        'id': v.get('code') or None,   # varchar: TM codes aren't always numeric
        'name': v.get('name'), 'city': v.get('city'), 'state': v.get('state'),
        'country': v.get('countryName') or v.get('country'),
        'country_code': v.get('countryCode'),
        'postal_code': v.get('postalCode'),
        'address_line_one': v.get('addressLineOne'),
        'latitude': v.get('latitude'), 'longitude': v.get('longitude'),
        'url': v.get('url'), 'image_url': v.get('imageUrl'),
    }
    d = ev.get('dates', {}) or {}
    event = {
        'id': ev['id'], 'discovery_id': ev.get('discoveryId'), 'title': ev.get('title'),
        'venue_id': venue['id'],
        'start_date': d.get('startDate'), 'end_date': d.get('endDate'),
        'onsale_date': d.get('onsaleDate'), 'date_display': d.get('dateDisplay'),
        'is_span_multiple_days': d.get('spanMultipleDays'),
        'url': ev.get('url'), 'upsell_landing_page_url': ev.get('upsellLandingPageUrl'),
        'seatmap_url': ev.get('seatmapUrl'),
        'is_partner_event': ev.get('partnerEvent'), 'is_partner': ev.get('isPartner'),
        'is_tm_button_shown': ev.get('showTmButton'), 'timezone': ev.get('timeZone'),
        'is_cancelled': ev.get('cancelled'), 'is_postponed': ev.get('postponed'),
        'is_rescheduled': ev.get('rescheduled'), 'is_tba': ev.get('tba'),
        'is_local': ev.get('local'), 'is_same_region': ev.get('sameRegion'),
        'is_sold_out': ev.get('soldOut'),
        'is_limited_availability': ev.get('limitedAvailability'),
        'is_virtual': ev.get('virtual'),
        'event_change_status': ev.get('eventChangeStatus'),
    }
    artists, event_artists = [], []
    for a in ev.get('artists', []) or []:
        name = a.get('name')
        if not name:
            continue
        imgs = a.get('imageUrls', {}) or {}
        artists.append({
            'name': name, 'url': a.get('url'),
            'image_url': imgs.get('ARTIST_PAGE_3_2'),
            'image_url_retina': imgs.get('RETINA_PORTRAIT_16_9'),
        })
        event_artists.append({'event_id': ev['id'], 'artist_id': name})
    return venue, event, artists, event_artists
