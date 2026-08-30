#!/usr/bin/env bash
# Fetch section/seat-level availability WITH prices (embed=offer) for a TM event.
# Run from anywhere:  bash /home/linked/arslan/scrapping/TM/fetch_facets.sh
set -euo pipefail

OUT="/home/linked/arslan/scrapping/TM/another_facet_api.json"

# If your session expires (401/invalid), grab a fresh 'c-tmpt' header value from
# DevTools -> Network -> the facets request -> Request Headers, and paste it here:
CTMPT='1:CAESGPwyPdlXtt8rVhi13tSXCOoOcSHLNYivgxiig6jUBiIvbG-8ISzu5BRPtj4x9f39yS4zCuMi5svtUiDR90KWchcTAXDzRZjZvs-3DtsmMqU'

URL='https://services.ticketmaster.com/api/ismds/event/0D00645AC812FBDB/facets?by=section+seating+attributes+available+accessibility+offer+placeGroups+inventoryType+offerType+area+description&show=places&embed=area&embed=description&embed=offer&q=available&compress=places&resaleChannelId=internal.ecommerce.consumer.desktop.web.browser.ticketmaster.us&apikey=b462oi7fic6pehcdkzony5bxhe&apisecret=pquzpfrfz7zd2ylvtz3w5dtyse'

curl --compressed -s "$URL" \
  -H "c-tmpt: $CTMPT" \
  -H 'Referer: https://www.ticketmaster.com/' \
  -H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36' \
  -H 'TMPS-Correlation-Id: 09d81e58-8a4b-4501-8057-23d05dd3a825' \
  -o "$OUT" \
  -w 'HTTP %{http_code}  size=%{size_download} bytes -> '"$OUT"'\n'

echo "--- quick check ---"
python3 -c "import json; d=json.load(open('$OUT')); print('facets:', len(d.get('facets',[]))); print('offers embedded:', len(d.get('_embedded',{}).get('offer',[])))" \
  || { echo 'Response was not valid JSON (likely an auth error). First 300 chars:'; head -c 300 "$OUT"; echo; }
