import { useState } from "react";

export function AccountControls({ local, cloud }: { local: boolean; cloud: boolean }) {
  const [error, setError] = useState("");
  if (local) return <a className="filter-button" href="/auth/local/account">本地账号</a>;
  if (!cloud) return null;

  async function signOut() {
    try {
      const state = await fetch("/auth/session", { cache: "no-store" });
      if (state.status === 401) { window.location.reload(); return; }
      if (!state.ok) throw new Error();
      const { csrf_token } = await state.json();
      const response = await fetch("/auth/logout", {
        method: "POST", headers: { Accept: "application/json", "X-CSRF-Token": csrf_token },
      });
      if (!response.ok) throw new Error();
      window.location.assign((await response.json()).logout_url);
    } catch { setError("退出失败，请重试"); }
  }

  return <><button type="button" onClick={() => void signOut()}>退出登录</button>
    {error && <span role="alert">{error}</span>}</>;
}
