"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type { PlayerOption, WatchlistEntry, WatchlistSuggestion } from "./page";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
export const STALE_RESEARCH_DAYS = 14;
const positions = ["All", "GK", "DEF", "MID", "FWD"];

function ageInDays(value: string | null) {
  return value ? Math.floor((Date.now() - new Date(value).getTime()) / 86400000) : Infinity;
}
function freshness(value: string | null) {
  if (!value) return "Never researched";
  const days = ageInDays(value);
  if (days <= 0) return "Researched today";
  if (days === 1) return "Researched yesterday";
  return `Researched ${days} days ago`;
}
function formattedDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}
function toEntry(player: PlayerOption, membership: { pinned: boolean; added_source: WatchlistEntry["added_source"]; addition_reason: string | null; added_at: string }): WatchlistEntry {
  return { ...player, last_research_at: null, research_needed: false, open_trigger_count: 0,
    primary_trigger_reason: null, primary_trigger_source: null, ...membership };
}

export function WatchlistPage({ initialEntries, initialSuggestions }: { initialEntries: WatchlistEntry[]; initialSuggestions: WatchlistSuggestion[] }) {
  const [entries, setEntries] = useState(initialEntries);
  const [suggestions, setSuggestions] = useState(initialSuggestions);
  const [suggestionBusy, setSuggestionBusy] = useState<string | null>(null);
  const [search, setSearch] = useState(""); const [position, setPosition] = useState("All");
  const [club, setClub] = useState("All"); const [source, setSource] = useState("All");
  const [research, setResearch] = useState("All"); const [pinned, setPinned] = useState("All");
  const [triggerFilter, setTriggerFilter] = useState("All");
  const [sort, setSort] = useState("least-researched"); const [busy, setBusy] = useState<number | null>(null);
  const [addSearch, setAddSearch] = useState(""); const [selectedId, setSelectedId] = useState("");
  const [playerOptions, setPlayerOptions] = useState<PlayerOption[]>([]); const [searchingPlayers, setSearchingPlayers] = useState(false);
  const [reason, setReason] = useState(""); const [addPinned, setAddPinned] = useState(false); const [message, setMessage] = useState("");
  const clubs = useMemo(() => Array.from(new Set(entries.map((entry) => entry.club))).sort(), [entries]);
  const available = playerOptions.filter((player) => !player.watchlisted && !entries.some((entry) => entry.player_id === player.player_id));
  const summary = { total: entries.length, pinned: entries.filter(e => e.pinned).length, never: entries.filter(e => !e.last_research_at).length, stale: entries.filter(e => e.last_research_at && ageInDays(e.last_research_at) > STALE_RESEARCH_DAYS).length };

  useEffect(() => {
    const term = addSearch.trim();
    if (!term) { setPlayerOptions([]); setSearchingPlayers(false); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSearchingPlayers(true);
      try {
        const response = await fetch(`${apiBase}/fpl/players?search=${encodeURIComponent(term)}&limit=20`, { signal: controller.signal });
        if (!response.ok) throw new Error();
        setPlayerOptions(await response.json());
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) setMessage("Player search is temporarily unavailable.");
      } finally { if (!controller.signal.aborted) setSearchingPlayers(false); }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [addSearch]);

  const filtered = useMemo(() => entries.filter((entry) => {
    const text = `${entry.player_name} ${entry.club}`.toLowerCase(); const age = ageInDays(entry.last_research_at);
    return text.includes(search.toLowerCase()) && (position === "All" || entry.position === position)
      && (club === "All" || entry.club === club) && (source === "All" || entry.added_source === source)
      && (pinned === "All" || (pinned === "Pinned") === entry.pinned)
      && (triggerFilter === "All" || (triggerFilter === "Needs research") === entry.research_needed)
      && (research === "All" || (research === "Never" && !entry.last_research_at)
        || (research === "Recent" && age <= STALE_RESEARCH_DAYS) || (research === "Stale" && !!entry.last_research_at && age > STALE_RESEARCH_DAYS));
  }).sort((a, b) => {
    if (sort === "name") return a.player_name.localeCompare(b.player_name);
    if (sort === "position") return a.position.localeCompare(b.position) || a.player_name.localeCompare(b.player_name);
    if (sort === "price") return b.price - a.price;
    if (sort === "most-researched") return (b.last_research_at ? new Date(b.last_research_at).getTime() : 0) - (a.last_research_at ? new Date(a.last_research_at).getTime() : 0);
    if (sort === "added") return new Date(b.added_at).getTime() - new Date(a.added_at).getTime();
    return ageInDays(b.last_research_at) - ageInDays(a.last_research_at);
  }), [entries, search, position, club, source, research, pinned, triggerFilter, sort]);

  function reset() { setSearch(""); setPosition("All"); setClub("All"); setSource("All"); setResearch("All"); setPinned("All"); setTriggerFilter("All"); }
  async function action(entry: WatchlistEntry, kind: "pin" | "remove") {
    if (kind === "remove" && !window.confirm(`Remove ${entry.player_name} from the Watchlist?`)) return;
    setBusy(entry.player_id);
    const response = await fetch(`${apiBase}/watchlist/${entry.player_id}${kind === "pin" ? "/pin" : ""}`, {
      method: kind === "pin" ? "PATCH" : "DELETE", headers: { "Content-Type": "application/json" },
      body: kind === "pin" ? JSON.stringify({ pinned: !entry.pinned }) : "{}",
    });
    if (response.ok) setEntries(current => kind === "remove" ? current.filter(e => e.player_id !== entry.player_id) : current.map(e => e.player_id === entry.player_id ? { ...e, pinned: !e.pinned } : e));
    else setMessage("Could not update the Watchlist.");
    setBusy(null);
  }
  async function add(event: FormEvent) {
    event.preventDefault(); const player = playerOptions.find(item => item.player_id === Number(selectedId)); if (!player) return;
    setBusy(player.player_id); setMessage("");
    const response = await fetch(`${apiBase}/watchlist/${player.player_id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason || null, pinned: addPinned }) });
    if (response.ok) { const membership = await response.json(); setEntries(current => [...current, toEntry(player, membership)]); setSelectedId(""); setReason(""); setAddPinned(false); setAddSearch(""); setMessage(`${player.player_name} added.`); }
    else setMessage("Could not add that player."); setBusy(null);
  }
  async function reviewSuggestion(suggestion: WatchlistSuggestion, action: "accept" | "reject") {
    setSuggestionBusy(suggestion.id); setMessage("");
    const response = await fetch(`${apiBase}/watchlist/suggestions/${suggestion.id}/${action}`, { method: "POST" });
    if (response.ok) {
      const payload = await response.json();
      setSuggestions(current => current.filter(item => item.id !== suggestion.id));
      if (action === "accept") {
        setEntries(current => current.some(item => item.player_id === suggestion.player_id) ? current : [...current, payload.watchlist]);
        setMessage(`${suggestion.player_name} added from research.`);
      }
    } else setMessage(`Could not ${action === "accept" ? "add" : "dismiss"} that suggestion.`);
    setSuggestionBusy(null);
  }

  return <main className="shell watchlist-shell">
    <nav className="crumb"><Link href="/">FPL Intelligence</Link> / Watchlist</nav>
    <header className="watchlist-header"><div><p className="eyebrow">Curated player intelligence</p><h1>Watchlist</h1><p className="muted">Interesting players worth tracking, not a transfer shortlist.</p></div></header>
    <div className="watchlist-summary"><span><strong>{summary.total}</strong> active</span><span><strong>{summary.pinned}</strong> pinned</span><span><strong>{summary.never}</strong> never researched</span><span><strong>{summary.stale}</strong> stale</span></div>

    {!!suggestions.length && <section className="suggestions-panel" aria-labelledby="suggested-players-title">
      <div><p className="eyebrow">Research discovery</p><h2 id="suggested-players-title">Suggested Players</h2><p className="muted">Evidence-backed players awaiting your review. Suggestions are not active Watchlist members.</p></div>
      <div className="suggestion-list">{suggestions.map(item => <article className="suggestion-row" key={item.id}>
        <div className="player-line"><span className="position-badge">{item.position}</span><div><h3><Link href={`/players/${item.player_id}`}>{item.player_name}</Link></h3><p>{item.club} · £{item.price.toFixed(1)}m</p></div></div>
        <div className="suggestion-context"><p>{item.reason}</p><small>From “{item.research_thread_title}” · {item.evidence.length} researched source{item.evidence.length === 1 ? "" : "s"}</small>
          {!!item.evidence.length && <details><summary>View research context</summary>{item.evidence.map(evidence => <p key={evidence.research_result_id}><a href={evidence.source_url} target="_blank" rel="noreferrer">Source</a> — {evidence.summary}</p>)}</details>}
        </div>
        <div className="row-actions"><button disabled={suggestionBusy === item.id} onClick={() => reviewSuggestion(item, "accept")}>Add to Watchlist</button><button className="quiet-button" disabled={suggestionBusy === item.id} onClick={() => reviewSuggestion(item, "reject")}>Dismiss</button></div>
      </article>)}</div>
    </section>}

    <details className="add-panel" open={!entries.length}><summary>Add a player</summary><form onSubmit={add}>
      <label>Find persisted player<input value={addSearch} onChange={e => { setAddSearch(e.target.value); setSelectedId(""); }} placeholder="Search name or club" /></label>
      <label>Player<select required value={selectedId} onChange={e => setSelectedId(e.target.value)} disabled={!addSearch.trim() || searchingPlayers}><option value="">{searchingPlayers ? "Searching…" : addSearch.trim() ? available.length ? "Select a player" : "No available players found" : "Search first"}</option>{available.map(player => <option key={player.player_id} value={player.player_id}>{player.player_name} · {player.club} · {player.position}</option>)}</select></label>
      <label>Reason (optional)<input value={reason} maxLength={300} onChange={e => setReason(e.target.value)} placeholder="Why are they interesting?" /></label>
      <label className="check"><input type="checkbox" checked={addPinned} onChange={e => setAddPinned(e.target.checked)} /> Pin player</label><button disabled={!selectedId || busy !== null}>Add to Watchlist</button>
    </form></details>{message && <p className="action-message" role="status">{message}</p>}

    {!!entries.length && <><section className="filter-panel" aria-label="Watchlist filters"><label className="search-field">Search<input value={search} onChange={e => setSearch(e.target.value)} placeholder="Player or club" /></label>
      <label>Position<select value={position} onChange={e => setPosition(e.target.value)}>{positions.map(x => <option key={x}>{x}</option>)}</select></label>
      <label>Club<select value={club} onChange={e => setClub(e.target.value)}><option>All</option>{clubs.map(x => <option key={x}>{x}</option>)}</select></label>
      <label>Added by<select value={source} onChange={e => setSource(e.target.value)}><option>All</option><option value="user">User</option><option value="research">Research</option><option value="system">System</option></select></label>
      <label>Research<select value={research} onChange={e => setResearch(e.target.value)}><option>All</option><option value="Never">Never researched</option><option value="Recent">Recent (≤ {STALE_RESEARCH_DAYS} days)</option><option value="Stale">Stale (&gt; {STALE_RESEARCH_DAYS} days)</option></select></label>
      <label>Trigger<select value={triggerFilter} onChange={e => setTriggerFilter(e.target.value)}><option>All</option><option>Needs research</option><option>No open triggers</option></select></label>
      <label>Pinned<select value={pinned} onChange={e => setPinned(e.target.value)}><option>All</option><option>Pinned</option><option>Unpinned</option></select></label>
      <label>Sort<select value={sort} onChange={e => setSort(e.target.value)}><option value="least-researched">Least recently researched</option><option value="most-researched">Most recently researched</option><option value="added">Most recently added</option><option value="name">Player name</option><option value="position">Position</option><option value="price">Price</option></select></label>
      <button className="filter-reset" type="button" onClick={reset}>Reset filters</button>
    </section>
    {filtered.length ? <div className="watchlist-list">{filtered.map(entry => <article className="watchlist-row" key={entry.player_id}>
      <div className="player-line"><span className="position-badge">{entry.position}</span><div><h2><Link href={`/players/${entry.player_id}`}>{entry.player_name}</Link>{entry.pinned && <span className="pin-mark" title="Pinned"> ◆</span>}</h2><p>{entry.club} · £{entry.price.toFixed(1)}m</p></div></div>
      <div className="watch-context"><span className="thread-tag">{entry.added_source}</span>{entry.research_needed && <span className="trigger-badge">Needs research</span>}<p>{entry.research_needed ? entry.primary_trigger_reason : entry.addition_reason ?? "No addition reason recorded."}</p><p>{entry.research_needed ? `${entry.open_trigger_count} open trigger${entry.open_trigger_count === 1 ? "" : "s"} · ${entry.primary_trigger_source}` : `Added ${formattedDate(entry.added_at)}`}</p></div>
      <div className={`freshness ${!entry.last_research_at || ageInDays(entry.last_research_at) > STALE_RESEARCH_DAYS ? "needs-research" : ""}`} title={entry.last_research_at ? `Last researched ${formattedDate(entry.last_research_at)}` : undefined}>{freshness(entry.last_research_at)}</div>
      <div className="row-actions"><Link className="text-action" href={`/players/${entry.player_id}`}>View research</Link><button className="quiet-button" disabled={busy === entry.player_id} onClick={() => action(entry, "pin")}>{entry.pinned ? "Unpin" : "Pin"}</button><button className="danger-button" disabled={busy === entry.player_id} onClick={() => action(entry, "remove")}>Remove</button></div>
    </article>)}</div> : <div className="empty"><h2>No matching players</h2><p>These filters do not match the current Watchlist.</p><button onClick={reset}>Reset filters</button></div>}</>}
    {!entries.length && <div className="empty watchlist-empty"><h2>Your Watchlist is empty</h2><p>Add persisted FPL players who are interesting enough to monitor. This does not mark them as immediate transfer targets.</p></div>}
  </main>;
}
