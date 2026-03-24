import { useRef, useState, useCallback, useEffect } from "react";
import { HandLandmarker, FilesetResolver } from "@mediapipe/tasks-vision";
import type { HandLandmarks } from "./types";

export interface UseCameraOptions {
  /** Called each frame with detected hand landmarks. */
  onLandmarks?: (hands: HandLandmarks[], timestamp: number) => void;
  /** Max frames per second for detection. Default 30. */
  maxFps?: number;
}

export interface UseCameraResult {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  isActive: boolean;
  isLoading: boolean;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
}

const WASM_CDN =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm";

export function useCamera(options: UseCameraOptions = {}): UseCameraResult {
  const { onLandmarks, maxFps = 30 } = options;
  const onLandmarksRef = useRef(onLandmarks);
  onLandmarksRef.current = onLandmarks;

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const handLandmarkerRef = useRef<HandLandmarker | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number>(0);

  const [isActive, setIsActive] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const minInterval = 1000 / maxFps;

  const drawLandmarks = useCallback(
    (hands: HandLandmarks[], width: number, height: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      canvas.width = width;
      canvas.height = height;
      ctx.clearRect(0, 0, width, height);

      // Hand landmark connections (MediaPipe hand topology)
      const connections = [
        [0, 1], [1, 2], [2, 3], [3, 4],       // thumb
        [0, 5], [5, 6], [6, 7], [7, 8],       // index
        [0, 9], [9, 10], [10, 11], [11, 12],  // middle
        [0, 13], [13, 14], [14, 15], [15, 16],// ring
        [0, 17], [17, 18], [18, 19], [19, 20],// pinky
        [5, 9], [9, 13], [13, 17],            // palm
      ];

      const colors = { Left: "#00FF00", Right: "#FF6600" };

      for (const hand of hands) {
        const color = colors[hand.handedness] || "#00FF00";

        // Draw connections
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        for (const [a, b] of connections) {
          const pa = hand.landmarks[a];
          const pb = hand.landmarks[b];
          if (!pa || !pb) continue;
          ctx.beginPath();
          ctx.moveTo(pa.x * width, pa.y * height);
          ctx.lineTo(pb.x * width, pb.y * height);
          ctx.stroke();
        }

        // Draw points
        ctx.fillStyle = color;
        for (const lm of hand.landmarks) {
          ctx.beginPath();
          ctx.arc(lm.x * width, lm.y * height, 4, 0, 2 * Math.PI);
          ctx.fill();
        }
      }
    },
    []
  );

  const detect = useCallback(
    (lastTime: number) => {
      const video = videoRef.current;
      const handLandmarker = handLandmarkerRef.current;
      if (!video || !handLandmarker || video.paused || video.ended) return;

      const now = performance.now();
      if (now - lastTime < minInterval) {
        rafRef.current = requestAnimationFrame(() => detect(lastTime));
        return;
      }

      const result = handLandmarker.detectForVideo(video, now);

      const hands: HandLandmarks[] = (result.landmarks || []).map(
        (landmarks, i) => ({
          landmarks: landmarks.map((lm) => ({
            x: lm.x,
            y: lm.y,
            z: lm.z,
          })),
          handedness:
            (result.handednesses?.[i]?.[0]?.categoryName as
              | "Left"
              | "Right") || "Right",
        })
      );

      drawLandmarks(hands, video.videoWidth, video.videoHeight);

      if (hands.length > 0) {
        onLandmarksRef.current?.(hands, now);
      }

      rafRef.current = requestAnimationFrame(() => detect(now));
    },
    [minInterval, drawLandmarks]
  );

  const start = useCallback(async () => {
    setError(null);
    setIsLoading(true);

    try {
      // Initialize MediaPipe
      if (!handLandmarkerRef.current) {
        const vision = await FilesetResolver.forVisionTasks(WASM_CDN);
        handLandmarkerRef.current = await HandLandmarker.createFromOptions(
          vision,
          {
            baseOptions: {
              modelAssetPath:
                "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
              delegate: "GPU",
            },
            runningMode: "VIDEO",
            numHands: 2,
          }
        );
      }

      // Get camera stream
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" },
      });
      streamRef.current = stream;

      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        await video.play();
        setIsActive(true);
        setIsLoading(false);
        rafRef.current = requestAnimationFrame(() => detect(0));
      }
    } catch (err) {
      const message =
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Camera access denied. Please allow camera permissions and try again."
          : `Camera error: ${err instanceof Error ? err.message : String(err)}`;
      setError(message);
      setIsLoading(false);
    }
  }, [detect]);

  const stop = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = 0;

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }

    const video = videoRef.current;
    if (video) {
      video.srcObject = null;
    }

    setIsActive(false);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cancelAnimationFrame(rafRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      handLandmarkerRef.current?.close();
    };
  }, []);

  return { videoRef, canvasRef, isActive, isLoading, error, start, stop };
}
