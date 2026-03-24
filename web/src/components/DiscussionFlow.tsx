import { useState, useEffect } from "react";
import type { RoomInfo } from "../lib/types";

interface RoomWithMessages extends RoomInfo {
  messageCount?: number;
  lastSender?: string;
  round?: number;
}

const STATUS_COLORS: Record<string, { bg: string; border: string; text: string; pulse?: string }> = {
  running:   { bg: "bg-green-50",  border: "border-green-400",  text: "text-green-700",  pulse: "animate-pulse" },
  completed: { bg: "bg-blue-50",   border: "border-blue-400",   text: "text-blue-700" },
  paused:    { bg: "bg-yellow-50", border: "border-yellow-400", text: "text-yellow-700" },
  failed:    { bg: "bg-red-50",    border: "border-red-400",    text: "text-red-700" },
  timeout:   { bg: "bg-orange-50", border: "border-orange-400", text: "text-orange-700" },
  cancelled: { bg: "bg-gray-50",   border: "border-gray-400",   text: "text-gray-500" },
  created:   { bg: "bg-gray-50",   border: "border-gray-300",   text: "text-gray-500" },
};

const AGENT_DOT_COLORS: Record<string, string> = {
  planner: "bg-blue-500", architect: "bg-emerald-500", critic: "bg-red-500",
  builder: "bg-amber-500", researcher: "bg-teal-500", coordinator: "bg-purple-500",
  auditor: "bg-cyan-500", explorer: "bg-sky-500", reviewer: "bg-violet-500",
  tester: "bg-pink-500", tracker: "bg-indigo-500", worker: "bg-orange-500",
};

interface Props {
  rooms: RoomInfo[];
  selectedRoom: string;
  onSelectRoom: (id: string) => void;
}

export function DiscussionFlow({ rooms, selectedRoom, onSelectRoom }: Props) {
  const [roomDetails, setRoomDetails] = useState<Map<string, RoomWithMessages>>(new Map());

  // Fetch message counts for each room
  useEffect(() => {
    const fetchDetails = async () => {
      const details = new Map<string, RoomWithMessages>();
      for (const room of rooms) {
        try {
          const res = await fetch(`/rooms/${room.room_id}/messages`);
          if (res.ok) {
            const msgs = await res.json();
            const agentCount = room.agents.length || 1;
            details.set(room.room_id, {
              ...room,
              messageCount: msgs.length,
              lastSender: msgs.length > 0 ? msgs[msgs.length - 1].sender : undefined,
              round: Math.floor(msgs.length / agentCount) + 1,
            });
          } else {
            details.set(room.room_id, { ...room });
          }
        } catch {
          details.set(room.room_id, { ...room });
        }
      }
      setRoomDetails(details);
    };
    fetchDetails();
    const interval = setInterval(fetchDetails, 5000);
    return () => clearInterval(interval);
  }, [rooms]);

  // Build tree: group by parent
  const rootRooms = rooms.filter((r) => !(r as any).parent_room);
  const childMap = new Map<string, RoomInfo[]>();
  rooms.forEach((r) => {
    const parent = (r as any).parent_room;
    if (parent) {
      childMap.set(parent, [...(childMap.get(parent) || []), r]);
    }
  });

  const renderNode = (room: RoomInfo, depth: number = 0) => {
    const detail = roomDetails.get(room.room_id) || room;
    const sc = STATUS_COLORS[room.status] || STATUS_COLORS.created;
    const isSelected = room.room_id === selectedRoom;
    const children = childMap.get(room.room_id) || [];
    const isRunning = room.status === "running";
    const rd = detail as RoomWithMessages;

    return (
      <div key={room.room_id} className="flex items-start gap-2">
        {/* Node */}
        <button
          onClick={() => onSelectRoom(room.room_id)}
          className={`shrink-0 border-2 rounded-lg px-3 py-2 text-left transition-all ${sc.bg} ${sc.border} ${
            isSelected ? "ring-2 ring-blue-500 ring-offset-1" : ""
          } ${sc.pulse || ""} hover:shadow-md`}
          style={{ minWidth: 140, maxWidth: 200 }}
        >
          {/* Protocol + status badge */}
          <div className="flex items-center gap-1.5 mb-1">
            <span className={`text-[10px] font-bold uppercase ${sc.text}`}>
              {room.protocol}
            </span>
            {isRunning && (
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            )}
          </div>

          {/* Task (truncated) */}
          <p className="text-[11px] text-gray-700 leading-tight truncate">
            {room.task.slice(0, 50)}
          </p>

          {/* Agent dots */}
          <div className="flex gap-0.5 mt-1.5">
            {room.agents.map((a) => (
              <div key={a} className="flex items-center gap-0.5" title={a}>
                <span className={`w-2 h-2 rounded-full ${AGENT_DOT_COLORS[a] || "bg-gray-400"} ${
                  isRunning && rd.lastSender === a ? "ring-2 ring-white ring-offset-1 scale-125" : ""
                }`} />
                <span className="text-[9px] text-gray-500">{a.slice(0, 3)}</span>
              </div>
            ))}
          </div>

          {/* Progress */}
          <div className="flex items-center gap-2 mt-1.5">
            {rd.messageCount !== undefined && (
              <span className="text-[10px] text-gray-400">
                {rd.messageCount} msgs
              </span>
            )}
            {rd.round !== undefined && isRunning && (
              <span className="text-[10px] text-gray-400">
                R{rd.round}
              </span>
            )}
            <span className={`text-[10px] font-medium ${sc.text} ml-auto`}>
              {room.status}
            </span>
          </div>
        </button>

        {/* Arrow + Children */}
        {children.length > 0 && (
          <div className="flex items-center gap-2">
            {/* Arrow */}
            <div className="flex flex-col items-center justify-center">
              <svg width="24" height="20" viewBox="0 0 24 20" className="text-gray-300">
                <path d="M0 10 L18 10 M14 5 L20 10 L14 15" stroke="currentColor" strokeWidth="2" fill="none" />
              </svg>
            </div>

            {/* Child rooms stacked vertically */}
            <div className="flex flex-col gap-1.5">
              {children.map((child) => renderNode(child, depth + 1))}
            </div>
          </div>
        )}
      </div>
    );
  };

  if (rooms.length === 0) return null;

  return (
    <div className="border-b border-gray-200 bg-white px-4 py-2 overflow-x-auto">
      <div className="flex items-start gap-3 min-w-max">
        {/* Label */}
        <div className="shrink-0 flex items-center h-full py-2">
          <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider [writing-mode:vertical-lr] rotate-180">
            Flow
          </span>
        </div>

        {/* Room nodes */}
        {rootRooms.map((room) => renderNode(room))}
      </div>
    </div>
  );
}
