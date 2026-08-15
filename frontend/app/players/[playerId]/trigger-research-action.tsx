"use client";

import { useState } from "react";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export function TriggerResearchAction({ playerId }: { playerId: number }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function trigger() {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/fpl/players/${playerId}/trigger-research`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (!response.ok) throw new Error();
      setMessage("Added to the research queue.");
    } catch { setMessage("Could not add this player to the research queue."); }
    finally { setBusy(false); }
  }
  return <div className="research-action"><button className="quiet-button" disabled={busy} onClick={trigger}>{busy ? "Adding…" : "Research again"}</button>{message && <p className="action-message" role="status">{message}</p>}</div>;
}
