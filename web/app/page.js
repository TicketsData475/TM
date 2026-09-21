"use client";
import { useEffect, useRef, useState } from "react";

/* --- helpers --- */
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

/* --- reusable searchable dropdown --- */
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

/* --- event row (used in dropdown + list) --- */
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

export default function Home() {
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query, 300);

  const [suggestions, setSuggestions] = useState([]);
  const [showDrop, setShowDrop] = useState(false);
  const [results, setResults] = useState(null);      // full list after Enter

  const [selected, setSelected] = useState(null);    // chosen event
  const [sections, setSections] = useState([]);
  const [section, setSection] = useState("");
  const [rows, setRows] = useState([]);
  const [row, setRow] = useState("");
  const [loadingRows, setLoadingRows] = useState(false);

  const [quantity, setQuantity] = useState("1");
  const [inventory, setInventory] = useState("resale");   // 'resale' | 'all'
  const [quote, setQuote] = useState(null);
  const debQty = useDebounced(quantity, 250);

  /* live autocomplete */
  useEffect(() => {
    if (selected) return; // don't fetch suggestions while viewing an event
    if (debounced.trim().length < 2) { setSuggestions([]); return; }
    let alive = true;
    fetch(`/api/search?q=${encodeURIComponent(debounced)}&limit=6`)
      .then((r) => r.json())
      .then((d) => { if (alive) { setSuggestions(Array.isArray(d) ? d : []); setShowDrop(true); } })
      .catch(() => {});
    return () => { alive = false; };
  }, [debounced, selected]);

  async function runFullSearch() {
    if (query.trim().length < 2) return;
    setShowDrop(false);
    setSelected(null);
    const d = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=20`).then((r) => r.json());
    setResults(Array.isArray(d) ? d : []);
  }

  async function pickEvent(e) {
    setSelected(e);
    setShowDrop(false);
    setResults(null);
    setSection(""); setRow(""); setRows([]); setSections([]);
    const s = await fetch(`/api/sections?eventId=${encodeURIComponent(e.event_id)}`).then((r) => r.json());
    setSections(Array.isArray(s) ? s : []);
  }

  async function pickSection(name) {
    setSection(name);
    setRow("");
    setRows([]);
    setLoadingRows(true);
    const r = await fetch(
      `/api/rows?eventId=${encodeURIComponent(selected.event_id)}&section=${encodeURIComponent(name)}`
    ).then((x) => x.json());
    setRows(Array.isArray(r) ? r.map((x) => x.row_name) : []);
    setLoadingRows(false);
  }

  /* live quote whenever section / row / quantity / inventory changes */
  useEffect(() => {
    if (!selected || !section) { setQuote(null); return; }
    const qty = parseInt(debQty, 10);
    if (!qty || qty < 1) { setQuote(null); return; }
    let alive = true;
    const params = new URLSearchParams({
      eventId: selected.event_id, section, quantity: String(qty), inventory,
    });
    if (row) params.set("row", row);
    fetch(`/api/quote?${params.toString()}`)
      .then((r) => r.json())
      .then((d) => { if (alive) setQuote(d); })
      .catch(() => { if (alive) setQuote(null); });
    return () => { alive = false; };
  }, [selected, section, row, debQty, inventory]);

  function reset() {
    setSelected(null); setResults(null); setSuggestions([]);
    setSection(""); setRow(""); setRows([]); setSections([]);
    setQuantity("1"); setQuote(null);
  }


  return (
    <div className="page">
      <div className="title">Search an event to sell tickets</div>

      {/* search box + autocomplete */}
      <div className="searchWrap">
        <input
          className="searchInput"
          placeholder="Search events..."
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

      {/* full results list (after Enter / See All) */}
      {results && !selected && (
        <div className="results">
          {results.length === 0 && <div className="muted">No events found</div>}
          {results.map((e) => (
            <EventRow key={e.event_id} e={e} onClick={() => pickEvent(e)} />
          ))}
        </div>
      )}

      {/* selected event -> section + row */}
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

          {/* quantity + inventory basis */}
          <div className="fields" style={{ marginTop: 16 }}>
            <div className="field">
              <label>Quantity <span className="req">*</span></label>
              <input
                className="selectBtn" type="number" min="1" max="100"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </div>
            <div className="field">
              <label>Price basis</label>
              <div className="toggle">
                <button className={inventory === "resale" ? "on" : ""} onClick={() => setInventory("resale")}>Resale (buyback)</button>
                <button className={inventory === "all" ? "on" : ""} onClick={() => setInventory("all")}>All</button>
              </div>
            </div>
          </div>

          {/* live quote */}
          {section && quote && (
            <div className="quoteBox">
              {quote.basis === "ga_unsupported" ? (
                <div className="qMsg">This is a <b>GA / general-admission</b> section — per-seat pricing isn't available yet.</div>
              ) : quote.basis === "no_data" || quote.price_per_seat == null ? (
                <div className="qMsg">No {inventory === "resale" ? "resale" : "available"} seats to price in this section right now.</div>
              ) : (
                <>
                  <div className="qRow">
                    <span>Market price / seat <small style={{ color: "#94a3b8" }}>(P5, incl. fees)</small></span>
                    <b>{money(quote.price_per_seat)}</b>
                  </div>
                  <div className="qRow qTotal">
                    <span>Estimated total ({quote.quantity} × {money(quote.price_per_seat)})</span>
                    <b>{money(quote.subtotal)}</b>
                  </div>
                  <div className="qNote">
                    Based on {quote.seats_considered} comparable {inventory === "resale" ? "resale" : "available"} seat(s)
                    of similar quality across {quote.sections_used} section(s)
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
