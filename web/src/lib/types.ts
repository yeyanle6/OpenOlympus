export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  layer: string;
  capabilities: string[];
  permissions: {
    write: boolean;
    execute: boolean;
    spawn_rooms: boolean;
  };
}

export interface RoomInfo {
  room_id: string;
  task: string;
  protocol: string;
  agents: string[];
  status: string;
}

export interface RoomMessage {
  id?: string;
  sender: string;
  content: string;
  type: string;
  timestamp?: string;
}

export interface LoopStatus {
  status: string;
  cycle_count: number;
  error_count: number;
  last_cost: number;
  total_cost: number;
}

export interface WsEvent {
  type: string;
  room_id?: string;
  data?: Record<string, unknown>;
  rooms_status?: RoomInfo[];
}

// ── Camera / MediaPipe types ──────────────────────────────────

export interface Landmark {
  x: number;
  y: number;
  z: number;
  visibility?: number;
}

export interface HandLandmarks {
  landmarks: Landmark[];
  handedness: "Left" | "Right";
}

export interface CameraLandmarkEvent {
  type: "landmarks";
  data: {
    hands: HandLandmarks[];
    timestamp: number;
  };
}

export interface CameraGestureEvent {
  type: "gesture";
  data: {
    gesture: string;
    confidence: number;
    hand: "Left" | "Right";
    timestamp: number;
  };
}

export interface CameraStatusEvent {
  type: "camera_status";
  data: {
    status: "active" | "inactive" | "error";
    message?: string;
  };
}
