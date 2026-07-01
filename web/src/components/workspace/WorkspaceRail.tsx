// WorkspaceRail + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse, ChannelSummary, Snapshot, WorkspaceProject } from "../../api/types";
import { Bot, Boxes, CalendarClock, ChevronRight, GitFork, Gauge, Home, Inbox, ListTodo, Map as MapIcon, MessageSquare, Plus, Route, Settings, Trash2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { LiveState, PageId } from "../../app/sharedTypes";
import { allBoardTasks, channelIdOf, channelNameOf, isObservabilityPage, projectLabelFromId } from "../../app/shared";

interface RailNavItem {
  id: PageId;
  icon: LucideIcon;
  label: string;
  badge?: number;
}


function shouldShowRailActionResult(result: ActionResponse): boolean {
  if (result.action === "chat-orchestrator" && result.ok) return false;
  return true;
}


export function WorkspaceRail({
  actionResult,
  activePage,
  activeProjectId,
  channels,
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
  const projectOptions = inferredProject && !activeProjectListed
    ? [inferredProject, ...projects]
    : projects;
  const selectedProjectValue = activeProjectId || projectOptions[0]?.project_id || "";
  const selectedProject = projectOptions.find((project) => project.project_id === selectedProjectValue) ?? projectOptions[0] ?? null;
  const workspaceNav: RailNavItem[] = [
    { id: "project", icon: Home, label: "Overview" },
    { id: "inbox", icon: Inbox, label: "Inbox", badge: inboxPendingCount },
    { id: "board", icon: ListTodo, label: "Tasks" },
    { id: "agents", icon: Bot, label: "Agents" },
    { id: "automations", icon: CalendarClock, label: "Automations" },
  ];
  const measureNav: RailNavItem[] = [
    { id: "delivery", icon: Route, label: "Delivery" },
    { id: "delivery-trace", icon: MapIcon, label: "Trace" },
    { id: "delivery-graph", icon: Boxes, label: "Graph" },
    { id: "behavior-loop", icon: GitFork, label: "Loop" },
  ];
  const runtimeNav: RailNavItem[] = [
    { id: "runtime", icon: Gauge, label: "Runtime" },
    { id: "observability", icon: MapIcon, label: "Observability" },
  ];
  const systemNav: RailNavItem[] = [
    { id: "settings", icon: Settings, label: "Settings" },
  ];
  const railActivePage = isObservabilityPage(activePage) ? "observability" : activePage;

  return (
    <section className="panel project-rail" aria-label="Navigation rail">
      <div className="section-heading">
        <div>
          <h2>Control</h2>
          <span className="muted">{activeTasks.length} active tasks</span>
        </div>
        <span className={`status-dot ${liveState === "live" ? "ok" : "warn"}`} />
      </div>
      <div className="project-rail-body">
        <div className="rail-group workspace-context-group">
          <span className="rail-nav-title">Workspace</span>
          {projectOptions.length ? (
            <div className="project-switcher">
              <div className="project-switcher-row">
                <select
                  aria-label="Project"
                  title={selectedProject?.root || selectedProjectValue}
                  value={selectedProjectValue}
                  onChange={(event) => onSelectProject(event.target.value)}
                >
                  {projectOptions.map((project) => (
                    <option key={project.project_id} value={project.project_id}>
                      {project.name || project.project_id}
                    </option>
                  ))}
                </select>
                <button className="project-switcher-add" title="Add Project" type="button" onClick={onAddProject}>
                  <Plus aria-hidden="true" size={16} strokeWidth={1.8} />
                </button>
                <button
                  className="project-switcher-add danger"
                  title={removeDisabled
                    ? "Server default project — restart `zf web` to remove it"
                    : "Remove Project from Workspace"}
                  type="button"
                  disabled={removeDisabled}
                  onClick={onRemoveProject}
                >
                  <Trash2 aria-hidden="true" size={16} strokeWidth={1.8} />
                </button>
              </div>
            </div>
          ) : (
            <button className="rail-nav-button muted-action" type="button" onClick={onAddProject}>
              <span className="rail-nav-label">
                <Plus aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
                <span>Add Project</span>
              </span>
            </button>
          )}
          <RailNav title="" items={workspaceNav} activePage={railActivePage} onOpenPage={onOpenPage} />
        </div>
        <div className="rail-group">
          <span className="rail-nav-title">Measure</span>
          <RailNav title="" items={measureNav} activePage={railActivePage} onOpenPage={onOpenPage} />
        </div>
        <div className="rail-group">
          <span className="rail-nav-title">Channels</span>
          <div className="rail-nav channel-rail-list">
            {channels.length ? channels.slice(0, 8).map((channel) => (
              <button
                className={`rail-nav-button channel-nav-button ${activePage === "channels" && selectedChannelId === channelIdOf(channel) ? "active" : ""}`}
                key={channelIdOf(channel)}
                type="button"
                onClick={() => onOpenChannel(channelIdOf(channel))}
              >
                <span className="rail-nav-label">
                  <MessageSquare aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
                  <span>{channelNameOf(channel)}</span>
                </span>
                <span className="muted">{channel.members?.length ?? 0}</span>
            </button>
          )) : (
            <span className="empty-text">No channels</span>
          )}
          <button className="rail-nav-button muted-action" type="button" onClick={onNewChannel}>
            <span className="rail-nav-label">
              <Plus aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
              <span>New Channel</span>
            </span>
          </button>
        </div>
        </div>
        <details className="rail-section" open>
          <summary>
            <span>Operator</span>
            <ChevronRight className="rail-section-chevron" size={14} strokeWidth={1.9} aria-hidden="true" />
          </summary>
          <RailNav title="" items={runtimeNav} activePage={railActivePage} onOpenPage={onOpenPage} />
        </details>
        <details className="rail-section" open>
          <summary>
            <span>System</span>
            <ChevronRight className="rail-section-chevron" size={14} strokeWidth={1.9} aria-hidden="true" />
          </summary>
          <RailNav title="" items={systemNav} activePage={railActivePage} onOpenPage={onOpenPage} />
        </details>
        {actionResult && shouldShowRailActionResult(actionResult) ? (
          <p className="empty-text compact-error">{actionResult.status}: {actionResult.reason}</p>
        ) : null}
      </div>
    </section>
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
  return (
    <div className="rail-nav">
      {title ? <span className="rail-nav-title">{title}</span> : null}
      {items.map((item) => (
        <button
          className={`rail-nav-button ${activePage === item.id ? "active" : ""}`}
          key={item.id}
          type="button"
          onClick={() => onOpenPage(item.id)}
        >
          <span className="rail-nav-label">
            <item.icon aria-hidden="true" className="rail-nav-icon" size={16} strokeWidth={1.8} />
            <span>{item.label}</span>
          </span>
          {item.badge ? <span className="rail-nav-badge">{item.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}
