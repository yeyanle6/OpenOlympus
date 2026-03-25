import { useState, useEffect } from "react";

interface PhaseInfo {
  name: string;
  protocol: string;
  agents: string[];
  model_tier: string;
  human_role: string;
  status: string;
}

interface ProjectInfo {
  project_id: string;
  name: string;
  template: string;
  current_phase: number;
  total_phases: number;
  current_phase_name: string;
  status: string;
  phases: PhaseInfo[];
  results: { phase_name: string; status: string; room_id: string; summary: string }[];
}

const PHASE_ICONS: Record<string, string> = {
  requirements: "📋",
  architecture: "🏗️",
  decomposition: "📦",
  execution: "⚙️",
  testing: "🧪",
  research: "🔬",
  analysis: "📊",
  planning: "📐",
};

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  active: { bg: "bg-green-100", text: "text-green-700", label: "Running" },
  completed: { bg: "bg-blue-100", text: "text-blue-700", label: "Done" },
  pending: { bg: "bg-gray-100", text: "text-gray-500", label: "Pending" },
  failed: { bg: "bg-red-100", text: "text-red-700", label: "Failed" },
  waiting_approval: { bg: "bg-yellow-100", text: "text-yellow-700", label: "Needs Approval" },
  rejected: { bg: "bg-orange-100", text: "text-orange-700", label: "Rejected" },
};

const HUMAN_LABELS: Record<string, string> = {
  active: "You lead",
  observe: "You watch",
  approve: "You approve",
  none: "Autonomous",
};

interface Props {
  onSelectRoom: (roomId: string) => void;
}

export function WorkflowView({ onSelectRoom }: Props) {
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [templates, setTemplates] = useState<Record<string, PhaseInfo[]>>({});
  const [newName, setNewName] = useState("");
  const [newTemplate, setNewTemplate] = useState("standard");
  const [loading, setLoading] = useState(false);

  // Fetch projects and templates
  useEffect(() => {
    const load = async () => {
      try {
        const [p, t] = await Promise.all([
          fetch("/workflows").then((r) => r.json()),
          fetch("/workflows/templates").then((r) => r.json()),
        ]);
        setProjects(p);
        setTemplates(t);
      } catch { /* ignore */ }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const createProject = async () => {
    if (!newName.trim()) return;
    setLoading(true);
    try {
      const res = await fetch("/workflows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, template: newTemplate }),
      });
      const proj = await res.json();
      setProjects((prev) => [...prev, proj]);
      setNewName("");
    } catch { /* ignore */ }
    setLoading(false);
  };

  const runPhase = async (projectId: string) => {
    setLoading(true);
    try {
      await fetch(`/workflows/${projectId}/run`, { method: "POST" });
      // Refresh
      const p = await fetch("/workflows").then((r) => r.json());
      setProjects(p);
    } catch { /* ignore */ }
    setLoading(false);
  };

  const runAll = async (projectId: string) => {
    setLoading(true);
    try {
      await fetch(`/workflows/${projectId}/run-all`, { method: "POST" });
      const p = await fetch("/workflows").then((r) => r.json());
      setProjects(p);
    } catch { /* ignore */ }
    setLoading(false);
  };

  const approve = async (projectId: string) => {
    try {
      await fetch(`/workflows/${projectId}/approve`, { method: "POST" });
      const p = await fetch("/workflows").then((r) => r.json());
      setProjects(p);
    } catch { /* ignore */ }
  };

  const reject = async (projectId: string) => {
    try {
      await fetch(`/workflows/${projectId}/reject`, { method: "POST" });
      const p = await fetch("/workflows").then((r) => r.json());
      setProjects(p);
    } catch { /* ignore */ }
  };

  return (
    <div className="h-full overflow-auto p-4">
      <h2 className="text-lg font-semibold mb-4">Project Workflows</h2>

      {/* Create new project */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">New Project</h3>
        <div className="flex gap-2">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Project name..."
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <select
            value={newTemplate}
            onChange={(e) => setNewTemplate(e.target.value)}
            className="border border-gray-300 rounded px-3 py-2 text-sm"
          >
            {Object.keys(templates).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button
            onClick={createProject}
            disabled={loading || !newName.trim()}
            className="px-4 py-2 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>

      {/* Project list */}
      {projects.length === 0 && (
        <p className="text-sm text-gray-400 text-center mt-8">No projects yet. Create one above.</p>
      )}

      {projects.map((proj) => (
        <div key={proj.project_id} className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
          {/* Project header */}
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-sm font-bold text-gray-800">{proj.name}</h3>
              <p className="text-[10px] text-gray-400">
                {proj.project_id} · {proj.template} · {proj.current_phase}/{proj.total_phases} phases
              </p>
            </div>
            <div className="flex gap-1.5">
              {proj.status === "paused" && (
                <>
                  <button
                    onClick={() => approve(proj.project_id)}
                    className="px-2 py-1 text-[11px] bg-green-100 text-green-700 rounded hover:bg-green-200"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => reject(proj.project_id)}
                    className="px-2 py-1 text-[11px] bg-red-100 text-red-700 rounded hover:bg-red-200"
                  >
                    Reject
                  </button>
                </>
              )}
              {proj.status !== "completed" && proj.status !== "failed" && proj.status !== "paused" && (
                <>
                  <button
                    onClick={() => runPhase(proj.project_id)}
                    disabled={loading}
                    className="px-2 py-1 text-[11px] bg-blue-100 text-blue-700 rounded hover:bg-blue-200 disabled:opacity-50"
                  >
                    Run Phase
                  </button>
                  <button
                    onClick={() => runAll(proj.project_id)}
                    disabled={loading}
                    className="px-2 py-1 text-[11px] bg-purple-100 text-purple-700 rounded hover:bg-purple-200 disabled:opacity-50"
                  >
                    Run All
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Phase pipeline visualization */}
          <div className="flex items-center gap-1 overflow-x-auto pb-2">
            {proj.phases.map((phase, i) => {
              const st = STATUS_STYLES[phase.status] || STATUS_STYLES.pending;
              const icon = PHASE_ICONS[phase.name] || "📌";
              const result = proj.results.find((r) => r.phase_name === phase.name);
              const isActive = phase.status === "active";

              return (
                <div key={phase.name} className="flex items-center">
                  {/* Phase card */}
                  <button
                    onClick={() => result?.room_id && onSelectRoom(result.room_id)}
                    className={`shrink-0 w-32 rounded-lg border p-2 text-left transition-all ${st.bg} border-gray-200 ${
                      isActive ? "ring-2 ring-green-400" : ""
                    } ${result?.room_id ? "hover:shadow-md cursor-pointer" : "cursor-default"}`}
                  >
                    <div className="flex items-center gap-1 mb-1">
                      <span className="text-sm">{icon}</span>
                      <span className="text-[10px] font-bold text-gray-700 truncate">{phase.name}</span>
                    </div>
                    <div className="text-[9px] text-gray-500">{phase.protocol}</div>
                    <div className="flex gap-0.5 mt-1">
                      {phase.agents.map((a) => (
                        <span key={a} className="text-[8px] bg-white/50 px-1 rounded">{a.slice(0, 4)}</span>
                      ))}
                    </div>
                    <div className="flex items-center justify-between mt-1.5">
                      <span className={`text-[9px] font-medium ${st.text}`}>{st.label}</span>
                      <span className="text-[8px] text-gray-400">{HUMAN_LABELS[phase.human_role]}</span>
                    </div>
                  </button>

                  {/* Arrow between phases */}
                  {i < proj.phases.length - 1 && (
                    <div className="shrink-0 px-1 text-gray-300">→</div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Phase results */}
          {proj.results.length > 0 && (
            <div className="mt-3 border-t border-gray-100 pt-2">
              <p className="text-[10px] text-gray-500 font-medium mb-1">Results</p>
              {proj.results.map((r) => (
                <div key={r.phase_name} className="flex items-start gap-2 mb-1">
                  <span className={`text-[10px] px-1 py-0.5 rounded ${
                    r.status === "completed" ? "bg-blue-100 text-blue-600" :
                    r.status === "waiting_approval" ? "bg-yellow-100 text-yellow-600" :
                    "bg-red-100 text-red-600"
                  }`}>{r.phase_name}</span>
                  <p className="text-[10px] text-gray-500 truncate flex-1">{r.summary.slice(0, 100)}</p>
                  {r.room_id && (
                    <button
                      onClick={() => onSelectRoom(r.room_id)}
                      className="text-[9px] text-blue-500 hover:underline shrink-0"
                    >
                      View
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
