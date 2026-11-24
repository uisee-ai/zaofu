import { ChevronsUpDown, KeyRound, LogOut, Monitor, Moon, Sun, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useUiPreferences } from "../../app/UiPreferences";

export function AccountControls({ username, local }: { username?: string; local: boolean }) {
  const { t, preferences, updatePreferences } = useUiPreferences();
  const details = useRef<HTMLDetailsElement>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const accountType = t(local ? "Local account" : "Cloud account");
  const label = username || accountType;
  const avatar = <span className="account-avatar" aria-hidden="true">
    {username ? Array.from(username)[0].toUpperCase() : <UserRound size={15} />}
  </span>;

  useEffect(() => {
    function dismiss(event: PointerEvent) {
      if (details.current?.open && !details.current.contains(event.target as Node)) details.current.open = false;
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== "Escape" || !details.current?.open) return;
      event.preventDefault();
      event.stopPropagation();
      details.current.open = false;
      details.current.querySelector("summary")?.focus();
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape, true);
    };
  }, []);

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

  return <details ref={details} className="account-switcher">
    <summary aria-label={label} title={label}>
      {avatar}
      <span className="account-name">{label}</span><ChevronsUpDown className="account-chevron" size={14} aria-hidden="true" />
    </summary>
    <div className="account-options">
      <div className="account-identity">
        {avatar}
        <div><strong>{label}</strong>{username && <small>{accountType}</small>}</div>
      </div>
      <fieldset className="account-theme">
        <legend>{t("Theme")}</legend>
        <div>{([
          ["light", "Light", Sun], ["dark", "Dark", Moon], ["system", "System", Monitor],
        ] as const).map(([mode, name, Icon]) => (
          <button key={mode} type="button" aria-pressed={preferences.theme === mode}
            onClick={() => updatePreferences({theme: mode})}>
            <Icon size={13} aria-hidden="true" />{t(name)}
          </button>
        ))}</div>
      </fieldset>
      <div className="account-actions">
        {local && <a href="/?page=settings&settings=password"><KeyRound size={15} />{t("Password")}</a>}
        <button type="button" disabled={busy} onClick={() => void signOut()}><LogOut size={15} />{t("Sign out")}</button>
      </div>
      {error && <span role="alert">{error}</span>}
    </div>
  </details>;
}
