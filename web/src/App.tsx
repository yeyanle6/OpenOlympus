import { useState, useCallback, useEffect } from "react";
import { api } from "./lib/api";
import { useWebSocket } from "./lib/ws";
import type { RoomInfo, WsEvent } from "./lib/types";
import { DirectorChat } from "./components/DirectorChat";
import { RoomSidebar } from "./components/RoomSidebar";
import { RoomDetail } from "./components/RoomDetail";
import { LoopDashboard } from "./components/LoopDashboard";
import { OfficeView } from "./components/OfficeView";

type Tab = "director" | "loop" | "office";

export default function App() {
  const [rooms, setRooms] = useState<RoomInfo[]>([]);
  const [selectedRoom, setSelectedRoom] = useState<string>(
    () => localStorage.getItem("olympus_selectedRoom") || ""
  );
  const [tab, setTab] = useState<Tab>(
    () => (localStorage.getItem("olympus_tab") as Tab) || "director"
  );

  const [speaker, setSpeaker] = useState<{ busy: boolean; current_speaker: string; current_room: string; queue_size: number }>({ busy: false, current_speaker: "", current_room: "", queue_size: 0 });

  // Persist tab and selected room across refreshes
  useEffect(() => { localStorage.setItem("olympus_tab", tab); }, [tab]);
  useEffect(() => { localStorage.setItem("olympus_selectedRoom", selectedRoom); }, [selectedRoom]);

  const handleWsEvent = useCallback((event: WsEvent) => {
    if (event.type === "init" && event.rooms_status) {
      setRooms(event.rooms_status);
    } else if (event.type === "room_status" && event.room_id && event.data) {
      setRooms((prev) =>
        prev.map((r) =>
          r.room_id === event.room_id
            ? { ...r, status: event.data!.status as string }
            : r
        )
      );
    }
  }, []);

  const { statusRef } = useWebSocket(handleWsEvent);

  // Fallback polling: only active when WebSocket is disconnected
  useEffect(() => {
    const poll = setInterval(async () => {
      if (statusRef.current === "connected") return;
      try {
        const [r, s] = await Promise.all([api.rooms(), api.speaker()]);
        setRooms(r);
        setSpeaker(s);
      } catch { /* ignore */ }
    }, 2000);
    return () => clearInterval(poll);
  }, [statusRef]);

  const handleRoomCreated = useCallback((roomId: string) => {
    setSelectedRoom(roomId);
    api.rooms().then(setRooms).catch(() => {});
  }, []);

  const tabs: { key: Tab; label: string }[] = [
    { key: "director", label: "Director" },
    { key: "office", label: "Office" },
    { key: "loop", label: "Loop" },
  ];

  return (
    <div className="h-screen flex flex-col bg-gray-50 text-gray-900">
      {/* Header */}
      <header className="h-12 bg-white border-b border-gray-200 flex items-center px-4 shrink-0">
        <h1 className="text-lg font-semibold tracking-tight">Olympus</h1>
        <nav className="ml-8 flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-3 py-1.5 text-sm rounded ${
                tab === t.key
                  ? "bg-blue-100 text-blue-700 font-medium"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-xs">
          {speaker.busy ? (
            <span className="flex items-center gap-1.5 bg-green-50 border border-green-200 text-green-700 px-2 py-1 rounded-full">
              <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
              <span className="font-medium">{speaker.current_speaker}</span>
              <span className="text-green-500">speaking</span>
              {speaker.queue_size > 0 && (
                <span className="text-green-400">+{speaker.queue_size} waiting</span>
              )}
            </span>
          ) : (
            <span className="text-gray-400">idle</span>
          )}
          <span className="text-gray-300">|</span>
          <span className="text-gray-400">
            {rooms.filter((r) => r.status === "running").length} rooms
          </span>
        </div>
      </header>

      {/* Main */}
      <div className="flex flex-1 min-h-0">
        {/* Sidebar - hide on office view */}
        {tab !== "office" && (
          <RoomSidebar
            rooms={rooms}
            selectedRoom={selectedRoom}
            onSelect={setSelectedRoom}
          />
        )}

        {/* Center */}
        <main className="flex-1 min-w-0">
          {tab === "director" && (
            <DirectorChat onRoomCreated={handleRoomCreated} />
          )}
          {tab === "loop" && <LoopDashboard />}
          {tab === "office" && (
            <OfficeView
              rooms={rooms}
              selectedRoom={selectedRoom}
              onSelectRoom={setSelectedRoom}
            />
          )}
        </main>

        {/* Detail panel */}
        {selectedRoom && (
          <aside className="w-96 border-l border-gray-200 bg-white">
            <RoomDetail roomId={selectedRoom} onClose={() => setSelectedRoom("")} />
          </aside>
        )}
      </div>
    </div>
  );
}
