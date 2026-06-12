async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) message = String(body.detail);
    } catch {
      /* JSONでないエラーはステータス表示のみ */
    }
    throw new Error(message);
  }
  return res.json();
}

export function getJSON<T>(url: string): Promise<T> {
  return fetch(url).then((res) => handle<T>(res));
}

export function postJSON<T>(url: string, body?: unknown): Promise<T> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((res) => handle<T>(res));
}

export function putJSON<T>(url: string, body: unknown): Promise<T> {
  return fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((res) => handle<T>(res));
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes()
  ).padStart(2, "0")}`;
}

export function formatYen(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${Math.round(value).toLocaleString()} 円`;
}
