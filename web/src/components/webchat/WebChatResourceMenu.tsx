import {
  Activity,
  Package,
  Search,
  Settings2,
} from "lucide-react";
import type { ReactNode } from "react";
import type { WebChatToolCatalog } from "../../api/types";

export type WebChatDrawerKind = "conversation" | "artifacts" | "activity" | "profile" | "";

export function WebChatResourceMenu({
  active,
  activityCount,
  artifactCount,
  onChange,
  query,
  toolCatalog,
}: {
  active: WebChatDrawerKind;
  activityCount: number;
  artifactCount: number;
  onChange: (kind: WebChatDrawerKind) => void;
  query: string;
  toolCatalog?: WebChatToolCatalog;
}) {
  function select(kind: Exclude<WebChatDrawerKind, "">) {
    onChange(active === kind ? "" : kind);
  }

  function item(
    kind: Exclude<WebChatDrawerKind, "">,
    label: string,
    description: string,
    icon: ReactNode,
    count?: number,
  ) {
    if (query && !`${label} ${description}`.toLowerCase().includes(query)) return null;
    return (
      <button
        aria-pressed={active === kind}
        className={active === kind ? "active" : ""}
        type="button"
        onClick={() => select(kind)}
      >
        {icon}
        <span>
          <strong>{label}</strong>
          <small>{description}</small>
        </span>
        {count ? <em>{count}</em> : null}
      </button>
    );
  }

  return (
    <>
          {!query ? <div className="webchat-resource-menu-title">Tools and resources</div> : null}
          {item("conversation", "Conversation", "Search, export, fork, or correct", <Search size={17} />)}
          {item("artifacts", "Artifacts", "Inspect and adopt durable outputs", <Package size={17} />, artifactCount)}
          {item("activity", "Activity", "Follow child agents and controls", <Activity size={17} />, activityCount)}
          {item("profile", "Agent profile", "Provider and permission proposal", <Settings2 size={17} />)}
          {(!query || toolCatalog?.items.some((tool) => `${tool.name} ${tool.description} ${tool.tool_class}`.toLowerCase().includes(query))) ? <details className="webchat-resource-menu-tools" open={query ? true : undefined}>
            <summary>Tool catalog</summary>
            <p>Provider-native tools: availability not confirmed by this catalog.</p>
            <p>External tools and MCP: availability not confirmed by this catalog.</p>
            <p>ZaoFu host policy catalog (not a Provider-native allowlist):</p>
            {toolCatalog?.items.filter((tool) => !query || `${tool.name} ${tool.description} ${tool.tool_class}`.toLowerCase().includes(query)).map((tool) => (
              <div key={tool.name}>
                <strong>{tool.name}</strong> · {tool.tool_class} · policy {tool.effective_policy}
                <small>{tool.description}</small>
              </div>
            ))}
            {toolCatalog && !toolCatalog.items.length ? <p>No catalog entries.</p> : null}
          </details> : null}
    </>
  );
}

