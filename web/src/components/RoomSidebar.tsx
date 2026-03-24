import type { RoomInfo } from "../lib/types";

const STATUS_COLORS: Record<string, string> = {
  created: "bg-gray-300",
  running: "bg-green-500",
  completed: "bg-blue-500",
  failed: "bg-red-500",
  timeout: "bg-yellow-500",
  budget_exceeded: "bg-orange-500",
  paused: "bg-purple-500",
  cancelled: "bg-gray-500",
};

interface Props {
  rooms: RoomInfo[];
  selectedRoom: string;
  onSelect: (id: string) => void;
}

export function RoomSidebar({ rooms, selectedRoom, onSelect }: Props) {
  return (
    <aside className="w-64 border-r border-gray-200 bg-white flex flex-col shrink-0">
      <div className="p-3 border-b border-gray-100">
        <h2 className="text-sm font-semibold text-gray-700">Rooms</h2>
      </div>
      <div className="flex-1 overflow-y-auto">
        {rooms.length === 0 && (
          <p className="text-xs text-gray-400 p-3">No rooms yet</p>
        )}
        {rooms.map((room) => (
          <button
            key={room.room_id}
            onClick={() => onSelect(room.room_id)}
            className={`w-full text-left p-3 border-b border-gray-50 hover:bg-gray-50 transition ${
              selectedRoom === room.room_id ? "bg-blue-50" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full shrink-0 ${
                  STATUS_COLORS[room.status] || "bg-gray-300"
                }`}
              />
              <span className="text-xs font-medium text-gray-700 truncate">
                {room.room_id}
              </span>
            </div>
            <p className="text-xs text-gray-500 mt-1 truncate">{room.task}</p>
            <div className="flex gap-1 mt-1 flex-wrap">
              <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 rounded text-gray-500">
                {room.protocol}
              </span>
              {room.agents.map((a) => (
                <span
                  key={a}
                  className="text-[10px] px-1.5 py-0.5 bg-blue-50 rounded text-blue-600"
                >
                  {a}
                </span>
              ))}
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
