import { useState, useEffect, useCallback, useRef } from "react";
import { useWebSocket } from "../lib/ws";
import type { RoomMessage, RoomInfo, WsEvent } from "../lib/types";
import { ReferencesPanel } from "./ReferencesPanel";

interface Props {
  roomId: string;
  onClose: () => void;
}

const AGENT_STYLES: Record<string, { bg: string; border: string; text: string; dot: string }> = {
  planner:    { bg: "bg-blue-50",   border: "border-blue-200",   text: "text-blue-700",   dot: "bg-blue-500" },
  architect:  { bg: "bg-emerald-50", border: "border-emerald-200", text: "text-emerald-700", dot: "bg-emerald-500" },
  critic:     { bg: "bg-red-50",    border: "border-red-200",    text: "text-red-700",    dot: "bg-red-500" },
  builder:    { bg: "bg-amber-50",  border: "border-amber-200",  text: "text-amber-700",  dot: "bg-amber-500" },
  worker:     { bg: "bg-orange-50", border: "border-orange-200", text: "text-orange-700", dot: "bg-orange-500" },
  coordinator:{ bg: "bg-purple-50", border: "border-purple-200", text: "text-purple-700", dot: "bg-purple-500" },
  tracker:    { bg: "bg-indigo-50", border: "border-indigo-200", text: "text-indigo-700", dot: "bg-indigo-500" },
  auditor:    { bg: "bg-cyan-50",   border: "border-cyan-200",   text: "text-cyan-700",   dot: "bg-cyan-500" },
  researcher: { bg: "bg-teal-50",   border: "border-teal-200",   text: "text-teal-700",   dot: "bg-teal-500" },
  explorer:   { bg: "bg-sky-50",    border: "border-sky-200",    text: "text-sky-700",    dot: "bg-sky-500" },
  reviewer:   { bg: "bg-violet-50", border: "border-violet-200", text: "text-violet-700", dot: "bg-violet-500" },
  tester:     { bg: "bg-pink-50",   border: "border-pink-200",   text: "text-pink-700",   dot: "bg-pink-500" },
  user:       { bg: "bg-white",     border: "border-gray-300",   text: "text-gray-900",   dot: "bg-gray-700" },
};

const DEFAULT_STYLE = { bg: "bg-gray-50", border: "border-gray-200", text: "text-gray-700", dot: "bg-gray-500" };

function getStyle(sender: string) {
  return AGENT_STYLES[sender] || DEFAULT_STYLE;
}

export function RoomDetail({ roomId, onClose }: Props) {
  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [roomStatus, setRoomStatus] = useState<string>("running");
  const [input, setInput] = useState("");
  const [actionLoading, setActionLoading] = useState(false);
  const [showRefs, setShowRefs] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // Load existing messages + room status
  useEffect(() => {
    setMessages([]);
    setExpanded(new Set());
    fetch(`/rooms/${roomId}/messages`)
      .then((res) => (res.ok ? res.json() : []))
      .then((msgs: RoomMessage[]) => {
        if (msgs.length > 0) setMessages(msgs);
      })
      .catch(() => {});
    fetch(`/rooms/${roomId}`)
      .then((res) => (res.ok ? res.json() : {}))
      .then((data: RoomInfo) => {
        if (data.status) setRoomStatus(data.status);
      })
      .catch(() => {});
  }, [roomId]);

  // Listen for new messages + status via WebSocket
  const handleEvent = useCallback(
    (event: WsEvent) => {
      if (event.room_id !== roomId) return;
      if (event.type === "room_message" && event.data) {
        const msg: RoomMessage = {
          sender: event.data.sender as string,
          content: event.data.content as string,
          type: event.data.type as string,
        };
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.sender === msg.sender && last.content === msg.content) return prev;
          return [...prev, msg];
        });
      } else if (event.type === "room_status" && event.data) {
        setRoomStatus(event.data.status as string);
      }
    },
    [roomId]
  );

  useWebSocket(handleEvent);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const toggleExpand = (index: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const roomAction = async (action: "pause" | "resume" | "stop") => {
    setActionLoading(true);
    try {
      await fetch(`/rooms/${roomId}/${action}`, { method: "POST" });
      // Refresh status
      const res = await fetch(`/rooms/${roomId}`);
      if (res.ok) {
        const data = await res.json();
        if (data.status) setRoomStatus(data.status);
      }
    } catch { /* ignore */ }
    setActionLoading(false);
  };

  const injectMessage = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    try {
      await fetch(`/rooms/${roomId}/inject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text, sender: "user" }),
      });
    } catch { /* ignore */ }
  };

  const isRunning = roomStatus === "running";
  const isPaused = roomStatus === "paused";
  const isActive = isRunning || isPaused;

  // Agent order for alternating sides
  const agentOrder: string[] = [];
  messages.forEach((m) => {
    if (!agentOrder.includes(m.sender)) agentOrder.push(m.sender);
  });

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-gray-200">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-700">Room Discussion</h3>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-xs text-gray-400">{roomId}</span>
              <span className="text-xs text-gray-400">{messages.length} msgs</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                isRunning ? "bg-green-100 text-green-700" :
                isPaused ? "bg-yellow-100 text-yellow-700" :
                roomStatus === "completed" ? "bg-blue-100 text-blue-700" :
                "bg-gray-100 text-gray-600"
              }`}>
                {roomStatus}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg">&times;</button>
        </div>

        {/* Control buttons */}
        <div className="flex gap-1.5 mt-2">
          {isRunning && (
            <button
              onClick={() => roomAction("pause")}
              disabled={actionLoading}
              className="px-2 py-1 text-[11px] bg-yellow-100 text-yellow-700 rounded hover:bg-yellow-200 disabled:opacity-50"
            >
              Pause
            </button>
          )}
          {isPaused && (
            <button
              onClick={() => roomAction("resume")}
              disabled={actionLoading}
              className="px-2 py-1 text-[11px] bg-green-100 text-green-700 rounded hover:bg-green-200 disabled:opacity-50"
            >
              Resume
            </button>
          )}
          {isActive && (
            <button
              onClick={() => roomAction("stop")}
              disabled={actionLoading}
              className="px-2 py-1 text-[11px] bg-red-100 text-red-700 rounded hover:bg-red-200 disabled:opacity-50"
            >
              Stop
            </button>
          )}
          <button
            onClick={() => setShowRefs(!showRefs)}
            className={`px-2 py-1 text-[11px] rounded ml-auto ${
              showRefs
                ? "bg-amber-200 text-amber-800"
                : "bg-amber-100 text-amber-700 hover:bg-amber-200"
            }`}
          >
            {showRefs ? "Discussion" : "References"}
          </button>
        </div>

        {/* Error state banner */}
        {["failed", "timeout", "budget_exceeded", "cancelled"].includes(roomStatus) && (
          <div className={`mt-2 px-2 py-1.5 rounded text-[11px] ${
            roomStatus === "failed" ? "bg-red-100 text-red-700" :
            roomStatus === "timeout" ? "bg-orange-100 text-orange-700" :
            roomStatus === "budget_exceeded" ? "bg-amber-100 text-amber-700" :
            "bg-gray-100 text-gray-600"
          }`}>
            {roomStatus === "failed" && "Room failed — an error occurred during execution"}
            {roomStatus === "timeout" && "Room timed out — discussion took too long"}
            {roomStatus === "budget_exceeded" && "Budget exceeded — token limit reached"}
            {roomStatus === "cancelled" && "Room was cancelled"}
          </div>
        )}

        {/* Agent legend */}
        <div className="flex flex-wrap gap-1 mt-2">
          {agentOrder.map((agent) => {
            const s = getStyle(agent);
            return (
              <span key={agent} className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded ${s.bg} ${s.text}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                {agent}
              </span>
            );
          })}
        </div>
      </div>

      {/* References Panel */}
      {showRefs && (
        <div className="flex-1 overflow-hidden">
          <ReferencesPanel roomId={roomId} onClose={() => setShowRefs(false)} />
        </div>
      )}

      {/* Messages */}
      {!showRefs && <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <p className="text-xs text-gray-400 text-center mt-8">Waiting for agent messages...</p>
        )}
        {messages.map((msg, i) => {
          const style = getStyle(msg.sender);
          const isExpanded = expanded.has(i);
          const isLong = msg.content.length > 500;
          const displayContent = isLong && !isExpanded ? msg.content.slice(0, 500) + "..." : msg.content;
          const isUser = msg.sender === "user";
          const agentIndex = agentOrder.indexOf(msg.sender);
          const isLeft = isUser ? false : agentIndex % 2 === 0;

          return (
            <div key={i} className={`flex ${isLeft ? "justify-start" : "justify-end"}`}>
              <div className={`max-w-[90%] rounded-2xl px-4 py-3 border ${style.bg} ${style.border} ${
                isLeft ? "rounded-tl-sm" : "rounded-tr-sm"
              }`}>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`w-2 h-2 rounded-full ${style.dot}`} />
                  <span className={`text-xs font-bold ${style.text}`}>{msg.sender}</span>
                  {msg.type === "backflow" && (
                    <span className="text-[10px] px-1 py-0.5 bg-purple-100 text-purple-600 rounded">sub-room result</span>
                  )}
                  {msg.type === "review" && (
                    <span className="text-[10px] px-1 py-0.5 bg-purple-100 text-purple-600 rounded">review</span>
                  )}
                  {msg.type === "system" && (
                    <span className="text-[10px] px-1 py-0.5 bg-gray-200 text-gray-600 rounded">injected</span>
                  )}
                  {msg.type === "opinion" && (
                    <span className="text-[10px] text-gray-400">
                      Round {Math.floor(i / Math.max(agentOrder.length, 1)) + 1}
                    </span>
                  )}
                </div>
                <div className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">{displayContent}</div>
                {isLong && (
                  <button onClick={() => toggleExpand(i)} className={`mt-2 text-xs font-medium ${style.text} hover:underline`}>
                    {isExpanded ? "Collapse" : "Show full message"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>}

      {/* Input bar for injecting messages */}
      {isActive && !showRefs && (
        <div className="border-t border-gray-200 p-2">
          <div className="flex gap-1.5">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && injectMessage()}
              placeholder="Insert a message into the discussion..."
              className="flex-1 border border-gray-300 rounded px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              onClick={injectMessage}
              disabled={!input.trim()}
              className="px-3 py-1.5 bg-gray-700 text-white rounded text-xs font-medium hover:bg-gray-800 disabled:opacity-50"
            >
              Inject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
