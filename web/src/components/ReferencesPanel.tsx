import { useState, useEffect } from "react";

interface RefContext {
  agent: string;
  quote: string;
  message_index: number;
}

interface RefItem {
  id: string;
  url: string;
  title: string;
  type: string;
  cited_by: string[];
  contexts: RefContext[];
  citation_count: number;
  message_indices: number[];
}

interface TreeNode {
  name: string;
  children?: TreeNode[];
  id?: string;
  url?: string;
  cited_by?: string[];
  citation_count?: number;
}

interface RefData {
  tree: TreeNode;
  by_type: Record<string, RefItem[]>;
  agent_refs: Record<string, string[]>;
  total: number;
  stats: Record<string, number>;
}

interface Props {
  roomId: string;
  onClose: () => void;
}

const TYPE_COLORS: Record<string, { bg: string; text: string; icon: string }> = {
  url:     { bg: "bg-blue-100",   text: "text-blue-700",   icon: "🔗" },
  paper:   { bg: "bg-amber-100",  text: "text-amber-700",  icon: "📄" },
  dataset: { bg: "bg-green-100",  text: "text-green-700",  icon: "📊" },
  tool:    { bg: "bg-purple-100", text: "text-purple-700", icon: "🔧" },
  standard:{ bg: "bg-gray-100",   text: "text-gray-700",   icon: "📋" },
};

const AGENT_COLORS: Record<string, string> = {
  planner: "bg-blue-500", architect: "bg-emerald-500", critic: "bg-red-500",
  builder: "bg-amber-500", researcher: "bg-teal-500", coordinator: "bg-purple-500",
  auditor: "bg-cyan-500", explorer: "bg-sky-500", reviewer: "bg-violet-500",
  tester: "bg-pink-500", tracker: "bg-indigo-500", worker: "bg-orange-500",
};

export function ReferencesPanel({ roomId, onClose }: Props) {
  const [data, setData] = useState<RefData | null>(null);
  const [view, setView] = useState<"tree" | "table" | "agents">("tree");
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [selectedRef, setSelectedRef] = useState<RefItem | null>(null);

  useEffect(() => {
    const load = () => {
      fetch(`/rooms/${roomId}/references`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d) setData(d); })
        .catch(() => {});
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [roomId]);

  if (!data) return null;

  const toggleType = (type: string) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header */}
      <div className="p-3 border-b border-gray-200">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-700">References</h3>
            <p className="text-xs text-gray-400 mt-0.5">{data.total} references found</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">&times;</button>
        </div>

        {/* Stats bar */}
        <div className="flex gap-1.5 mt-2">
          {Object.entries(data.stats).map(([type, count]) => {
            const tc = TYPE_COLORS[type] || TYPE_COLORS.url;
            return (
              <span key={type} className={`text-[10px] px-1.5 py-0.5 rounded ${tc.bg} ${tc.text}`}>
                {tc.icon} {type}: {count}
              </span>
            );
          })}
        </div>

        {/* View toggle */}
        <div className="flex gap-1 mt-2">
          {(["tree", "table", "agents"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-1 text-[11px] rounded ${
                view === v ? "bg-gray-800 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {v === "tree" ? "Tree" : v === "table" ? "Table" : "By Agent"}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {view === "tree" && <TreeView data={data} expandedTypes={expandedTypes} toggleType={toggleType} onSelect={setSelectedRef} />}
        {view === "table" && <TableView data={data} onSelect={setSelectedRef} />}
        {view === "agents" && <AgentView data={data} onSelect={setSelectedRef} />}
      </div>

      {/* Detail drawer */}
      {selectedRef && (
        <div className="border-t border-gray-200 p-3 max-h-[40%] overflow-y-auto bg-gray-50">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold text-gray-700">{selectedRef.title}</h4>
            <button onClick={() => setSelectedRef(null)} className="text-gray-400 text-xs">&times;</button>
          </div>
          {selectedRef.url && (
            <a href={selectedRef.url} target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline break-all">
              {selectedRef.url}
            </a>
          )}
          <div className="mt-2 space-y-2">
            <p className="text-[10px] text-gray-500 font-medium">Citations ({selectedRef.contexts.length})</p>
            {selectedRef.contexts.slice(0, 5).map((ctx, i) => (
              <div key={i} className="bg-white rounded p-2 border border-gray-100 text-xs">
                <span className="font-medium text-gray-700">{ctx.agent}</span>
                <span className="text-gray-400 ml-1">(msg #{ctx.message_index + 1})</span>
                <p className="text-gray-500 mt-1 italic">"{ctx.quote}"</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TreeView({
  data, expandedTypes, toggleType, onSelect,
}: {
  data: RefData;
  expandedTypes: Set<string>;
  toggleType: (t: string) => void;
  onSelect: (r: RefItem) => void;
}) {
  return (
    <div className="space-y-1">
      {data.tree.children?.map((typeNode) => {
        const isOpen = expandedTypes.has(typeNode.name);
        const tc = TYPE_COLORS[typeNode.name] || TYPE_COLORS.url;
        const refs = data.by_type[typeNode.name] || [];

        return (
          <div key={typeNode.name}>
            <button
              onClick={() => toggleType(typeNode.name)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-left hover:bg-gray-50 ${tc.text}`}
            >
              <span className="text-xs">{isOpen ? "▼" : "▶"}</span>
              <span className="text-xs">{tc.icon}</span>
              <span className="text-xs font-medium">{typeNode.name}</span>
              <span className="text-[10px] text-gray-400 ml-auto">{refs.length}</span>
            </button>
            {isOpen && (
              <div className="ml-6 space-y-0.5">
                {refs.map((ref) => (
                  <button
                    key={ref.id}
                    onClick={() => onSelect(ref)}
                    className="w-full flex items-center gap-2 px-2 py-1 rounded text-left hover:bg-gray-100"
                  >
                    <span className="text-xs text-gray-700 truncate flex-1">{ref.title}</span>
                    <div className="flex gap-0.5 shrink-0">
                      {ref.cited_by.map((agent) => (
                        <span
                          key={agent}
                          className={`w-2 h-2 rounded-full ${AGENT_COLORS[agent] || "bg-gray-400"}`}
                          title={agent}
                        />
                      ))}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TableView({ data, onSelect }: { data: RefData; onSelect: (r: RefItem) => void }) {
  const allRefs = Object.values(data.by_type).flat();
  allRefs.sort((a, b) => b.citation_count - a.citation_count);

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-gray-500 border-b">
          <th className="py-1 pr-2">Type</th>
          <th className="py-1 pr-2">Reference</th>
          <th className="py-1 pr-2">Cited by</th>
          <th className="py-1">#</th>
        </tr>
      </thead>
      <tbody>
        {allRefs.map((ref) => {
          const tc = TYPE_COLORS[ref.type] || TYPE_COLORS.url;
          return (
            <tr
              key={ref.id}
              onClick={() => onSelect(ref)}
              className="border-b border-gray-50 hover:bg-gray-50 cursor-pointer"
            >
              <td className="py-1.5 pr-2">
                <span className={`text-[10px] px-1 py-0.5 rounded ${tc.bg} ${tc.text}`}>
                  {tc.icon} {ref.type}
                </span>
              </td>
              <td className="py-1.5 pr-2 text-gray-700 truncate max-w-[150px]">{ref.title}</td>
              <td className="py-1.5 pr-2">
                <div className="flex gap-0.5">
                  {ref.cited_by.map((a) => (
                    <span
                      key={a}
                      className={`w-2 h-2 rounded-full ${AGENT_COLORS[a] || "bg-gray-400"}`}
                      title={a}
                    />
                  ))}
                </div>
              </td>
              <td className="py-1.5 text-gray-400">{ref.citation_count}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function AgentView({ data, onSelect }: { data: RefData; onSelect: (r: RefItem) => void }) {
  const allRefs = Object.values(data.by_type).flat();
  const refMap = new Map(allRefs.map((r) => [r.id, r]));

  return (
    <div className="space-y-3">
      {Object.entries(data.agent_refs).map(([agent, refIds]) => (
        <div key={agent}>
          <div className="flex items-center gap-2 mb-1">
            <span className={`w-2.5 h-2.5 rounded-full ${AGENT_COLORS[agent] || "bg-gray-400"}`} />
            <span className="text-xs font-semibold text-gray-700">{agent}</span>
            <span className="text-[10px] text-gray-400">{refIds.length} refs</span>
          </div>
          <div className="ml-5 space-y-0.5">
            {[...new Set(refIds)].map((rid) => {
              const ref = refMap.get(rid);
              if (!ref) return null;
              const tc = TYPE_COLORS[ref.type] || TYPE_COLORS.url;
              return (
                <button
                  key={rid}
                  onClick={() => onSelect(ref)}
                  className="w-full flex items-center gap-2 px-2 py-1 rounded text-left hover:bg-gray-100"
                >
                  <span className="text-[10px]">{tc.icon}</span>
                  <span className="text-xs text-gray-700 truncate">{ref.title}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
