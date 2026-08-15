import { notFound } from "next/navigation";
import { ResearchAction } from "./research-action";
import { WatchlistAction } from "./watchlist-action";
import { TriggerResearchAction } from "./trigger-research-action";

export const dynamic = "force-dynamic";

type ResearchResult = {
  id: string; summary: string; findings: string; uncertainty: string | null;
  researched_at: string; source_url: string; source_title: string | null;
  source_domain: string; source_type: string | null; thread_id: string;
  thread_title: string; thread_type: string;
};

type CollectedSource = {
  id: string; url: string; title: string | null; domain: string; source_type: string | null;
  relevance_reason: string | null; status: string; discovered_at: string;
  thread_id: string; thread_title: string; thread_type: string;
};

type PlayerDetails = {
  player: {
    id: number; first_name: string; second_name: string; display_name: string;
    club_name: string; club_short_name: string; position: string; price: number;
    ownership_percent: number | null; availability_status: string;
    chance_of_playing_next_round: number | null; news: string | null;
  };
  watchlist: { active: boolean; pinned: boolean; added_source: string | null; addition_reason: string | null; added_at: string | null };
  current_research_context: Array<{
    situation_id: string; title: string; status: string;
    involved_players: Array<{ player_id: number; player_name: string; club: string; position: string }>;
    active_hypotheses: Array<{ id: string; statement: string }>;
  }>;
  completed_research: ResearchResult[];
  collected_sources: CollectedSource[];
  recent_pulses: Array<{
    gameweek: number; minutes: number | null; starts: number | null; total_points: number | null;
    goals_scored: number | null; assists: number | null; clean_sheets: number | null;
    bonus: number | null; bps: number | null; expected_goals: number | null;
    expected_assists: number | null; expected_goal_involvements: number | null;
  }>;
  recent_pulse_summary: { appearances: number; attacking_blank_streak: number; total_points: number };
  research_triggers: Array<{ id: string; trigger_type: string; source: string; status: string; description: string; gameweek: number | null; created_at: string }>;
  monitoring_triggers: Array<{ id: string; description: string; category: string; active: boolean; satisfied_at: string | null }>;
};

const apiBase = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

function date(value: string) {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

async function getPlayer(playerId: string): Promise<PlayerDetails> {
  const response = await fetch(`${apiBase}/fpl/players/${playerId}`, { cache: "no-store" });
  if (response.status === 404) notFound();
  if (!response.ok) throw new Error("Player intelligence is temporarily unavailable.");
  return response.json() as Promise<PlayerDetails>;
}

export default async function PlayerPage({ params }: { params: Promise<{ playerId: string }> }) {
  const { playerId } = await params;
  const data = await getPlayer(playerId);
  const latest = data.completed_research[0];
  const threadCount = new Set([
    ...data.completed_research.map((item) => item.thread_id),
    ...data.collected_sources.map((item) => item.thread_id),
  ]).size;

  return (
    <main className="shell">
      <header className="player-header">
        <div>
          <p className="eyebrow">{data.player.club_name} · {data.player.position}</p>
          <h1>{data.player.first_name} {data.player.second_name}</h1>
          <p className="player-meta">£{data.player.price.toFixed(1)}m · {data.player.ownership_percent ?? "—"}% selected</p>
        </div>
        <div><WatchlistAction playerId={data.player.id} initial={data.watchlist} /><TriggerResearchAction playerId={data.player.id} /><ResearchAction playerId={data.player.id} /></div>
      </header>

      <section aria-labelledby="pulse-heading">
        <div className="section-heading"><div><p className="eyebrow">Official FPL facts</p><h2 id="pulse-heading">Recent Gameweeks</h2></div><span>{data.recent_pulses.length}</span></div>
        {data.recent_pulses.length ? <>
          <div className="pulse-summary"><span>{data.recent_pulse_summary.appearances} apps</span><span>{data.recent_pulse_summary.total_points} pts</span><span>{data.recent_pulse_summary.attacking_blank_streak} attacking blank streak</span></div>
          <div className="pulse-strip">{data.recent_pulses.map((pulse) => (
            <article className="pulse-card" key={pulse.gameweek}>
              <strong>GW{pulse.gameweek}</strong><span>{pulse.minutes ?? "—"} min</span><span>{pulse.total_points ?? "—"} pts</span>
              <small>{pulse.goals_scored ?? "—"} G · {pulse.assists ?? "—"} A{pulse.bonus ? ` · ${pulse.bonus} B` : ""}</small>
            </article>
          ))}</div>
        </> : <p className="muted">No weekly pulse has been captured yet.</p>}
      </section>

      <section aria-labelledby="trigger-heading">
        <div className="section-heading"><div><p className="eyebrow">Why investigate</p><h2 id="trigger-heading">Research triggers</h2></div><span>{data.research_triggers.length}</span></div>
        {data.research_triggers.length ? <div className="trigger-list">{data.research_triggers.map(trigger => <article className="trigger-card" key={trigger.id}><div><span className={`status status-${trigger.status}`}>{trigger.status}</span><span className="thread-tag">{trigger.source}</span></div><strong>{trigger.description}</strong><small>{trigger.trigger_type.replaceAll("_", " ")}{trigger.gameweek ? ` · GW${trigger.gameweek}` : ""}</small></article>)}</div> : <p className="muted">No research triggers recorded.</p>}
      </section>

      <section aria-labelledby="context-heading">
        <div className="section-heading"><div><p className="eyebrow">Situation</p><h2 id="context-heading">Current Research Context</h2></div><span>{data.current_research_context.length}</span></div>
        {data.current_research_context.length ? (
          <div className="context-list">{data.current_research_context.map((situation) => (
            <article className="context-card" key={situation.situation_id}>
              <div className="card-top"><span className={`status status-${situation.status}`}>{situation.status}</span><span className="thread-tag">{situation.involved_players.length} player{situation.involved_players.length === 1 ? "" : "s"}</span></div>
              <h3>{situation.title}</h3>
              <p className="context-players">{situation.involved_players.map(player => `${player.player_name} (${player.position})`).join(", ")}</p>
              {situation.active_hypotheses.length ? <ul>{situation.active_hypotheses.map(hypothesis => <li key={hypothesis.id}>{hypothesis.statement}</li>)}</ul> : null}
            </article>
          ))}</div>
        ) : <p className="muted">No current research situation recorded.</p>}
      </section>

      <section aria-labelledby="monitoring-heading">
        <div className="section-heading"><div><p className="eyebrow">What to watch next</p><h2 id="monitoring-heading">Monitoring triggers</h2></div><span>{data.monitoring_triggers.filter(item => item.active).length} active</span></div>
        {data.monitoring_triggers.length ? <div className="trigger-list">{data.monitoring_triggers.map(trigger => <article className="trigger-card" key={trigger.id}><span className="thread-tag">{trigger.category.replaceAll("_", " ")}</span><strong>{trigger.description}</strong><small>{trigger.active ? "Active" : trigger.satisfied_at ? "Satisfied" : "Retired"}</small></article>)}</div> : <p className="muted">No future monitoring conditions recorded.</p>}
      </section>

      <section aria-labelledby="latest-heading">
        <div className="section-heading"><div><p className="eyebrow">Current view</p><h2 id="latest-heading">Latest intelligence</h2></div></div>
        {latest ? (
          <article className="latest-card">
            <span className="thread-tag">{latest.thread_title}</span>
            <h3>{latest.summary}</h3>
            <p>{latest.findings}</p>
            {latest.uncertainty && <p className="uncertainty"><strong>Uncertainty:</strong> {latest.uncertainty}</p>}
            <p className="source-line">Researched {date(latest.researched_at)} · <a href={latest.source_url} target="_blank" rel="noopener noreferrer">{latest.source_title ?? latest.source_domain} ↗</a></p>
          </article>
        ) : (
          <div className="empty"><h3>No completed research</h3><p>No source has produced completed intelligence for this player yet. Collected links, if any, remain listed separately below.</p></div>
        )}
      </section>

      <section aria-labelledby="completed-heading">
        <div className="section-heading"><div><p className="eyebrow">Evidence history</p><h2 id="completed-heading">Completed research</h2></div><span>{data.completed_research.length}</span></div>
        {data.completed_research.length ? (
          <div className="timeline">{data.completed_research.map((item) => (
            <article className="research-card" key={item.id}>
              <div className="card-top"><span className="thread-tag">{item.thread_title}</span><time>{date(item.researched_at)}</time></div>
              <h3>{item.summary}</h3><p>{item.findings}</p>
              {item.uncertainty && <p className="uncertainty"><strong>Remaining uncertainty:</strong> {item.uncertainty}</p>}
              <a className="source-link" href={item.source_url} target="_blank" rel="noopener noreferrer">Open {item.source_title ?? item.source_domain} ↗</a>
            </article>
          ))}</div>
        ) : <p className="muted">Completed findings will appear here newest first.</p>}
      </section>

      <section aria-labelledby="sources-heading">
        <div className="section-heading"><div><p className="eyebrow">Not findings</p><h2 id="sources-heading">Collected sources</h2></div><span>{data.collected_sources.length}</span></div>
        <p className="distinction">Collected Source ≠ Completed Research</p>
        {data.collected_sources.length ? (
          <div className="source-list">{data.collected_sources.map((source) => (
            <article className="source-card" key={source.id}>
              <div className="card-top"><span className={`status status-${source.status}`}>{source.status}</span><time>{date(source.discovered_at)}</time></div>
              <h3><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title ?? source.domain} ↗</a></h3>
              <p>{source.relevance_reason ?? "Collected as a potentially relevant source."}</p>
              <p className="source-line">{source.source_type ?? source.domain} · {source.thread_title}</p>
            </article>
          ))}</div>
        ) : <div className="empty"><h3>No collected sources</h3><p>Use “Research player” to prepare a player investigation and begin collecting sources.</p></div>}
      </section>

      {threadCount > 0 && <p className="thread-summary">Intelligence shown across {threadCount} research thread{threadCount === 1 ? "" : "s"}.</p>}
    </main>
  );
}
