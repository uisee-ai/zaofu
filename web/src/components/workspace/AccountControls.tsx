import { ChevronsUpDown, KeyRound, LogOut, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useUiPreferences } from "../../app/UiPreferences";

export function AccountControls({ username, local }: { username?: string; local: boolean }) {
  const { t } = useUiPreferences();
  const details = useRef<HTMLDetailsElement>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const label = username || t("Local account");

  useEffect(() => {
    if (!local) return;
    function dismiss(event: PointerEvent) {
      if (details.current?.open && !details.current.contains(event.target as Node)) details.current.open = false;
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && details.current?.open) {
        details.current.open = false;
        details.current.querySelector("summary")?.focus();
      }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", onKey);
    };
  }, [local]);

  async function signOut() {
    setError("");
    setBusy(true);
    try {
      if (local) {
        const response = await fetch("/auth/local/logout", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        if (!response.ok && response.status !== 401) throw new Error();
        window.location.replace("/");
        return;
      }
      const state = await fetch("/auth/session", { cache: "no-store" });
      if (state.status === 401) { window.location.reload(); return; }
      if (!state.ok) throw new Error();
      const { csrf_token } = await state.json();
      const response = await fetch("/auth/logout", {
        method: "POST", headers: { Accept: "application/json", "X-CSRF-Token": csrf_token },
      });
      if (!response.ok) throw new Error();
      window.location.assign((await response.json()).logout_url);
    } catch { setError(t("Sign out failed. Try again.")); setBusy(false); }
  }

  if (!local) return <><button type="button" disabled={busy} onClick={() => void signOut()}>退出登录</button>
    {error && <span role="alert">{error}</span>}</>;
  return <details ref={details} className="account-switcher">
    <summary aria-label={label} title={label}>
      <span className="account-avatar" aria-hidden="true">{username ? username[0].toUpperCase() : <UserRound size={15} />}</span>
      <span className="account-name">{label}</span><ChevronsUpDown className="account-chevron" size={14} aria-hidden="true" />
    </summary>
    <div className="account-options">
      <a href="/?page=settings&settings=password"><KeyRound size={15} />{t("Password")}</a>
      <button type="button" disabled={busy} onClick={() => void signOut()}><LogOut size={15} />{t("Sign out")}</button>
      {error && <span role="alert">{error}</span>}
    </div>
  </details>;
}
