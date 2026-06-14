import { useEffect, useState } from "react";
import { getJSON, postJSON } from "./api";
import { ErrorNote } from "./components";
import OverviewPage from "./pages/Overview";
import RacesPage from "./pages/Races";
import BetsPage from "./pages/Bets";
import JobsPage from "./pages/Jobs";
import SettingsPage from "./pages/Settings";
import HorsePage from "./pages/Horse";
import type { AuthStatus } from "./types";

const TABS = [
  { id: "overview", label: "概要", adminOnly: false },
  { id: "races", label: "レース", adminOnly: false },
  { id: "bets", label: "賭け履歴", adminOnly: false },
  { id: "jobs", label: "ジョブ", adminOnly: true },
  { id: "settings", label: "設定", adminOnly: true },
] as const;

type TabId = (typeof TABS)[number]["id"];

function LoginPage({
  auth,
  onLogin,
}: {
  auth: AuthStatus | null;
  onLogin: (auth: AuthStatus) => void;
}) {
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const login = async () => {
    setError(null);
    setBusy(true);
    try {
      await postJSON("/api/auth/login", { login_id: loginId, password });
      setPassword("");
      onLogin({ configured: true, authenticated: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-panel">
        <h2>管理者ログイン</h2>
        {!auth?.configured && (
          <div className="danger-note">ADMIN_LOGIN_ID / ADMIN_PASSWORD が未設定です。</div>
        )}
        <ErrorNote message={error} />
        <label>
          <span>ログインID</span>
          <input
            value={loginId}
            onChange={(e) => setLoginId(e.target.value)}
            disabled={!auth?.configured || busy}
            autoFocus
          />
        </label>
        <label>
          <span>パスワード</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={!auth?.configured || busy}
            onKeyDown={(e) => {
              if (e.key === "Enter") void login();
            }}
          />
        </label>
        <button className="primary" onClick={login} disabled={!auth?.configured || busy}>
          {busy ? "ログイン中..." : "ログイン"}
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<TabId>("overview");
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const horseMatch = window.location.pathname.match(/^\/horses\/([^/]+)$/);
  const horseId = horseMatch ? decodeURIComponent(horseMatch[1]) : null;
  const visibleTabs = TABS.filter((t) => !t.adminOnly || auth?.authenticated);

  useEffect(() => {
    getJSON<AuthStatus>("/api/auth/status")
      .then(setAuth)
      .catch(() => setAuth({ configured: false, authenticated: false }));
  }, []);

  useEffect(() => {
    if (!visibleTabs.some((t) => t.id === tab)) {
      setTab("overview");
    }
  }, [tab, visibleTabs]);

  const logout = async () => {
    await postJSON("/api/auth/logout");
    setAuth({ configured: auth?.configured ?? true, authenticated: false });
    setShowLogin(false);
    if (tab === "jobs" || tab === "settings") {
      setTab("overview");
    }
  };

  const selectTab = (nextTab: TabId) => {
    setShowLogin(false);
    setTab(nextTab);
  };

  return (
    <div className="app">
      {!horseId && <header className="header">
        <h1>
          競馬予測AI <span className="header-sub">管理コンソール</span>
        </h1>
        <div className="header-nav">
          <nav className="tabs">
            {visibleTabs.map((t) => (
              <button
                key={t.id}
                className={`tab ${!showLogin && tab === t.id ? "active" : ""}`}
                onClick={() => selectTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="auth-actions">
            {auth?.authenticated ? (
              <button className="auth-button auth-button-logout" onClick={() => void logout()}>
                ログアウト
              </button>
            ) : (
              <button
                className={`auth-button auth-button-login ${showLogin ? "active" : ""}`}
                onClick={() => setShowLogin(true)}
              >
                ログイン
              </button>
            )}
          </div>
        </div>
      </header>}
      <main className="content">
        {showLogin && (
          <LoginPage
            auth={auth}
            onLogin={(nextAuth) => {
              setAuth(nextAuth);
              setShowLogin(false);
            }}
          />
        )}
        {!showLogin && horseId && <HorsePage horseId={horseId} />}
        {!showLogin && !horseId && tab === "overview" && <OverviewPage auth={auth} />}
        {!showLogin && !horseId && tab === "races" && <RacesPage />}
        {!showLogin && !horseId && tab === "bets" && <BetsPage auth={auth} />}
        {!showLogin && !horseId && auth?.authenticated && tab === "jobs" && <JobsPage />}
        {!showLogin && !horseId && auth?.authenticated && tab === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
