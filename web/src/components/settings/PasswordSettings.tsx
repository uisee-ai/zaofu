import { useState } from "react";
import type { FormEvent } from "react";
import { useUiPreferences } from "../../app/UiPreferences";

export function PasswordSettings({ writable }: { writable: boolean }) {
  const { t } = useUiPreferences();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (data.get("password") !== data.get("confirm")) {
      setError(t("New passwords do not match."));
      event.currentTarget.querySelector<HTMLInputElement>("[name=confirm]")?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/auth/local/password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current: data.get("current"), password: data.get("password") }),
      });
      if (response.ok || response.status === 401) { window.location.replace("/"); return; }
      const body = await response.json();
      setError(body.error || t("Could not change password."));
    } catch { setError(t("Could not change password.")); }
    finally { setBusy(false); }
  }

  if (!writable) return <p className="settings-description">{t("Password changes require write access.")}</p>;
  return <form className="settings-password-form" onSubmit={save}>
    <p className="settings-description">{t("Changing your password signs you out of all sessions.")}</p>
    <label>{t("Current password")}<input type="password" name="current" autoComplete="current-password" required /></label>
    <label>{t("New password")}<input type="password" name="password" autoComplete="new-password" required /></label>
    <label>{t("Confirm new password")}<input type="password" name="confirm" autoComplete="new-password" required /></label>
    {error && <p role="alert">{error}</p>}
    <button type="submit" className="primary-action" disabled={busy}>{t("Update password")}</button>
  </form>;
}
