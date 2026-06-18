import { useEffect, useState } from "react";
import { getJSON, postJSON } from "./api";
import { ErrorNote } from "./components";
import OverviewPage from "./pages/Overview";
import RacesPage from "./pages/Races";
import BetsPage from "./pages/Bets";
import JobsPage from "./pages/Jobs";
import ModelsPage from "./pages/Models";
import SettingsPage from "./pages/Settings";
import HorsePage from "./pages/Horse";
import PersonPage from "./pages/Person";
import ModelPage from "./pages/Model";
import type { AuthStatus } from "./types";

const TABS = [
  { id: "overview", label: "概要", adminOnly: false, path: "/overview" },
  { id: "races", label: "レース", adminOnly: false, path: "/races" },
  { id: "bets", label: "賭け履歴", adminOnly: false, path: "/bets" },
  { id: "jobs", label: "ジョブ", adminOnly: true, path: "/jobs" },
  { id: "settings", label: "設定", adminOnly: true, path: "/settings" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const LOGIN_PATH = "/login";

function pathToTab(path: string): TabId | null {
  if (path === "/") return "overview";
  const match = TABS.find((t) => t.path === path);
  return match ? match.id : null;
}

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
  const [path, setPath] = useState(() => window.location.pathname);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const horseMatch = path.match(/^\/horses\/([^/]+)$/);
  const horseId = horseMatch ? decodeURIComponent(horseMatch[1]) : null;
  const jockeyMatch = path.match(/^\/jockeys\/([^/]+)$/);
  const jockeyId = jockeyMatch ? decodeURIComponent(jockeyMatch[1]) : null;
  const trainerMatch = path.match(/^\/trainers\/([^/]+)$/);
  const trainerId = trainerMatch ? decodeURIComponent(trainerMatch[1]) : null;
  const modelMatch = path.match(/^\/models\/([^/]+)$/);
  const modelVersion = modelMatch ? decodeURIComponent(modelMatch[1]) : null;
  const modelsListOpen = path === "/models";
  const detailPageOpen = Boolean(
    horseId || jockeyId || trainerId || modelVersion || modelsListOpen
  );
  const visibleTabs = TABS.filter((t) => !t.adminOnly || auth?.authenticated);
  const requestedTab = pathToTab(path);
  // 認証が必要なタブに未ログインでアクセスした場合は概要にフォールバックする。
  const tab: TabId =
    requestedTab && visibleTabs.some((t) => t.id === requestedTab) ? requestedTab : "overview";
  const showLogin = path === LOGIN_PATH;

  // ブラウザの戻る/進むで URL が変わったら表示を追従させる。
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = (to: string) => {
    if (window.location.pathname !== to) {
      window.history.pushState({}, "", to);
    }
    setPath(to);
  };

  useEffect(() => {
    getJSON<AuthStatus>("/api/auth/status")
      .then(setAuth)
      .catch(() => setAuth({ configured: false, authenticated: false }));
  }, []);

  const logout = async () => {
    await postJSON("/api/auth/logout");
    setAuth({ configured: auth?.configured ?? true, authenticated: false });
    if (tab === "jobs" || tab === "settings" || showLogin) {
      navigate("/overview");
    }
  };

  const selectTab = (nextTab: TabId) => {
    const target = TABS.find((t) => t.id === nextTab);
    navigate(target ? target.path : "/overview");
  };

  return (
    <div className="app">
      {!detailPageOpen && <header className="header">
        <h1>
          <img className="header-logo" src="/favicon.svg" alt="" aria-hidden="true" />
          競馬予測AI <span className="header-sub">（プロトタイプ）</span>
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
                onClick={() => navigate(LOGIN_PATH)}
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
              navigate("/overview");
            }}
          />
        )}
        {!showLogin && horseId && <HorsePage horseId={horseId} />}
        {!showLogin && jockeyId && <PersonPage kind="jockey" personId={jockeyId} />}
        {!showLogin && trainerId && <PersonPage kind="trainer" personId={trainerId} />}
        {!showLogin && modelVersion && <ModelPage version={modelVersion} />}
        {!showLogin && modelsListOpen && <ModelsPage />}
        {!showLogin && !detailPageOpen && tab === "overview" && <OverviewPage auth={auth} />}
        {!showLogin && !detailPageOpen && tab === "races" && <RacesPage />}
        {!showLogin && !detailPageOpen && tab === "bets" && <BetsPage auth={auth} />}
        {!showLogin && !detailPageOpen && auth?.authenticated && tab === "jobs" && <JobsPage />}
        {!showLogin && !detailPageOpen && auth?.authenticated && tab === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
