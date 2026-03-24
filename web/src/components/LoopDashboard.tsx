import { useState, useEffect } from "react";
import { api } from "../lib/api";
import type { LoopStatus } from "../lib/types";

export function LoopDashboard() {
  const [status, setStatus] = useState<LoopStatus | null>(null);
  const [consensus, setConsensus] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    try {
      const [s, c] = await Promise.all([api.loopStatus(), api.consensus()]);
      setStatus(s);
      setConsensus(c.content);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleStart = async () => {
    setLoading(true);
    try {
      await api.loopStart();
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      await api.loopStop();
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const isRunning = status?.status === "running";

  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto">
      <h2 className="text-lg font-semibold mb-4">Autonomous Loop</h2>

      {/* Status Cards */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <StatusCard
          label="Status"
          value={status?.status || "unknown"}
          color={isRunning ? "green" : "gray"}
        />
        <StatusCard
          label="Cycles"
          value={String(status?.cycle_count || 0)}
          color="blue"
        />
        <StatusCard
          label="Errors"
          value={String(status?.error_count || 0)}
          color={status?.error_count ? "red" : "gray"}
        />
        <StatusCard
          label="Total Cost"
          value={`$${(status?.total_cost || 0).toFixed(2)}`}
          color="purple"
        />
      </div>

      {/* Controls */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={handleStart}
          disabled={loading || isRunning}
          className="px-4 py-2 bg-green-600 text-white rounded text-sm font-medium hover:bg-green-700 disabled:opacity-50"
        >
          Start Loop
        </button>
        <button
          onClick={handleStop}
          disabled={loading || !isRunning}
          className="px-4 py-2 bg-red-600 text-white rounded text-sm font-medium hover:bg-red-700 disabled:opacity-50"
        >
          Stop Loop
        </button>
        <button
          onClick={refresh}
          className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm font-medium hover:bg-gray-300"
        >
          Refresh
        </button>
      </div>

      {/* Consensus */}
      <div className="flex-1 min-h-0">
        <h3 className="text-sm font-semibold text-gray-700 mb-2">
          Current Consensus
        </h3>
        <div className="bg-white border border-gray-200 rounded-lg p-4 overflow-y-auto max-h-[60vh]">
          {consensus ? (
            <pre className="text-sm text-gray-700 whitespace-pre-wrap font-mono">
              {consensus}
            </pre>
          ) : (
            <p className="text-sm text-gray-400">No consensus yet</p>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  const colorMap: Record<string, string> = {
    green: "border-green-200 bg-green-50",
    blue: "border-blue-200 bg-blue-50",
    red: "border-red-200 bg-red-50",
    purple: "border-purple-200 bg-purple-50",
    gray: "border-gray-200 bg-gray-50",
  };

  return (
    <div
      className={`border rounded-lg p-3 ${colorMap[color] || colorMap.gray}`}
    >
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-lg font-semibold mt-0.5">{value}</p>
    </div>
  );
}
