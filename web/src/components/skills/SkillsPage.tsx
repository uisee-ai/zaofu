// SkillsPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { search } from "../../api/client";
import type { SkillsSummary } from "../../api/types";
import { Bell, Boxes, FileText, Users, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ProjectionMetricSpec } from "../../app/sharedTypes";
import { ProjectionMetricGrid, TablePage, asRecord, compactPath, stringify, textValue } from "../../app/shared";

export function SkillsPage({ summary }: { summary: SkillsSummary | null }) {
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [activeTab, setActiveTab] = useState("loaded");
  const loadedRows = useMemo(() => (
    (summary?.loaded ?? summary?.lock ?? [])
      .map((row) => asRecord(row))
      .sort((left, right) => (
        `${textValue(left.role)} ${textValue(left.name)}`.localeCompare(
          `${textValue(right.role)} ${textValue(right.name)}`,
        )
      ))
  ), [summary]);
  const enabledRows = useMemo(() => (summary?.enabled ?? []).map((row) => asRecord(row)), [summary]);
  const poolRows = useMemo(() => (summary?.pool ?? []).map((row) => asRecord(row)), [summary]);
  const manifestRows = useMemo(() => (summary?.manifests ?? []).map((row) => asRecord(row)), [summary]);
  const warningRows = useMemo(() => (summary?.warnings ?? []).map((row) => asRecord(row)), [summary]);
  const roles = useMemo(() => (
    [...new Set([...loadedRows, ...enabledRows].map((row) => textValue(row.role) || textValue(row.role_name)).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right))
  ), [enabledRows, loadedRows]);
  const sources = useMemo(() => (
    [...new Set([...loadedRows, ...poolRows, ...manifestRows].map((row) => textValue(row.source) || textValue(row.path)).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right))
  ), [loadedRows, manifestRows, poolRows]);
  const needle = query.trim().toLowerCase();

  function filterSkillRows(rows: Record<string, unknown>[]): Record<string, unknown>[] {
    return rows.filter((row) => {
      const role = textValue(row.role) || textValue(row.role_name);
      const source = textValue(row.source) || textValue(row.path);
      if (roleFilter !== "all" && role !== roleFilter) return false;
      if (sourceFilter !== "all" && source !== sourceFilter) return false;
      if (!needle) return true;
      return stringify(row).toLowerCase().includes(needle);
    });
  }

  const skillTabs = [
    {
      id: "loaded",
      title: "Loaded Skills",
      rows: filterSkillRows(loadedRows),
      total: loadedRows.length,
      empty: {
        title: "No loaded skills",
        description: "Loaded skills appear after the runtime materializes role-specific skill lock entries.",
        icon: Wrench,
        compact: true,
      },
    },
    {
      id: "enabled",
      title: "Enabled By Role",
      rows: filterSkillRows(enabledRows),
      total: enabledRows.length,
      empty: {
        title: "No role skill bindings",
        description: "Role bindings appear when zf.yaml enables skills for runtime roles.",
        icon: Users,
        compact: true,
      },
    },
    {
      id: "pool",
      title: "Skill Pool",
      rows: filterSkillRows(poolRows),
      total: poolRows.length,
      empty: {
        title: "No skill pool rows",
        description: "The configured skill pool has no projected skill manifest rows for this project.",
        icon: Boxes,
        compact: true,
      },
    },
    {
      id: "manifests",
      title: "Skill Manifests",
      rows: filterSkillRows(manifestRows),
      total: manifestRows.length,
      empty: {
        title: "No skill manifests",
        description: "Manifest projections appear when local or repo skills are discovered.",
        icon: FileText,
        compact: true,
      },
    },
    {
      id: "warnings",
      title: "Warnings",
      rows: filterSkillRows(warningRows),
      total: warningRows.length,
      empty: {
        title: "No skill warnings",
        description: "Skill discovery and materialization warnings will be surfaced here.",
        icon: Bell,
        compact: true,
      },
    },
  ];
  const activeSkillTab = skillTabs.find((tab) => tab.id === activeTab) ?? skillTabs[0];
  const filteredLoaded = filterSkillRows(loadedRows);
  const metrics: ProjectionMetricSpec[] = [
    { icon: Wrench, label: "Loaded", value: loadedRows.length, meta: `${filteredLoaded.length} visible`, tone: loadedRows.length ? "info" : "muted" },
    { icon: Users, label: "Roles", value: roles.length, meta: "skill-enabled roles", tone: roles.length ? "info" : "muted" },
    { icon: Boxes, label: "Pool", value: poolRows.length, meta: compactPath(summary?.pool_path ?? "") || "no pool path", tone: poolRows.length ? "info" : "muted" },
    { icon: FileText, label: "Manifests", value: manifestRows.length, meta: textValue(summary?.lock_file) || "no lock file", tone: manifestRows.length ? "info" : "muted" },
    { icon: Bell, label: "Warnings", value: warningRows.length, meta: textValue(summary?.materialize) || "materialize state", tone: warningRows.length ? "warn" : "ok" },
  ];

  const roleDisabled = roles.length === 0;
  const sourceDisabled = sources.length === 0;
  useEffect(() => {
    if (roleDisabled && roleFilter !== "all") setRoleFilter("all");
  }, [roleDisabled, roleFilter]);
  useEffect(() => {
    if (sourceDisabled && sourceFilter !== "all") setSourceFilter("all");
  }, [sourceDisabled, sourceFilter]);

  return (
    <div className="skills-page-shell">
      <div className="section-heading skills-heading">
        <div>
          <h2>Skills</h2>
          <span className="muted">{compactPath(summary?.pool_path ?? "") || "runtime skill projection"}</span>
        </div>
        <div className="filter-row">
          <input
            className="filter-input skills-search"
            placeholder="skill, role, source"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)} disabled={roleDisabled}>
            <option value="all">all roles</option>
            {roles.map((role) => <option value={role} key={role}>{role}</option>)}
          </select>
          <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} disabled={sourceDisabled}>
            <option value="all">all sources</option>
            {sources.map((source) => <option value={source} key={source}>{compactPath(source)}</option>)}
          </select>
          {summary?.materialize ? <span className="metric-chip">{summary.materialize}</span> : null}
        </div>
      </div>
      <ProjectionMetricGrid className="skills-summary-grid" metrics={metrics} />
      {warningRows.length ? (
        <section className="subsection skills-warning-panel">
          <div className="inline-heading">
            <h3>Attention</h3>
            <span className="muted">{warningRows.length} warnings</span>
          </div>
          <div className="skills-warning-list">
            {warningRows.slice(0, 3).map((warning, index) => (
              <div className="skills-warning-item" key={`${textValue(warning.name) || "warning"}-${index}`}>
                <span className="badge badge-warn">warning</span>
                <span>{textValue(warning.message) || textValue(warning.warning) || stringify(warning)}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      <section className="subsection skills-tab-panel">
        <div className="tab-row compact-tabs skills-tabs" aria-label="Skill projection tabs">
          {skillTabs.map((tab) => (
            <button
              className={`tab-button ${tab.id === activeSkillTab.id ? "active" : ""}`}
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.title} <span className="muted">{tab.total}</span>
            </button>
          ))}
        </div>
        <TablePage
          title={activeSkillTab.title}
          rows={activeSkillTab.rows}
          embedded
          emptyState={activeSkillTab.empty}
        />
      </section>
    </div>
  );
}


