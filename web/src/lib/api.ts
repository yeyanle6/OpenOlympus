import type { AgentInfo, RoomInfo, LoopStatus } from "./types";

const BASE = "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path}: ${res.status}`);
  return res.json();
}

export const api = {
  health: () => get<{ status: string }>("/health"),
  agents: () => get<AgentInfo[]>("/agents"),
  rooms: () => get<RoomInfo[]>("/rooms"),
  room: (id: string) => get<RoomInfo>(`/rooms/${id}`),

  directorChat: (message: string) =>
    post<{ reply: string; room_id?: string; protocol?: string; agents?: string[] }>(
      "/director/chat",
      { message }
    ),

  loopStatus: () => get<LoopStatus>("/loop/status"),
  loopStart: () => post<{ ok: boolean }>("/loop/start"),
  loopStop: () => post<{ ok: boolean }>("/loop/stop"),
  loopCycles: (limit = 20) => get<unknown[]>(`/loop/cycles?limit=${limit}`),

  consensus: () => get<{ content: string }>("/consensus"),
  consensusHistory: (limit = 10) =>
    get<{ timestamp: string; content: string }[]>(
      `/consensus/history?limit=${limit}`
    ),
  decisions: (limit = 20) => get<unknown[]>(`/decisions?limit=${limit}`),

  speaker: () =>
    get<{ busy: boolean; current_speaker: string; current_room: string; queue_size: number }>(
      "/speaker"
    ),
};
