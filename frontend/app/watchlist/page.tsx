import { WatchlistPage } from "./watchlist-page";

export const dynamic = "force-dynamic";

const apiBase = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type WatchlistEntry = {
  player_id: number; player_name: string; club: string; position: string; price: number;
  ownership_percent: number | null; pinned: boolean; added_source: "user" | "research" | "system";
  addition_reason: string | null; added_at: string; last_research_at: string | null;
  research_needed: boolean; open_trigger_count: number; primary_trigger_reason: string | null;
  primary_trigger_source: string | null;
};

export type PlayerOption = {
  player_id: number; player_name: string; club: string; position: string; price: number;
  ownership_percent: number | null; watchlisted: boolean;
};

export type WatchlistSuggestion = {
  id: string; player_id: number; player_name: string; club: string; position: string; price: number;
  reason: string; status: "pending" | "accepted" | "rejected"; created_at: string;
  research_thread_id: string; research_thread_title: string; research_thread_question: string | null;
  evidence: { research_result_id: string; source_url: string; summary: string }[];
};

async function load<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Watchlist data is temporarily unavailable.");
  return response.json() as Promise<T>;
}

export default async function Page() {
  const [entries, suggestions] = await Promise.all([
    load<WatchlistEntry[]>("/watchlist"), load<WatchlistSuggestion[]>("/watchlist/suggestions"),
  ]);
  return <WatchlistPage initialEntries={entries} initialSuggestions={suggestions} />;
}
