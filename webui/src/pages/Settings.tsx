import { useEffect, useState } from "react";
import { getJSON, putJSON } from "../api";
import { ErrorNote } from "../components";
import type { SettingsView } from "../types";

export default function SettingsPage() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [form, setForm] = useState({
    betting_mode: "sim",
    bet_amount: "100",
    bet_score_threshold: "0.15",
    bet_min_expected_value: "1.0",
  });
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const applyView = (v: SettingsView) => {
    setView(v);
    setForm({
      betting_mode: v.editable.betting_mode,
      bet_amount: String(v.editable.bet_amount),
      bet_score_threshold: String(v.editable.bet_score_threshold),
      bet_min_expected_value: String(v.editable.bet_min_expected_value),
    });
  };

  useEffect(() => {
    getJSON<SettingsView>("/api/settings")
      .then(applyView)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const save = async () => {
    setError(null);
    setMessage(null);

    if (
      form.betting_mode === "prod" &&
      view?.editable.betting_mode !== "prod" &&
      !window.confirm(
        "本番モード(prod)に切り替えます。IPAT_DRY_RUN=false の場合、実際にお金を使った購入が行われます。よろしいですか?"
      )
    ) {
      return;
    }

    setSaving(true);
    try {
      const updated = await putJSON<SettingsView>("/api/settings", {
        betting_mode: form.betting_mode,
        bet_amount: Number(form.bet_amount),
        bet_score_threshold: Number(form.bet_score_threshold),
        bet_min_expected_value: Number(form.bet_min_expected_value),
      });
      applyView(updated);
      setMessage("保存しました。次回のジョブ実行から反映されます。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!view && !error) return <div className="loading">読み込み中...</div>;

  return (
    <div className="settings-page">
      <h2>賭け設定(再起動不要)</h2>
      <ErrorNote message={error} />
      {message && <div className="info-note">{message}</div>}

      <div className="form">
        <label>
          <span>
            賭けモード
            {form.betting_mode === "prod" && <b className="danger-text"> 本番購入モード</b>}
          </span>
          <select
            value={form.betting_mode}
            onChange={(e) => setForm({ ...form, betting_mode: e.target.value })}
          >
            <option value="sim">sim(シミュレーション)</option>
            <option value="prod">prod(本番・実購入)</option>
          </select>
        </label>
        <label>
          <span>1件あたりの賭け金(円・100円単位)</span>
          <input
            type="number"
            min={100}
            step={100}
            value={form.bet_amount}
            onChange={(e) => setForm({ ...form, bet_amount: e.target.value })}
          />
        </label>
        <label>
          <span>賭けを行うAIスコアの下限(0-1)</span>
          <input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={form.bet_score_threshold}
            onChange={(e) => setForm({ ...form, bet_score_threshold: e.target.value })}
          />
        </label>
        <label>
          <span>賭けを行う期待値(score x odds)の下限</span>
          <input
            type="number"
            min={0}
            step={0.05}
            value={form.bet_min_expected_value}
            onChange={(e) => setForm({ ...form, bet_min_expected_value: e.target.value })}
          />
        </label>
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? "保存中..." : "保存"}
        </button>
      </div>

      {view && (
        <>
          <h2>環境設定(.env / 変更にはコンテナの再作成が必要)</h2>
          <table className="table settings-env-table">
            <thead>
              <tr>
                <th>項目</th>
                <th>環境変数</th>
                <th>現在値</th>
              </tr>
            </thead>
            <tbody>
              {view.env_settings.map((item) => (
                <tr key={item.key}>
                  <td>{item.label}</td>
                  <td className="env-key">{item.key}</td>
                  <td>
                    {item.key === "IPAT_DRY_RUN" && item.value === false ? (
                      <b className="danger-text">false(実購入あり)</b>
                    ) : (
                      String(item.value)
                    )}
                    {item.secret && <span className="muted"> (値は非表示)</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
