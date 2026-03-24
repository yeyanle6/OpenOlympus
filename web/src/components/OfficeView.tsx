import { useState, useEffect, useMemo } from "react";
import type { RoomInfo } from "../lib/types";

interface RoomDetail extends RoomInfo {
  messageCount: number;
  lastSender: string;
  round: number;
}

const AGENT_AVATARS: Record<string, { emoji: string; color: string }> = {
  planner:     { emoji: "📋", color: "#3b82f6" },
  architect:   { emoji: "🏗️", color: "#10b981" },
  critic:      { emoji: "🔍", color: "#ef4444" },
  builder:     { emoji: "🔨", color: "#f59e0b" },
  worker:      { emoji: "⚙️", color: "#f97316" },
  coordinator: { emoji: "🎯", color: "#8b5cf6" },
  tracker:     { emoji: "📊", color: "#6366f1" },
  auditor:     { emoji: "📐", color: "#06b6d4" },
  researcher:  { emoji: "🔬", color: "#14b8a6" },
  explorer:    { emoji: "🧭", color: "#0ea5e9" },
  reviewer:    { emoji: "✅", color: "#7c3aed" },
  tester:      { emoji: "🧪", color: "#ec4899" },
};

const PROTOCOL_LABELS: Record<string, string> = {
  roundtable: "Roundtable",
  delegate: "Delegate",
  peer_review: "Peer Review",
  pipeline: "Pipeline",
  parallel: "Parallel",
};

interface Props {
  rooms: RoomInfo[];
  selectedRoom: string;
  onSelectRoom: (id: string) => void;
}

// Group rooms into "buildings" (projects)
interface Building {
  id: string;
  name: string;
  floors: Floor[];
  rootRoom: RoomInfo;
}

interface Floor {
  theme: string;
  label: string;
  icon: string;
  rooms: RoomInfo[];
}

export function OfficeView({ rooms, selectedRoom, onSelectRoom }: Props) {
  const [roomDetails, setRoomDetails] = useState<Map<string, RoomDetail>>(new Map());
  const [selectedBuilding, setSelectedBuilding] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // Animation tick
  useEffect(() => {
    const t = setInterval(() => setTick((v) => v + 1), 2000);
    return () => clearInterval(t);
  }, []);

  // Only fetch details for running rooms + selected building's rooms (not ALL)
  useEffect(() => {
    const load = async () => {
      const details = new Map<string, RoomDetail>(roomDetails);
      // Only fetch running rooms or rooms in the selected building
      const toFetch = rooms.filter((r) =>
        r.status === "running" ||
        (selectedBuilding && (r.room_id === selectedBuilding || (r as any).parent_room === selectedBuilding)) ||
        !details.has(r.room_id)
      );
      for (const room of toFetch.slice(0, 10)) {
        try {
          const res = await fetch(`/rooms/${room.room_id}/messages`);
          const msgs = res.ok ? await res.json() : [];
          const ac = Math.max(room.agents.length, 1);
          details.set(room.room_id, {
            ...room, messageCount: msgs.length,
            lastSender: msgs.length > 0 ? msgs[msgs.length - 1].sender : "",
            round: Math.floor(msgs.length / ac) + 1,
          });
        } catch {
          details.set(room.room_id, { ...room, messageCount: 0, lastSender: "", round: 1 });
        }
      }
      setRoomDetails(new Map(details));
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [rooms, selectedBuilding]);

  // Build buildings from room hierarchy
  const buildings = useMemo(() => {
    const rootRooms = rooms.filter((r) => !(r as any).parent_room);
    const childMap = new Map<string, RoomInfo[]>();
    rooms.forEach((r) => {
      const p = (r as any).parent_room;
      if (p) childMap.set(p, [...(childMap.get(p) || []), r]);
    });

    // Group: a root room + all descendants = one building
    const result: Building[] = [];
    const assigned = new Set<string>();

    // Find roots that have children (these are project heads)
    for (const root of rootRooms) {
      const children = childMap.get(root.room_id) || [];
      if (children.length > 0 || root.status === "running") {
        const allRooms = [root, ...children];
        // Get deeper children too
        const getDeep = (parentId: string, depth: number): RoomInfo[] => {
          const kids = childMap.get(parentId) || [];
          let all: RoomInfo[] = [];
          for (const k of kids) {
            all.push(k);
            all = [...all, ...getDeep(k.room_id, depth + 1)];
          }
          return all;
        };
        const deepChildren = getDeep(root.room_id, 1);

        // Build floors by theme
        const THEME_CONFIG: Record<string, { label: string; icon: string; order: number }> = {
          strategy:   { label: "Strategy",   icon: "🎯", order: 0 },
          technical:  { label: "Technical",  icon: "⚙️", order: 1 },
          design:     { label: "Design",     icon: "🎨", order: 2 },
          validation: { label: "Validation", icon: "🧪", order: 3 },
          business:   { label: "Business",   icon: "💼", order: 4 },
          compliance: { label: "Compliance", icon: "📜", order: 5 },
        };

        const floorMap = new Map<string, RoomInfo[]>();
        // Root room is always "strategy"
        floorMap.set("strategy", [root]);
        for (const c of deepChildren) {
          const theme = (c as any).theme || "technical";
          floorMap.set(theme, [...(floorMap.get(theme) || []), c]);
        }

        const floors: Floor[] = [];
        for (const [theme, floorRooms] of [...floorMap.entries()].sort((a, b) => {
          const oa = THEME_CONFIG[a[0]]?.order ?? 99;
          const ob = THEME_CONFIG[b[0]]?.order ?? 99;
          return oa - ob;
        })) {
          const cfg = THEME_CONFIG[theme] || { label: theme, icon: "📁", order: 99 };
          floors.push({
            theme,
            label: cfg.label,
            icon: cfg.icon,
            rooms: floorRooms,
          });
        }

        result.push({
          id: root.room_id,
          name: root.task.slice(0, 30),
          floors,
          rootRoom: root,
        });
        assigned.add(root.room_id);
        deepChildren.forEach((c) => assigned.add(c.room_id));
      }
    }

    // Standalone rooms without children → group into "General" building
    const standalone = rootRooms.filter((r) => !assigned.has(r.room_id));
    if (standalone.length > 0) {
      result.unshift({
        id: "general",
        name: "General Discussions",
        floors: [{ depth: 0, label: "Lobby", rooms: standalone }],
        rootRoom: standalone[0],
      });
    }

    return result;
  }, [rooms]);

  // ─── Building List View (first layer) ─────────────────────
  if (!selectedBuilding) {
    return (
      <div className="h-full overflow-auto bg-gradient-to-b from-sky-100 to-sky-50 p-6">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">
          Project Buildings
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {buildings.map((b) => {
            const totalRooms = b.floors.reduce((s, f) => s + f.rooms.length, 0);
            const runningCount = b.floors.reduce(
              (s, f) => s + f.rooms.filter((r) => r.status === "running").length, 0
            );
            const totalMsgs = b.floors.reduce(
              (s, f) => s + f.rooms.reduce((ss, r) => ss + (roomDetails.get(r.room_id)?.messageCount || 0), 0), 0
            );
            const allAgents = new Set<string>();
            b.floors.forEach((f) => f.rooms.forEach((r) => r.agents.forEach((a) => allAgents.add(a))));

            return (
              <button
                key={b.id}
                onClick={() => setSelectedBuilding(b.id)}
                className="text-left bg-white rounded-xl shadow-md hover:shadow-lg transition-all border border-gray-100 overflow-hidden group"
              >
                {/* Building roof */}
                <div className="h-3 bg-gradient-to-r from-indigo-500 to-blue-500" />

                {/* Building facade */}
                <div className="p-4">
                  <div className="flex items-start justify-between mb-2">
                    <span className="text-2xl">🏢</span>
                    {runningCount > 0 && (
                      <span className="flex items-center gap-1 text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full">
                        <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
                        {runningCount} active
                      </span>
                    )}
                  </div>

                  <h3 className="text-sm font-semibold text-gray-800 mb-1 group-hover:text-blue-700 transition-colors">
                    {b.name}...
                  </h3>

                  {/* Building floors preview */}
                  <div className="flex flex-col-reverse gap-0.5 my-3">
                    {b.floors.map((f) => (
                      <div key={f.theme} className="flex items-center gap-1">
                        <span className="text-[9px] text-gray-400 w-10 text-right">{f.icon}</span>
                        <div className="flex-1 h-5 bg-gray-50 border border-gray-200 rounded-sm flex items-center px-1 gap-0.5">
                          {f.rooms.map((r) => (
                            <div
                              key={r.room_id}
                              className={`h-3 rounded-sm flex-1 ${
                                r.status === "running" ? "bg-green-300 animate-pulse" :
                                r.status === "completed" ? "bg-blue-200" : "bg-gray-200"
                              }`}
                              title={r.task.slice(0, 40)}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Stats */}
                  <div className="flex items-center gap-3 text-[10px] text-gray-400">
                    <span>{b.floors.length} floors</span>
                    <span>{totalRooms} rooms</span>
                    <span>{totalMsgs} msgs</span>
                  </div>

                  {/* Agents in building */}
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {[...allAgents].map((a) => {
                      const av = AGENT_AVATARS[a] || { emoji: "🤖", color: "#6b7280" };
                      return (
                        <span key={a} className="text-xs" title={a}>{av.emoji}</span>
                      );
                    })}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // ─── Building Interior View (second layer) ─────────────────
  const building = buildings.find((b) => b.id === selectedBuilding);
  if (!building) {
    setSelectedBuilding(null);
    return null;
  }

  return (
    <div className="h-full flex flex-col bg-gradient-to-b from-gray-50 to-white">
      {/* Building header */}
      <div className="px-4 py-3 border-b border-gray-200 bg-white flex items-center gap-3">
        <button
          onClick={() => setSelectedBuilding(null)}
          className="text-gray-400 hover:text-gray-600 text-sm"
        >
          ← Back
        </button>
        <span className="text-lg">🏢</span>
        <div>
          <h2 className="text-sm font-semibold text-gray-800">{building.name}...</h2>
          <p className="text-[10px] text-gray-400">
            {building.floors.length} floors · {building.floors.reduce((s, f) => s + f.rooms.length, 0)} rooms
          </p>
        </div>
      </div>

      {/* Floors (bottom to top) */}
      <div className="flex-1 overflow-auto p-4">
        <div className="flex flex-col-reverse gap-4 max-w-4xl mx-auto">
          {building.floors.map((floor) => (
            <div key={floor.theme} className="relative">
              {/* Floor label */}
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm">{floor.icon}</span>
                <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                  {floor.label}
                </span>
                <span className="text-[10px] text-gray-300">
                  {floor.rooms.length} team{floor.rooms.length > 1 ? "s" : ""}
                </span>
                <div className="flex-1 h-px bg-gray-200" />
              </div>

              {/* Rooms on this floor */}
              <div className="flex gap-3 overflow-x-auto pb-2">
                {floor.rooms.map((room) => {
                  const detail = roomDetails.get(room.room_id);
                  const isRunning = room.status === "running";
                  const isSelected = room.room_id === selectedRoom;

                  return (
                    <button
                      key={room.room_id}
                      onClick={() => onSelectRoom(room.room_id)}
                      className={`shrink-0 w-56 rounded-lg border-2 p-3 text-left transition-all hover:shadow-md ${
                        isSelected ? "ring-2 ring-blue-500 ring-offset-2" : ""
                      } ${
                        isRunning ? "border-green-400 bg-green-50" :
                        room.status === "completed" ? "border-blue-300 bg-blue-50" :
                        "border-gray-200 bg-white"
                      }`}
                    >
                      {/* Room header */}
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-[10px] font-bold text-gray-500 uppercase">
                          {PROTOCOL_LABELS[room.protocol] || room.protocol}
                        </span>
                        <div className="flex items-center gap-1">
                          {isRunning && (
                            <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                          )}
                          <span className={`text-[10px] font-medium ${
                            isRunning ? "text-green-600" :
                            room.status === "completed" ? "text-blue-600" :
                            "text-gray-400"
                          }`}>
                            {room.status}
                          </span>
                        </div>
                      </div>

                      {/* Task */}
                      <p className="text-[11px] text-gray-700 leading-tight mb-2 line-clamp-2">
                        {room.task.slice(0, 60)}...
                      </p>

                      {/* Agent avatars "sitting at desks" */}
                      <div className="flex gap-1 mb-2">
                        {room.agents.map((agentId) => {
                          const av = AGENT_AVATARS[agentId] || { emoji: "🤖", color: "#6b7280" };
                          const isSpeaking = isRunning && detail?.lastSender === agentId;
                          return (
                            <div
                              key={agentId}
                              className={`flex flex-col items-center px-1 py-0.5 rounded ${
                                isSpeaking ? "bg-yellow-100 ring-1 ring-yellow-300" : ""
                              }`}
                              title={agentId}
                            >
                              <span className={`text-base ${isSpeaking ? "animate-bounce" : ""}`}>
                                {av.emoji}
                              </span>
                              <span className="text-[8px] text-gray-400">{agentId.slice(0, 4)}</span>
                            </div>
                          );
                        })}
                      </div>

                      {/* Progress */}
                      {detail && (
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${
                                isRunning ? "bg-green-500" : "bg-blue-500"
                              }`}
                              style={{
                                width: `${Math.min(100, (detail.messageCount / (room.agents.length * 3)) * 100)}%`,
                              }}
                            />
                          </div>
                          <span className="text-[9px] text-gray-400">
                            {detail.messageCount} msgs
                          </span>
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>

              {/* Floor "ground" line */}
              <div className="h-1 bg-gradient-to-r from-gray-200 via-gray-300 to-gray-200 rounded mt-1" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
