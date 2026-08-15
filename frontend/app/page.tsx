import Link from "next/link";

export default function Home() {
  return (
    <main className="shell">
      <p className="eyebrow">FPL Intelligence</p>
      <h1>Player research</h1>
      <p className="muted">Open a player at /players/&lt;official FPL player ID&gt;.</p>
      <p><Link className="home-link" href="/watchlist">Open Watchlist →</Link></p>
    </main>
  );
}
