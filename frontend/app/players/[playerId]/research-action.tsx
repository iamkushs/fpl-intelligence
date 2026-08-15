"use client";

import { useState } from "react";

type PreparedThread = { thread_id: string; title: string; collect_url: string };

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export function ResearchAction({ playerId }: { playerId: number }) {
  const [thread, setThread] = useState<PreparedThread | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function prepare() {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${apiBase}/fpl/players/${playerId}/research`, { method: "POST" });
      if (!response.ok) throw new Error("Could not prepare player research.");
      setThread((await response.json()) as PreparedThread);
      setMessage("Research thread ready. You can now collect relevant sources.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not prepare player research.");
    } finally {
      setBusy(false);
    }
  }

  async function collect() {
    if (!thread) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${apiBase}${thread.collect_url}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) throw new Error("Source collection could not be completed.");
      const result = (await response.json()) as { links_added: number };
      setMessage(`Collection complete: ${result.links_added} new source${result.links_added === 1 ? "" : "s"}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Source collection could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="research-action">
      {!thread ? (
        <button onClick={prepare} disabled={busy}>{busy ? "Preparing…" : "Research player"}</button>
      ) : (
        <button onClick={collect} disabled={busy}>{busy ? "Collecting…" : "Collect relevant sources"}</button>
      )}
      {message && <p className="action-message" role="status">{message}</p>}
    </div>
  );
}
