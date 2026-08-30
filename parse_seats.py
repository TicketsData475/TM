#!/usr/bin/env python3
"""
Join Ticketmaster facets (availability + price) with the seat manifest to
produce a flat seat-level table: section, row, seat, inventoryType (primary /
resale), offerId, and price (if the facets call embedded offers).

Inputs (from the two network requests you captured):
  facets  : services.ticketmaster.com/.../facets?...&show=places&compress=places
  manifest: pubapi.ticketmaster.com/sdk/static/manifest/v1/<eventId>

Tip: add  &embed=offer  to the facets URL so prices come back too.
"""
import json, csv, sys


def decompress_place_ids(compressed_text):
    """Decompress TM's bracket notation into full placeIds.
    'PRE[A,Q]'          -> ['PREA', 'PREQ']
    'PRE[C[M[A,Y],NA]]' -> ['PRECMA', 'PRECMY', 'PRECNA']   (arbitrary nesting)
    """
    place_ids = []

    def parse_branch(shared_prefix, char_index):
        current_token = ''
        while char_index < len(compressed_text):
            character = compressed_text[char_index]
            if character == '[':
                # Everything read so far becomes the prefix for the branches inside [ ].
                branch_prefix = shared_prefix + current_token
                char_index += 1
                while True:
                    char_index = parse_branch(branch_prefix, char_index)
                    if char_index < len(compressed_text) and compressed_text[char_index] == ',':
                        char_index += 1
                        continue
                    break
                if char_index < len(compressed_text) and compressed_text[char_index] == ']':
                    char_index += 1
                return char_index
            elif character in ',]':
                # End of this branch: emit the finished placeId.
                place_ids.append(shared_prefix + current_token)
                return char_index
            else:
                current_token += character
                char_index += 1
        place_ids.append(shared_prefix + current_token)
        return char_index

    parse_branch('', 0)
    return place_ids


def build_seat_by_place_id(manifest):
    """placeId -> (section, row, seat), decoded from the manifest's parallel arrays.

    The manifest stores seats as flat, same-length lists (place_id_list and
    seat_number_list), plus per-section row counts. Sections and rows are laid
    out consecutively, so we walk them in order and assign each slot its
    section/row.
    """
    section_list      = manifest['manifestSections']   # [{name, numSeats}, ...]  one per section
    rows_by_section   = manifest['manifestRows']        # [[{name, numSeats}, ...], ...]  rows per section
    seat_number_list  = manifest['manifestSeats']       # ["1", "2", ...]  seat label per slot
    place_id_list     = manifest['placeIds']            # ["GEYD...", ...]  placeId per slot
    total_seats       = len(place_id_list)

    section_by_slot = [None] * total_seats
    row_by_slot     = [None] * total_seats

    slot_index = 0
    for section_index, section in enumerate(section_list):
        for row in rows_by_section[section_index]:
            for _ in range(row['numSeats']):
                section_by_slot[slot_index] = section['name']
                row_by_slot[slot_index]     = row['name']
                slot_index += 1

    if slot_index != total_seats:
        print(f"WARN: manifest filled {slot_index} slots but has {total_seats} placeIds",
              file=sys.stderr)

    return {
        place_id: (section_by_slot[slot], row_by_slot[slot], seat_number_list[slot])
        for slot, place_id in enumerate(place_id_list)
    }


def build_price_by_offer_id(facets_document):
    """offerId -> price info, from _embedded.offer (present only with embed=offer)."""
    price_by_offer_id = {}
    for offer in facets_document.get('_embedded', {}).get('offer', []):
        price_by_offer_id[offer['offerId']] = {
            'name':       offer.get('name'),
            'listPrice':  offer.get('listPrice'),
            'totalPrice': offer.get('totalPrice'),
            'faceValue':  offer.get('faceValue'),
            'currency':   offer.get('currency'),
            'priceLevel': offer.get('priceLevelSecname'),
        }
    return price_by_offer_id


def main(facets_path, manifest_path, output_csv_path='seats.csv'):
    facets_document   = json.load(open(facets_path))
    manifest          = json.load(open(manifest_path))
    seat_by_place_id  = build_seat_by_place_id(manifest)
    price_by_offer_id = build_price_by_offer_id(facets_document)

    seat_rows = []
    unmatched_place_ids = 0

    for facet in facets_document['facets']:
        inventory_type = (facet.get('inventoryTypes') or [None])[0]
        offer_id       = (facet.get('offers') or [None])[0]
        price          = price_by_offer_id.get(offer_id, {})

        for compressed_places in facet.get('places', []):
            for place_id in decompress_place_ids(compressed_places):
                seat_location = seat_by_place_id.get(place_id)
                if seat_location is None:
                    unmatched_place_ids += 1
                    continue
                section, row, seat = seat_location
                seat_rows.append({
                    'section': section, 'row': row, 'seat': seat,
                    'inventoryType': inventory_type, 'offerId': offer_id,
                    'offerName':  price.get('name'),
                    'priceLevel': price.get('priceLevel'),
                    'listPrice':  price.get('listPrice'),
                    'totalPrice': price.get('totalPrice'),
                    'placeId': place_id,
                })

    seat_rows.sort(key=lambda seat: (
        seat['section'], seat['row'],
        int(seat['seat']) if str(seat['seat']).isdigit() else 0,
    ))

    with open(output_csv_path, 'w', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(seat_rows[0].keys()))
        writer.writeheader()
        writer.writerows(seat_rows)

    primary_count = sum(1 for seat in seat_rows if seat['inventoryType'] == 'primary')
    resale_count  = sum(1 for seat in seat_rows if seat['inventoryType'] == 'resale')
    print(f"wrote {output_csv_path}: {len(seat_rows)} seats  "
          f"(primary={primary_count}, resale={resale_count})"
          + (f", {unmatched_place_ids} placeIds not in manifest" if unmatched_place_ids else ""))
    if not price_by_offer_id:
        print("NOTE: no prices found -> re-fetch facets with &embed=offer to get prices.")
    return seat_rows


if __name__ == '__main__':
    facets_path   = sys.argv[1] if len(sys.argv) > 1 else 'another_facet_api.json'
    manifest_path = sys.argv[2] if len(sys.argv) > 2 else 'manifest.json'
    output_path   = sys.argv[3] if len(sys.argv) > 3 else 'seats.csv'
    main(facets_path, manifest_path, output_path)
