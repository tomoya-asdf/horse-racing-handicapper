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
      setMessage("保存しました。次回のジョブ実行から反映されます(再起動不要)。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!view && !error) return <div className="loading">読み込み中...</div>;

  return (
    <div className="settings-page">
      <h2>賭け設定(再起動不要で反映)</h2>
      <ErrorNote message={error} />
      {message && <div className="info-note">{message}</div>}

      <div className="form">
        <label>
          <span>
            賭けモード
            {form.betting_mode === "prod" && <b className="danger-text"> ※実購入モード</b>}
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
          <span>1件あたりの賭け金額(円・100円単位)</span>
          <input
            type="number"
            min={100}
            step={100}
            value={form.bet_amount}
            onChange={(e) => setForm({ ...form, bet_amount: e.target.value })}
          />
        </label>
        <label>
          <span>賭けを行う予測スコアの下限(0〜1)</span>
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
          <span>賭けを行う期待値(スコア×オッズ)の下限</span>
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
          <h2>環境設定(.env / 変更にはコンテナの再起動が必要)</h2>
          <table className="table table-narrow">
            <tbody>
              <tr>
                <td>データ収集間隔</td>
                <td>{view.readonly.collect_interval_minutes} 分</td>
              </tr>
              <tr>
                <td>予測・決済間隔</td>
                <td>{view.readonly.predict_interval_minutes} 分</td>
              </tr>
              <tr>
                <td>スクレイピング間隔</td>
                <td>{view.readonly.scraper_request_interval_seconds} 秒</td>
              </tr>
              <tr>
                <td>IPATドライラン</td>
                <td>
                  {view.readonly.ipat_dry_run ? (
                    "有効(実際の購入は行わない)"
                  ) : (
                    <b className="danger-text">無効(実際に購入する)</b>
                  )}
                </td>
              </tr>
              <tr>
                <td>IPAT認証情報</td>
                <td>{view.readonly.ipat_credentials_configured ? "設定済み" : "未設定"}</td>
              </tr>
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
