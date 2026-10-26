import { useUiPreferences } from "../../app/UiPreferences";
// WorkspaceRail + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse, ChannelSummary, Snapshot, WorkspaceProject } from "../../api/types";
import { Bot, CalendarClock, ChevronRight, Gauge, GitFork, Home, Inbox, ListTodo, Menu, MessageSquare, PanelLeft, Plus, Radio, Route, Settings, Trash2, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { LiveState, PageId } from "../../app/sharedTypes";
import { allBoardTasks, channelIdOf, channelNameOf, isObservabilityPage, projectLabelFromId } from "../../app/shared";

interface RailNavItem {
  id: PageId;
  icon: LucideIcon;
  label: string;
  badge?: number;
}

const COLLAPSED_KEY = "zf.sidebarCollapsed.v1";

function shouldShowRailActionResult(result: ActionResponse): boolean {
  if (result.action === "chat-orchestrator" && result.ok) return false;
  return true;
}


export function WorkspaceRail({
  actionResult,
  activePage,
  activeProjectId,
  channels,
  channelsLoaded,
  inboxPendingCount = 0,
  liveState,
  onAddProject,
  onNewChannel,
  onOpenChannel,
  onOpenPage,
  onRemoveProject,
  onSelectProject,
  projects,
  removeDisabled = false,
  selectedChannelId,
  snapshot,
}: {
  actionResult: ActionResponse | null;
  activePage: PageId;
  activeProjectId: string;
  channels: ChannelSummary[];
  channelsLoaded: boolean;
  inboxPendingCount?: number;
  liveState: LiveState;
  onAddProject: () => void;
  onNewChannel: () => void;
  onOpenChannel: (channelId: string) => void;
  onOpenPage: (page: PageId) => void;
  onRemoveProject: () => void;
  onSelectProject: (projectId: string) => void;
  projects: WorkspaceProject[];
  removeDisabled?: boolean;
  selectedChannelId: string;
  snapshot: Snapshot | null;
}) {
  const { t } = useUiPreferences();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === "true"; }
    catch { return false; }
  });
  const [systemOpen, setSystemOpen] = useState(true);
  const railRef = useRef<HTMLElement>(null);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    try { localStorage.setItem(COLLAPSED_KEY, String(next)); }
    catch { /* Navigation still works when browser storage is unavailable. */ }
  };
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 981px)");
    const closeOnDesktop = () => { if (desktop.matches) setMobileOpen(false); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);
  useEffect(() => {
    if (!mobileOpen) return;
    const rail = railRef.current;
    const workspace = rail?.parentElement;
    const background = [...(workspace?.children ?? []), ...(workspace?.parentElement?.children ?? [])]
      .filter((node): node is HTMLElement => node instanceof HTMLElement && node !== rail && node !== workspace && !node.classList.contains("rail-scrim"));
    const priorInert = background.map((node) => node.inert);
    background.forEach((node) => { node.inert = true; });
    mobileToggleRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileOpen(false);
      }
      if (event.key !== "Tab") return;
      const items = [...(rail?.querySelectorAll<HTMLElement>("button:not([disabled]), select:not([disabled]), summary, [tabindex='0']") ?? [])]
        .filter((item) => item.getClientRects().length > 0);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      background.forEach((node, index) => { node.inert = priorInert[index]; });
      document.removeEventListener("keydown", onKey);
      mobileToggleRef.current?.focus();
    };
  }, [mobileOpen]);
  const openPage = (nextPage: PageId) => {
    onOpenPage(nextPage);
    setMobileOpen(false);
  };
  const openChannel = (channelId: string) => {
    onOpenChannel(channelId);
    setMobileOpen(false);
  };
  const tasks = snapshot ? allBoardTasks(snapshot) : [];
  const activeTasks = tasks.filter((task) => task.status !== "done" && task.status !== "cancelled");
  const inferredProject: WorkspaceProject | null = activeProjectId ? {
    project_id: activeProjectId,
    name: snapshot?.project.name || projectLabelFromId(activeProjectId),
    root: snapshot?.project.root || "",
    config_path: "",
    state_dir_hint: snapshot?.project.state_dir || "",
    state_dir_resolved: snapshot?.project.state_dir || "",
  } : null;
  const activeProjectListed = Boolean(activeProjectId && projects.some((project) => project.project_id === activeProjectId));
  const rawProjectOptions = inferredProject && !activeProjectListed
    ? [inferredProject, ...projects]
    : projects;
  // The server-default project and its registry entry can both appear with the
  // same name+root ("zaofu" twice in the picker). Keep the first occurrence.
  const seenProjectKeys = new Set<string>();
  const projectOptions = rawProjectOptions.filter((project) => {
    const key = `${project.name || project.project_id}::${project.root || ""}`;
    if (seenProjectKeys.has(key)) return false;
    seenProjectKeys.add(key);
    return true;
  });
  const selectedProjectValue = activeProjectId || projectOptions[0]?.project_id || "";
  const selectedProject = projectOptions.find((project) => project.project_id === selectedProjectValue) ?? projectOptions[0] ?? null;
  // 2026-07-11 operator re-decision(推翻 doc116 §6 的四项保留):Playwright
  // 实测三页顶部 40%(feature 选择器+cockpit hero)逐像素相同,Stage Heatmap
  // 在 Trace/Graph 双渲染 —— Delivery/Trace/Graph 收敛为单导航项 + 页内
  // mode tab(delivery-trace/delivery-graph 深链仍有效,见 railActivePage)。
  // Act vs watch: Workspace = what I do (decide/assign/intervene);
  // Monitoring = what the system is doing (delivery + deep-dive).
  const workspaceNav: RailNavItem[] = [
    { id: "project", icon: Home, label: "Overview" },
    { id: "inbox", icon: Inbox, label: "Inbox", badge: inboxPendingCount },
    { id: "board", icon: ListTodo, label: "Tasks" },
    { id: "workflows", icon: GitFork, label: "Workflows" },
    { id: "agents", icon: Bot, label: "Agents" },
    { id: "automations", icon: CalendarClock, label: "Automations" },
  ];
  const monitoringNav: RailNavItem[] = [
    { id: "delivery", icon: Route, label: "Delivery" },
    { id: "behavior-loop", icon: GitFork, label: "Loop" },
    { id: "traces", icon: Radio, label: "Traces" },
  ];
  const systemNav: RailNavItem[] = [
    { id: "settings", icon: Settings, label: "Settings" },
  ];
  const railActivePage = isObservabilityPage(activePage)
    ? "traces"
    : activePage === "runtime" || activePage === "traces" || activePage === "diagnostics"
      ? "traces"
      : activePage === "delivery-trace" || activePage === "delivery-graph"
        ? "delivery"
        : activePage;

  return (
    <>
    {mobileOpen && <button className="rail-scrim" aria-label={t("Close navigation")} tabIndex={-1} type="button" onClick={() => setMobileOpen(false)} />}
    <section ref={railRef} className={`panel project-rail ${mobileOpen ? "mobile-open" : ""} ${collapsed ? "is-collapsed" : ""}`} aria-label="Navigation rail" role={mobileOpen ? "dialog" : "region"} aria-modal={mobileOpen || undefined}>
      <div className="section-heading">
        <div>
          <h2 className="rail-brand" aria-label="ZaoFu">
            <span className="rail-brand-mark" aria-hidden="true">Z</span>
            <span className="rail-brand-name">ZaoFu</span>
          </h2>
          {/* count removed: the unified header is the single count source (doc116 §11.2) */}
        </div>
        <div className="rail-heading-actions">
          <span className={`status-dot ${liveState === "live" ? "ok" : "warn"}`} />
          <button
            aria-controls="workspace-navigation"
            aria-expanded={!collapsed}
            aria-label={t(collapsed ? "Expand sidebar" : "Collapse sidebar")}
            title={t(collapsed ? "Expand sidebar" : "Collapse sidebar")}
            className="rail-desktop-toggle"
            type="button"
            onClick={toggleCollapsed}
          >
            <PanelLeft aria-hidden="true" size={16} strokeWidth={1.8} />
          </button>
          <button
            ref={mobileToggleRef}
            aria-controls="workspace-navigation"
            aria-expanded={mobileOpen}
            aria-label={t(mobileOpen ? "Close navigation" : "Open navigation")}
            className="rail-mobile-toggle"
            type="button"
            onClick={() => setMobileOpen((open) => !open)}
          >
            {mobileOpen ? <X aria-hidden="true" size={17} /> : <Menu aria-hidden="true" size={17} />}
          </button>
        </div>
      </div>
      <div className="project-rail-body" id="workspace-navigation">
        <div className="rail-group workspace-context-group">
          <span className="rail-nav-title">{t("Workspace")}</span>
          {projectOptions.length ? (
            <div className="project-switcher">
              <div className="project-switcher-row">
                <select
                  aria-label="Project"
                  title={selectedProject?.root || selectedProjectValue}
                  value={selectedProjectValue}
                  onChange={(event) => {
                    onSelectProject(event.target.value);
                    setMobileOpen(false);
                  }}
                >
                  {projectOptions.map((project) => (
                    <option key={project.project_id} value={project.project_id}>
                      {project.name || project.project_id}
                    </option>
                  ))}
                </select>
                <button className="project-switcher-add" title="Add Project" type="button" onClick={() => { setMobileOpen(false); onAddProject(); }}>
                  <Plus aria-hidden="true" size={16} strokeWidth={1.8} />
                </button>
                <button
                  className="project-switcher-add danger"
                  title={removeDisabled
                    ? "Server default project — restart `zf web` to remove it"
                    : "Remove Project from Workspace"}
                  type="button"
                  disabled={removeDisabled}
                  onClick={() => { setMobileOpen(false); onRemoveProject(); }}
                >
                  <Trash2 aria-hidden="true" size={16} strokeWidth={1.8} />
                </button>
              </div>
            </div>
          ) : (
            <button className="rail-nav-button muted-action" aria-label={t("Add Project")} title={t("Add Project")} type="button" onClick={() => { setMobileOpen(false); onAddProject(); }}>
              <span className="rail-nav-label">
                <Plus aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
                <span>{t("Add Project")}</span>
              </span>
            </button>
          )}
          <RailNav title="" items={workspaceNav} activePage={railActivePage} onOpenPage={openPage} />
        </div>
        <div className="rail-group">
          <span className="rail-nav-title">{t("Monitoring")}</span>
          <RailNav title="" items={monitoringNav} activePage={railActivePage} onOpenPage={openPage} />
        </div>
        <div className="rail-group">
          <span className="rail-nav-title">{t("Channels")}</span>
          <div className="rail-nav channel-rail-list">
            {channels.length ? channels.slice(0, 8).map((channel) => (
              <button
                className={`rail-nav-button channel-nav-button ${activePage === "channels" && selectedChannelId === channelIdOf(channel) ? "active" : ""}`}
                key={channelIdOf(channel)}
                aria-label={channelNameOf(channel)}
                title={channelNameOf(channel)}
                type="button"
                onClick={() => openChannel(channelIdOf(channel))}
              >
                <span className="rail-nav-label">
                  <MessageSquare aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
                  <span>{channelNameOf(channel)}</span>
                </span>
                <span className="muted">{channel.members?.length ?? 0}</span>
            </button>
          )) : channelsLoaded ? (
            <span className="empty-text">No channels</span>
          ) : (
            <button className="rail-nav-button" aria-label={t("Open Channels")} title={t("Open Channels")} type="button" onClick={() => openPage("channels")}>
              <span className="rail-nav-label">
                <MessageSquare aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
                <span>{t("Open Channels")}</span>
              </span>
            </button>
          )}
          <button className="rail-nav-button muted-action" aria-label={t("New Channel")} title={t("New Channel")} type="button" onClick={() => { setMobileOpen(false); onNewChannel(); }}>
            <span className="rail-nav-label">
              <Plus aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
              <span>{t("New Channel")}</span>
            </span>
          </button>
        </div>
        </div>
        <details className="rail-section" open={(collapsed && !mobileOpen) || systemOpen}>
          <summary onClick={(event) => { event.preventDefault(); setSystemOpen((open) => !open); }}>
            <span>{t("System settings")}</span>
            <ChevronRight className="rail-section-chevron" size={14} strokeWidth={1.9} aria-hidden="true" />
          </summary>
          <RailNav title="" items={systemNav} activePage={railActivePage} onOpenPage={openPage} />
        </details>
        {actionResult && shouldShowRailActionResult(actionResult) ? (
          <p className="empty-text compact-error">{actionResult.status}: {actionResult.reason}</p>
        ) : null}
      </div>
    </section>
    </>
  );
}


function RailNav({
  activePage,
  items,
  onOpenPage,
  title,
}: {
  activePage: PageId;
  items: RailNavItem[];
  onOpenPage: (page: PageId) => void;
  title: string;
}) {
  const { t } = useUiPreferences();
  return (
    <div className="rail-nav">
      {title ? <span className="rail-nav-title">{t(title)}</span> : null}
      {items.map((item) => (
        <button
          className={`rail-nav-button ${activePage === item.id ? "active" : ""}`}
          key={item.id}
          aria-label={t(item.label)}
          aria-current={activePage === item.id ? "page" : undefined}
          title={t(item.label)}
          type="button"
          onClick={() => onOpenPage(item.id)}
        >
          <span className="rail-nav-label">
            <item.icon aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
            <span>{t(item.label)}</span>
          </span>
          {item.badge ? <span className="rail-nav-badge">{item.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}
