"use client";
import { useEffect, useRef, useState } from "react";

/* ============================ helpers ============================ */
function useDebounced(value, delay = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}
function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleDateString("en-US", {
    weekday: "long", month: "short", day: "numeric", year: "numeric",
  }) + " • " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}
function dateParts(ts) {
  const d = new Date(ts);
  return {
    m: d.toLocaleDateString("en-US", { month: "short" }),
    d: d.getDate(),
    y: d.getFullYear(),
  };
}
function venueLine(e) {
  return [e.venue_name, e.city, e.state].filter(Boolean).join(", ");
}
function money(n) {
  if (n === null || n === undefined) return "—";
  return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* recommended defaults (tennis / Australian Open) */
const DEF_INVENTORY = "primary";
const DEF_NUMS = { percentile: "0", rankWindow: "200", maxSections: "7", margin: "30" };

const INVENTORY_LABEL = { primary: "primary (box-office)", resale: "resale", all: "available" };

/* ===================== searchable dropdown ====================== */
function SearchableSelect({ label, required, placeholder, options, value, onChange, disabled }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const ref = useRef(null);
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  const list = options.filter((o) => o.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div className="field">
      <label>{label} {required && <span className="req">*</span>}</label>
      <div className="select" ref={ref}>
        <button
          type="button" className="selectBtn" disabled={disabled}
          onClick={() => setOpen((o) => !o)}
        >
          {value || <span style={{ color: "#94a3b8" }}>{placeholder}</span>}
        </button>
        {open && !disabled && (
          <div className="selectMenu">
            <input
              className="selectSearch" autoFocus placeholder="Search..."
              value={filter} onChange={(e) => setFilter(e.target.value)}
            />
            {list.length === 0 && <div className="empty">No matches</div>}
            {list.map((o, i) => (
              <div
                key={i} className={"opt" + (o === value ? " sel" : "")}
                onClick={() => { onChange(o); setOpen(false); setFilter(""); }}
              >
                {o === value ? "✓ " : ""}{o}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* a single advanced numeric control with a help line underneath */
function NumControl({ label, unit, value, onChange, min, max, step, help }) {
  return (
    <div className="ctrl">
      <label>{label}</label>
      <div className="ctrlInput">
        <input
          type="number" min={min} max={max} step={step || 1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {unit && <span className="unit">{unit}</span>}
      </div>
      <div className="help">{help}</div>
    </div>
  );
}

/* ================ event row (dropdown + list) =================== */
function EventRow({ e, onClick }) {
  const dp = dateParts(e.start_date);
  return (
    <div className="rowItem" onClick={onClick}>
      <div className="dateBox">
        <div className="m">{dp.m}</div>
        <div className="d">{dp.d}</div>
        <div className="y">{dp.y}</div>
      </div>
      <div>
        <div className="evTitle">{e.title}</div>
        <div className="evMeta">{fmtDate(e.start_date)} • {venueLine(e)}</div>
      </div>
    </div>
  );
}

export default function AceifyHome() {
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query, 300);

  const [suggestions, setSuggestions] = useState([]);
  const [showDrop, setShowDrop] = useState(false);
  const [results, setResults] = useState(null);

  const [selected, setSelected] = useState(null);
  const [sections, setSections] = useState([]);
  const [section, setSection] = useState("");
  const [rows, setRows] = useState([]);
  const [row, setRow] = useState("");
  const [loadingRows, setLoadingRows] = useState(false);

  const [quantity, setQuantity] = useState("1");
  const debQty = useDebounced(quantity, 250);

  /* overridable pricing controls */
  const [inventory, setInventory] = useState(DEF_INVENTORY);   // 'primary' | 'resale' | 'all'
  const [nums, setNums] = useState(DEF_NUMS);                  // percentile, rankWindow, maxSections, margin (strings)
  const [advOpen, setAdvOpen] = useState(false);
  const debNums = useDebounced(JSON.stringify(nums), 300);

  const [quote, setQuote] = useState(null);

  const isDefaults =
    inventory === DEF_INVENTORY &&
    nums.percentile === DEF_NUMS.percentile &&
    nums.rankWindow === DEF_NUMS.rankWindow &&
    nums.maxSections === DEF_NUMS.maxSections &&
    nums.margin === DEF_NUMS.margin;

  function setNum(key, v) { setNums((n) => ({ ...n, [key]: v })); }
  function resetControls() { setInventory(DEF_INVENTORY); setNums(DEF_NUMS); }

  /* live autocomplete (aceify = tennis only) */
  useEffect(() => {
    if (selected) return;
    if (debounced.trim().length < 2) { setSuggestions([]); return; }
    let alive = true;
    fetch(`/api/aceify/search?q=${encodeURIComponent(debounced)}&limit=6`)
      .then((r) => r.json())
      .then((d) => { if (alive) { setSuggestions(Array.isArray(d) ? d : []); setShowDrop(true); } })
      .catch(() => {});
    return () => { alive = false; };
  }, [debounced, selected]);

  async function runFullSearch() {
    if (query.trim().length < 2) return;
    setShowDrop(false);
    setSelected(null);
    const d = await fetch(`/api/aceify/search?q=${encodeURIComponent(query)}&limit=20`).then((r) => r.json());
    setResults(Array.isArray(d) ? d : []);
  }

  async function pickEvent(e) {
    setSelected(e);
    setShowDrop(false);
    setResults(null);
    setSection(""); setRow(""); setRows([]); setSections([]);
    const s = await fetch(`/api/aceify/sections?eventId=${encodeURIComponent(e.event_id)}`).then((r) => r.json());
    setSections(Array.isArray(s) ? s : []);
  }

  async function pickSection(name) {
    setSection(name);
    setRow("");
    setRows([]);
    setLoadingRows(true);
    const r = await fetch(
      `/api/aceify/rows?eventId=${encodeURIComponent(selected.event_id)}&section=${encodeURIComponent(name)}`
    ).then((x) => x.json());
    setRows(Array.isArray(r) ? r.map((x) => x.row_name) : []);
    setLoadingRows(false);
  }

  /* live quote whenever anything that affects it changes */
  useEffect(() => {
    if (!selected || !section) { setQuote(null); return; }
    const qty = parseInt(debQty, 10);
    if (!qty || qty < 1) { setQuote(null); return; }
    const n = JSON.parse(debNums);
    let alive = true;
    const params = new URLSearchParams({
      eventId: selected.event_id, section, quantity: String(qty), inventory,
      percentile: n.percentile, rankWindow: n.rankWindow,
      maxSections: n.maxSections, margin: n.margin,
    });
    if (row) params.set("row", row);
    fetch(`/api/aceify/quote?${params.toString()}`)
      .then((r) => r.json())
      .then((d) => { if (alive) setQuote(d); })
      .catch(() => { if (alive) setQuote(null); });
    return () => { alive = false; };
  }, [selected, section, row, debQty, inventory, debNums]);

  function reset() {
    setSelected(null); setResults(null); setSuggestions([]);
    setSection(""); setRow(""); setRows([]); setSections([]);
    setQuantity("1"); setQuote(null);
    resetControls(); setAdvOpen(false);
  }

  const invLabel = INVENTORY_LABEL[inventory] || inventory;
  const marginPct = quote && quote.margin != null ? Math.round(Number(quote.margin) * 100) : Number(nums.margin);

  return (
    <div className="page">
      <div className="brand">Aceify · Tennis ticket buyback</div>
      <div className="title">Search an event to quote tickets</div>

      {/* search box + autocomplete */}
      <div className="searchWrap">
        <input
          className="searchInput"
          placeholder="Search tennis events (e.g. Australian Open)..."
          value={query}
          onChange={(e) => { setQuery(e.target.value); setShowDrop(true); }}
          onFocus={() => { if (suggestions.length) setShowDrop(true); }}
          onKeyDown={(e) => { if (e.key === "Enter") runFullSearch(); }}
        />
        {showDrop && !selected && suggestions.length > 0 && (
          <div className="dropdown">
            <div className="dropHead">
              <span>Top Results</span>
              <a onClick={runFullSearch}>See All Results »</a>
            </div>
            {suggestions.map((e) => (
              <EventRow key={e.event_id} e={e} onClick={() => pickEvent(e)} />
            ))}
          </div>
        )}
      </div>

      {/* full results list */}
      {results && !selected && (
        <div className="results">
          {results.length === 0 && <div className="muted">No events found</div>}
          {results.map((e) => (
            <EventRow key={e.event_id} e={e} onClick={() => pickEvent(e)} />
          ))}
        </div>
      )}

      {/* selected event */}
      {selected && (
        <div className="detail">
          <button className="back" onClick={reset}>‹ Back to search</button>
          <h2>{selected.title}</h2>
          <div className="sub">{fmtDate(selected.start_date)} • {venueLine(selected)}</div>

          <div className="fields">
            <SearchableSelect
              label="Section" required placeholder="Select a section"
              options={sections.map((s) => s.section_name)}
              value={section}
              onChange={pickSection}
            />
            <SearchableSelect
              label="Row" placeholder={
                !section ? "Select a section first"
                : loadingRows ? "Loading..."
                : rows.length ? "Select a row (optional)"
                : "No available rows"
              }
              options={rows}
              value={row}
              onChange={setRow}
              disabled={!section || loadingRows}
            />
          </div>

          <div className="fields" style={{ marginTop: 16 }}>
            <div className="field">
              <label>Quantity <span className="req">*</span></label>
              <input
                className="selectBtn" type="number" min="1" max="100"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </div>
            <div className="field" />
          </div>

          {/* ---------- advanced pricing controls ---------- */}
          <div className="advCard">
            <button className="advHead" onClick={() => setAdvOpen((o) => !o)}>
              <span className="advHeadLeft">
                <span className="gear">⚙</span> Pricing controls
                {isDefaults && <span className="defBadge">recommended defaults</span>}
              </span>
              <span className="chev">{advOpen ? "▲" : "▼"}</span>
            </button>

            {!advOpen && (
              <div className="advSummary">
                {invLabel} · P{nums.percentile}% · ±{nums.rankWindow} rank · {nums.maxSections} sections · {nums.margin}% margin
              </div>
            )}

            {advOpen && (
              <div className="advBody">
                <p className="advIntro">
                  These control how we estimate the market price and your offer. The defaults
                  work well for tennis — change them only if you want to. Every change updates
                  the quote below instantly.
                </p>

                {/* price basis */}
                <div className="ctrl">
                  <label>Price basis</label>
                  <div className="toggle3">
                    <button className={inventory === "primary" ? "on" : ""} onClick={() => setInventory("primary")}>Primary</button>
                    <button className={inventory === "resale" ? "on" : ""} onClick={() => setInventory("resale")}>Resale</button>
                    <button className={inventory === "all" ? "on" : ""} onClick={() => setInventory("all")}>All</button>
                  </div>
                  <div className="help">
                    Which tickets we compare against. <b>Primary</b> = official box-office seats
                    (face value) — recommended for the Australian Open. <b>Resale</b> = seats
                    relisted by other fans. <b>All</b> = both. Primary gives the truest
                    face-value comparison for tennis; the other two may have few or no listings.
                  </div>
                </div>

                <div className="ctrlGrid">
                  <NumControl
                    label="Percentile" unit="%" min={0} max={100} step={5}
                    value={nums.percentile} onChange={(v) => setNum("percentile", v)}
                    help="Where in the price range of comparable seats we read the market price. 0% = the cheapest comparable seat (most conservative). 50% = the middle (median). 100% = the most expensive. Higher percentile → higher market price → higher offer."
                  />
                  <NumControl
                    label="Rank window" unit="± rank" min={1} max={5000} step={25}
                    value={nums.rankWindow} onChange={(v) => setNum("rankWindow", v)}
                    help="How far to reach for 'comparable' seats, by seat quality. Every seat has a venue-wide quality rank (1 = best). A window of 200 pools seats within ±200 ranks of the chosen seat. Wider → more seats, steadier price. Narrower → only very similar seats, fewer of them."
                  />
                  <NumControl
                    label="Max sections" unit="sections" min={1} max={50} step={1}
                    value={nums.maxSections} onChange={(v) => setNum("maxSections", v)}
                    help="The most nearby sections to pull comparable seats from (we always pick the closest-in-quality sections first). More → a broader comparison pool. Fewer → keeps the comparison tight to the chosen section."
                  />
                  <NumControl
                    label="Your margin" unit="%" min={0} max={95} step={1}
                    value={nums.margin} onChange={(v) => setNum("margin", v)}
                    help="Your buyback profit cut. The offer we show the seller = market price − margin. Higher margin → lower offer to the seller (more profit for you). Lower margin → higher, more competitive offer."
                  />
                </div>

                {!isDefaults && (
                  <button className="resetBtn" onClick={resetControls}>↺ Reset to recommended defaults</button>
                )}
              </div>
            )}
          </div>

          {/* ---------- live quote ---------- */}
          {section && quote && (
            <div className="quoteBox">
              {quote.basis === "ga_unsupported" ? (
                <div className="qMsg">This is a <b>GA / general-admission</b> section — per-seat pricing isn't available.</div>
              ) : quote.basis === "no_data" || quote.price_per_seat == null ? (
                <div className="qMsg">
                  No {invLabel} seats to price in this section with the current settings. Try the{" "}
                  <b>Price basis</b> or a wider <b>rank window</b> / more <b>sections</b> above.
                </div>
              ) : (
                <>
                  <div className="qRow">
                    <span>Market price / seat <small style={{ color: "#94a3b8" }}>(P{nums.percentile}% of comparable {invLabel} list prices)</small></span>
                    <b>{money(quote.comp_price_per_seat)}</b>
                  </div>
                  <div className="qRow qOffer">
                    <span>Your offer / seat <small style={{ color: "#94a3b8" }}>(after {marginPct}% margin)</small></span>
                    <b>{money(quote.price_per_seat)}</b>
                  </div>
                  <div className="qRow qTotal">
                    <span>Estimated total ({quote.quantity} × {money(quote.price_per_seat)})</span>
                    <b>{money(quote.subtotal)}</b>
                  </div>
                  <div className="qNote">
                    Based on {quote.seats_considered} comparable {invLabel} seat(s) of similar quality
                    across {quote.sections_used} section(s)
                    {quote.related_sections ? ` (${quote.related_sections})` : ""}.
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
