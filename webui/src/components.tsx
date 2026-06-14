import { useEffect, useState } from "react";

const STATUS_LABELS: Record<string, string> = {
  queued: "待機中",
  running: "実行中",
  success: "成功",
  failed: "失敗",
  pending: "未確認",
  placed: "購入済み",
  cancelled: "キャンセル",
  dry_run: "dry-run",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export function ModeBadge({ mode }: { mode: string }) {
  return (
    <span className={`badge ${mode === "prod" ? "badge-prod" : "badge-sim"}`}>
      {mode === "prod" ? "本番" : "シミュレーション"}
    </span>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="error-note">エラー: {message}</div>;
}

/** loaderを定期実行してデータを保持するフック(画面の自動更新用) */
export function usePolling<T>(loader: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      loader()
        .then((d) => {
          if (active) {
            setData(d);
            setError(null);
          }
        })
        .catch((e) => {
          if (active) setError(e instanceof Error ? e.message : String(e));
        });
    load();
    const timer = setInterval(load, intervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error };
}
