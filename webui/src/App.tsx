import { useState } from "react";
import OverviewPage from "./pages/Overview";
import RacesPage from "./pages/Races";
import BetsPage from "./pages/Bets";
import JobsPage from "./pages/Jobs";
import SettingsPage from "./pages/Settings";

const TABS = [
  { id: "overview", label: "概要" },
  { id: "races", label: "レース" },
  { id: "bets", label: "賭け履歴" },
  { id: "jobs", label: "ジョブ" },
  { id: "settings", label: "設定" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("overview");

  return (
    <div className="app">
      <header className="header">
        <h1>
          🐎 競馬予測AI <span className="header-sub">管理コンソール</span>
        </h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="content">
        {tab === "overview" && <OverviewPage />}
        {tab === "races" && <RacesPage />}
        {tab === "bets" && <BetsPage />}
        {tab === "jobs" && <JobsPage />}
        {tab === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
