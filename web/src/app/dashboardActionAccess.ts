import type { RuntimeSummary } from "../api/types";

export function dashboardActionAccess(session: RuntimeSummary["web_session"] | null, tokenPresent: boolean) {
  const mode = session?.mode;
  const account = mode === "local_account" || mode === "controller_session"
    || session?.local_account || session?.controller_managed;
  const legacyToken = !account && mode !== "read_only"
    && (mode === "token_required" || Boolean(session?.token_fallback_enabled));
  const hasToken = legacyToken && tokenPresent;
  const sessionActionReady = Boolean(session?.actions_enabled) && mode !== "read_only";
  const actionReady = sessionActionReady || hasToken;
  const passcodeRequired = mode === "remote_passcode" && !sessionActionReady;
  const showTokenRow = legacyToken && !actionReady;
  const mutationEnabled = actionReady || passcodeRequired || showTokenRow;
  const actionState = actionReady ? "active" : passcodeRequired ? "passcode needed"
    : showTokenRow ? "token needed" : mode ? "read only" : "connecting";
  return { actionReady, actionState, mutationEnabled, passcodeRequired, sessionActionReady,
    showTokenRow, hasToken, mode };
}
