"""Adapter for HOST-system events (e.g. Australian Open) sold on a .com.au/.com
brand site instead of ismds.

Their data comes from two per-brand endpoints + the (identical) manifest:
  - seatmapoffered -> offers (primary price levels + resale listings)
  - quickpicks     -> available seats (section, row, seat #s, price, offerIds)
  - manifest       -> seat structure + rank (same format as everywhere else)

`to_facets_doc()` reshapes these into exactly the ismds "facets" document that
parse.build_seating_rows already understands -- so the DB upserts and the pricing
functions need NO changes; this is a pure addition.
"""
import parse


def _offer_row(o):
    """A seatmapoffered offer -> the _embedded.offer shape build_offer_row reads."""
    price = o.get("originalPrice")
    fees = sum((c.get("originalFee") or 0) for c in (o.get("charges") or []))
    return {
        "offerId": o["offerId"],
        "name": o.get("name"),
        "description": o.get("description"),
        "inventoryType": o.get("inventoryType"),
        "offerType": o.get("offerType"),
        "priceLevelId": o.get("priceLevelId"),
        "priceLevelSecname": None,
        "faceValue": price,                                   # before fees
        "listPrice": price,
        "totalPrice": round((price or 0) + fees, 2) if price is not None else None,
        "noChargesPrice": price,
        "ticketTypeId": o.get("ticketTypeId"),
        "rank": o.get("rank"),
        "online": True,
        "listingId": o.get("resaleListingId"),                # resale only
    }


def to_facets_doc(manifest, picks, offered_offers):
    """Build an ismds-shaped facets doc from quickpicks + seatmapoffered + manifest.

    Available seats come from `picks`; each is matched to its manifest place id by
    (section, row, seat number) so the existing parser picks up the rank. Each seat
    is attributed to the offer whose price matches the pick's displayed price."""
    place_to_seat, _, _ = parse.decode_manifest(manifest)
    place_by_seat = {(str(sec), str(row), str(seat)): pid
                     for pid, (sec, row, seat) in place_to_seat.items()}
    offers_by_id = {o["offerId"]: o for o in offered_offers}

    groups = {}            # (section, offer_id) -> [place_id, ...]
    for p in picks:
        section, row = str(p.get("section")), str(p.get("row"))
        try:
            lo, hi = int(p["seatFrom"]), int(p["seatTo"])
        except (TypeError, ValueError, KeyError):
            continue
        offer_ids = p.get("offerIds") or []
        # the offer whose price == the pick's shown price (else the first offer)
        chosen = next((oid for oid in offer_ids
                       if (offers_by_id.get(oid) or {}).get("originalPrice") == p.get("originalPrice")),
                      offer_ids[0] if offer_ids else None)
        if not chosen:
            continue
        for n in range(lo, hi + 1):
            pid = place_by_seat.get((section, row, str(n)))
            if pid:
                groups.setdefault((section, chosen), []).append(pid)

    facets, used = [], set()
    for (section, offer_id), places in groups.items():
        used.add(offer_id)
        inv = (offers_by_id.get(offer_id) or {}).get("inventoryType")
        facets.append({
            "section": section, "seating": "reserved", "available": True,
            "inventoryTypes": [inv] if inv else [],
            "offerTypes": ["standard"], "offers": [offer_id],
            "places": places, "count": len(places),
            "areas": [], "attributes": [], "accessibility": [], "placeGroups": [],
        })

    embedded = [_offer_row(offers_by_id[oid]) for oid in used if oid in offers_by_id]
    return {"facets": facets, "_embedded": {"offer": embedded}}
