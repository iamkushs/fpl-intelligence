"use client";

import { useState } from "react";
import { QueueResearchAction } from "../../queue-research-action";

type WatchlistState = { active: boolean; pinned: boolean };
const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export function WatchlistAction({ playerId, initial }: { playerId: number; initial: WatchlistState }) {
  const [state, setState] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function mutate(action: "membership" | "pin") {
    setBusy(true); setMessage("");
    try {
      const response = action === "membership"
        ? await fetch(`${apiBase}/watchlist/${playerId}`, { method: state.active ? "DELETE" : "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
        : await fetch(`${apiBase}/watchlist/${playerId}/pin`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pinned: !state.pinned }) });
      if (!response.ok) throw new Error("Could not update the Watchlist.");
      const updated = await response.json() as WatchlistState;
      setState(updated);
      setMessage(updated.active ? (updated.pinned ? "Player is pinned." : "Player is on the Watchlist.") : "Player removed from the Watchlist.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not update the Watchlist."); }
    finally { setBusy(false); }
  }

  return <div className="research-action"><QueueResearchAction playerId={playerId} />
    <button onClick={() => mutate("membership")} disabled={busy}>{state.active ? "Remove from Watchlist" : "Add to Watchlist"}</button>
    {state.active && <button onClick={() => mutate("pin")} disabled={busy}>{state.pinned ? "Unpin" : "Pin"}</button>}
    {message && <p className="action-message" role="status">{message}</p>}
  </div>;
}
