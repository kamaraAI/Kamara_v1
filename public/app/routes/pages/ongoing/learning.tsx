import { useEffect, useState, useRef, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import {
  Award,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Crown,
  FileBadge,
  Home,
  LibraryBig,
  LoaderCircle,
  Phone,
  PhoneOff,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Mic,
  Send,
  Share2,
  Star,
  UserRound,
  Video,
  MicOff,
} from "lucide-react";
import { type Editor, serializeTldrawJson, toRichText } from "tldraw";

import LiveBoard, { applyBoardCommand, type BoardCommand } from "./liveBoard";
import ModuleLibrary, { buildModulesFromBackendResponse, placeholderModules, type LearningModule } from "../dash-component/mod-lib";
import { getGeneratedCourseStorageKey } from "../genie-api";
import { consumeSubscriptionFeature, isSubscriptionRequiredError } from "../../subscription-api";
import { isLoggedIn } from "../../auth/session";
import { getWebSocketBaseUrl } from "../../api-config";

type GeneratedCourseSession = {
  session_id?: string;
  course?: {
    title?: string;
    description?: string;
  };
  prompt?: string;
  generatedCourse?: any;
};

type TutorEvent = {
  type: string;
  title: string;
  detail?: string;
};

type AssistantAudioPayload = {
  type: "assistant_audio";
  mime_type?: string | null;
  data: string;
};

const fallbackModules = placeholderModules;

const WS_BASE_URL = getWebSocketBaseUrl();
const COURSE_MODULES_WS_ENDPOINT = `${WS_BASE_URL}/courses/hrm/modules`;
function isAudioStreamingSupported() {
  return (
    typeof window !== "undefined" &&
    "WebSocket" in window &&
    "AudioContext" in window &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

function getActiveSessionId() {
  if (typeof window === "undefined") {
    return null;
  }

  const params = new URLSearchParams(window.location.search);
  return params.get("session_id") ?? localStorage.getItem("active_session_id") ?? getStoredGeneratedSessionId();
}

function getGeneratedSessionId(generatedSession: GeneratedCourseSession | null) {
  const generatedCourse = generatedSession?.generatedCourse;

  return (
    generatedCourse?.session_id ??
    generatedCourse?.data?.session_id ??
    generatedCourse?.id ??
    generatedSession?.session_id ??
    null
  );
}

function getStoredGeneratedSessionId() {
  if (typeof window === "undefined") {
    return null;
  }

  const storageKey = getGeneratedCourseStorageKey();
  const storedCourse = sessionStorage.getItem(storageKey);

  if (!storedCourse) {
    return null;
  }

  try {
    const parsedCourse = JSON.parse(storedCourse) as GeneratedCourseSession;
    return getGeneratedSessionId(parsedCourse);
  } catch {
    return null;
  }
}

function base64ToUint8Array(value: string) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  return bytes;
}

function parseSampleRate(mimeType?: string | null) {
  const match = mimeType?.match(/rate=(\d+)/i);
  return match?.[1] ? Number(match[1]) : 24000;
}

function decodePcm16ToAudioBuffer(context: AudioContext, bytes: Uint8Array, sampleRate: number) {
  const sampleCount = Math.floor(bytes.byteLength / 2);
  const audioBuffer = context.createBuffer(1, sampleCount, sampleRate);
  const channel = audioBuffer.getChannelData(0);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  for (let index = 0; index < sampleCount; index += 1) {
    const sample = view.getInt16(index * 2, true);
    channel[index] = sample < 0 ? sample / 0x8000 : sample / 0x7fff;
  }

  return audioBuffer;
}

function isWavAudio(bytes: Uint8Array) {
  return (
    bytes.byteLength >= 12 &&
    String.fromCharCode(bytes[0] ?? 0, bytes[1] ?? 0, bytes[2] ?? 0, bytes[3] ?? 0) === "RIFF" &&
    String.fromCharCode(bytes[8] ?? 0, bytes[9] ?? 0, bytes[10] ?? 0, bytes[11] ?? 0) === "WAVE"
  );
}

function downsampleBuffer(inputBuffer: Float32Array, sourceRate: number, targetRate = 16000) {
  if (targetRate === sourceRate) {
    return inputBuffer;
  }

  const ratio = sourceRate / targetRate;
  const newLength = Math.round(inputBuffer.length / ratio);
  const result = new Float32Array(newLength);

  let sourceOffset = 0;
  for (let index = 0; index < newLength; index += 1) {
    const nextSourceOffset = Math.round((index + 1) * ratio);
    let sum = 0;
    let count = 0;

    for (let cursor = sourceOffset; cursor < nextSourceOffset && cursor < inputBuffer.length; cursor += 1) {
      sum += inputBuffer[cursor] ?? 0;
      count += 1;
    }

    result[index] = count > 0 ? sum / count : 0;
    sourceOffset = nextSourceOffset;
  }

  return result;
}

function float32To16BitPCM(input: Float32Array) {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);

  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index] ?? 0));
    // PCM16 maps [-1, 1] to signed 16-bit integers. Negative values use 0x8000, positive use 0x7fff.
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }

  return buffer;
}

function appendFloat32Chunks(existing: Float32Array, next: Float32Array) {
  if (existing.length === 0) {
    return new Float32Array(next);
  }

  const merged = new Float32Array(existing.length + next.length);
  merged.set(existing, 0);
  merged.set(next, existing.length);
  return merged;
}

function trimFloat32Buffer(input: Float32Array, maxLength: number) {
  if (input.length <= maxLength) {
    return input;
  }

  return input.slice(input.length - maxLength);
}

function calculateRms(inputBuffer: Float32Array) {
  if (inputBuffer.length === 0) {
    return 0;
  }

  let sumSquares = 0;
  for (let index = 0; index < inputBuffer.length; index += 1) {
    const sample = inputBuffer[index] ?? 0;
    sumSquares += sample * sample;
  }

  return Math.sqrt(sumSquares / inputBuffer.length);
}

async function blobToDataUrl(blob: Blob) {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read image blob."));
    reader.readAsDataURL(blob);
  });
}

export function meta() {
  return [
    { title: "Learning | Kamara AI" },
    {
      name: "description",
      content: "Course progress .",
    },
  ];
}

export default function DashboardPage() {
  const [modules, setModules] = useState<LearningModule[]>(fallbackModules);
  const [generatedSession, setGeneratedSession] = useState<GeneratedCourseSession | null>(null);
  const [selectedModuleId, setSelectedModuleId] = useState(fallbackModules[0]?.id ?? "");
  const [chatInput, setChatInput] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [isMicConnecting, setIsMicConnecting] = useState(false);
  const [isTutorConnected, setIsTutorConnected] = useState(false);
  const [isTutorConnecting, setIsTutorConnecting] = useState(false);
  const [isLearningPanelOpen, setIsLearningPanelOpen] = useState(true);
  const [isLibraryOpen, setIsLibraryOpen] = useState(false);
  const [boardEditor, setBoardEditor] = useState<Editor | null>(null);
  const [tutorNotice, setTutorNotice] = useState<{ type: "info" | "warning" | "error"; message: string } | null>(null);
  const navigate = useNavigate();

  const moduleSocketRef = useRef<WebSocket | null>(null);
  const tutorSocketRef = useRef<WebSocket | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const audioProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const audioGainRef = useRef<GainNode | null>(null);
  const assistantAudioContextRef = useRef<AudioContext | null>(null);
  const assistantAudioNextTimeRef = useRef<number>(0);
  const assistantAudioSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const boardSnapshotTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastCanvasSnapshotTextRef = useRef<string>("");
  const isSendingCanvasSnapshotRef = useRef(false);
  const micChunkCountRef = useRef(0);
  const micPendingSamplesRef = useRef<Float32Array>(new Float32Array(0));
  const micPreRollSamplesRef = useRef<Float32Array>(new Float32Array(0));
  const micIsSpeakingRef = useRef(false);
  const micLastVoiceAtRef = useRef(0);
  const pendingBoardCommandsRef = useRef<unknown[]>([]);
  const previousSessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    const storageKey = getGeneratedCourseStorageKey();
    const storedCourse = sessionStorage.getItem(storageKey);

    if (!storedCourse) {
      return;
    }

    try {
      const parsedCourse = JSON.parse(storedCourse) as GeneratedCourseSession;
      const generatedModules = buildModulesFromBackendResponse(parsedCourse.generatedCourse);
      const sessionId = getGeneratedSessionId(parsedCourse);

      setGeneratedSession(parsedCourse);

      if (sessionId) {
        localStorage.setItem("active_session_id", String(sessionId));
      }

      if (generatedModules.length > 0) {
        setModules(generatedModules);
        setSelectedModuleId(generatedModules[0].id);
      }
    } catch {
      sessionStorage.removeItem(storageKey);
    }
  }, []);

  const activeSessionId = getGeneratedSessionId(generatedSession) ?? getActiveSessionId() ?? getStoredGeneratedSessionId();

  useEffect(() => {
    const previousSessionId = previousSessionIdRef.current;
    previousSessionIdRef.current = activeSessionId ?? null;

    if (!previousSessionId || previousSessionId === activeSessionId) {
      return;
    }

    disconnectTutorSession();
    pendingBoardCommandsRef.current = [];
    lastCanvasSnapshotTextRef.current = "";
    isSendingCanvasSnapshotRef.current = false;
  }, [activeSessionId]);

  useEffect(() => {
    if (generatedSession) {
      return;
    }

    moduleSocketRef.current = new WebSocket(COURSE_MODULES_WS_ENDPOINT);
    const currentSocket = moduleSocketRef.current;

    currentSocket.addEventListener("open", () => {
      currentSocket.send(JSON.stringify({ action: "get_modules" }));
    });

    currentSocket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (payload.type === "course_modules" && Array.isArray(payload.modules)) {
          const backendModules = buildModulesFromBackendResponse(payload);
          setModules(backendModules);
          setSelectedModuleId(backendModules[0]?.id ?? "");
        }

        if (payload.type === "chat_response") {
          return;
        }
      } catch {
        setModules(fallbackModules);
        setSelectedModuleId(fallbackModules[0]?.id ?? "");
      }
    });

    currentSocket.addEventListener("error", () => {
      setModules(fallbackModules);
      setSelectedModuleId(fallbackModules[0]?.id ?? "");
    });

    return () => {
      if (currentSocket.readyState === WebSocket.OPEN) {
        currentSocket.send(JSON.stringify({ action: "close" }));
      }
      currentSocket.close();
    };
  }, [generatedSession]);

  const clearBoardSnapshotTimer = () => {
    if (boardSnapshotTimerRef.current) {
      clearTimeout(boardSnapshotTimerRef.current);
      boardSnapshotTimerRef.current = null;
    }
  };

  const ensureAssistantAudioContext = async () => {
    if (!assistantAudioContextRef.current) {
      assistantAudioContextRef.current = new AudioContext({ sampleRate: 24000 });
    }

    if (assistantAudioContextRef.current.state === "suspended") {
      await assistantAudioContextRef.current.resume();
    }

    if (assistantAudioNextTimeRef.current === 0) {
      assistantAudioNextTimeRef.current = assistantAudioContextRef.current.currentTime;
    }

    return assistantAudioContextRef.current;
  };

  const stopAssistantAudio = () => {
    assistantAudioSourcesRef.current.forEach((source) => {
      try {
        source.stop();
        source.disconnect();
      } catch {
        // The source may already be finished or disconnected.
      }
    });
    assistantAudioSourcesRef.current.clear();
    assistantAudioNextTimeRef.current = 0;

    assistantAudioContextRef.current?.close().catch(() => undefined);
    assistantAudioContextRef.current = null;
  };

  const queueAssistantBuffer = async (audioBuffer: AudioBuffer) => {
    const context = await ensureAssistantAudioContext();
    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);

    const startTime = Math.max(context.currentTime, assistantAudioNextTimeRef.current);
    assistantAudioSourcesRef.current.add(source);
    source.onended = () => {
      assistantAudioSourcesRef.current.delete(source);
    };
    source.start(startTime);
    assistantAudioNextTimeRef.current = startTime + audioBuffer.duration;
  };

  const playAssistantBinaryAudio = async (chunk: ArrayBuffer) => {
    if (chunk.byteLength === 0) {
      return;
    }

    try {
      const context = await ensureAssistantAudioContext();
      const bytes = new Uint8Array(chunk);

      if (isWavAudio(bytes)) {
        try {
          const decoded = await context.decodeAudioData(chunk.slice(0));
          await queueAssistantBuffer(decoded);
          return;
        } catch {
          // Fall back to PCM decoding below if the chunk is raw PCM rather than WAV.
        }
      }

      const buffer = decodePcm16ToAudioBuffer(context, bytes, 24000);
      await queueAssistantBuffer(buffer);
    } catch (error) {
      console.error("Could not play assistant audio:", error);
    }
  };

  const playAssistantAudio = async (payload: AssistantAudioPayload) => {
    if (!payload.data) {
      return;
    }

    try {
      const bytes = base64ToUint8Array(payload.data);
      const context = await ensureAssistantAudioContext();

      if (isWavAudio(bytes)) {
        const decoded = await context.decodeAudioData(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        await queueAssistantBuffer(decoded);
        return;
      }

      const sampleRate = parseSampleRate(payload.mime_type);
      const buffer = decodePcm16ToAudioBuffer(context, bytes, sampleRate);
      await queueAssistantBuffer(buffer);
    } catch (error) {
      console.error("Could not play assistant audio:", error);
    }
  };

  const extractBoardCommand = (payload: unknown) => {
    if (!payload || typeof payload !== "object") {
      return null;
    }

    const candidate = payload as {
      type?: string;
      action?: string;
      payload?: unknown;
      data?: unknown;
    };

    if (candidate.type === "tool_call") {
      return candidate.payload ?? candidate.data ?? null;
    }

    if (candidate.action) {
      return payload;
    }

    if (candidate.payload && typeof candidate.payload === "object") {
      return candidate.payload;
    }

    if (candidate.data && typeof candidate.data === "object") {
      return candidate.data;
    }

    return null;
  };

  const applyBoardPayload = (payload: unknown) => {
    const command = extractBoardCommand(payload);

    if (!boardEditor || !command) {
      if (payload && typeof payload === "object") {
        pendingBoardCommandsRef.current.push(payload);
      }
      return;
    }

    const candidate = command as Partial<BoardCommand> & {
      action?: string;
      data?: unknown;
    };

    if (!candidate.action) {
      return;
    }

    if (
      candidate.action === "draw_shape" ||
      candidate.action === "write_text" ||
      candidate.action === "move_shape" ||
      candidate.action === "resize_item" ||
      candidate.action === "delete_shape" ||
      candidate.action === "clear_board" ||
      candidate.action === "draw_line"
    ) {
      try {
        applyBoardCommand(boardEditor, candidate as BoardCommand);
      } catch (error) {
        console.error("Could not apply tutor board command", error);
      }
    }
  };

  const normalizeCanvasShape = (shape: any) => {
    if (!shape || typeof shape !== "object") {
      return shape;
    }

    const normalized = {
      ...shape,
      props: { ...(shape.props ?? {}) },
    };
    const legacyText = normalized.text;
    if ("text" in normalized) {
      delete normalized.text;
    }

    if (normalized.type === "text") {
      const richTextSource = normalized.props.text ?? legacyText;
      if (!("richText" in normalized.props)) {
        normalized.props.richText = toRichText(String(richTextSource ?? ""));
      }
      delete normalized.props.text;
    }

    if (normalized.type === "geo") {
      delete normalized.props.text;
    }

    if (normalized.type === "arrow") {
      if (!("richText" in normalized.props)) {
        normalized.props.richText = toRichText(String(normalized.props.text ?? legacyText ?? ""));
      }
      delete normalized.props.text;

      const start = normalized.props.start;
      if (!start || typeof start !== "object" || Array.isArray(start)) {
        normalized.props.start = { x: 0, y: 0 };
      } else {
        normalized.props.start = {
          x: Number((start as { x?: unknown }).x ?? 0) || 0,
          y: Number((start as { y?: unknown }).y ?? 0) || 0,
        };
      }

      const end = normalized.props.end;
      if (!end || typeof end !== "object" || Array.isArray(end)) {
        normalized.props.end = { x: 140, y: 0 };
      } else {
        normalized.props.end = {
          x: Number((end as { x?: unknown }).x ?? 140) || 0,
          y: Number((end as { y?: unknown }).y ?? 0) || 0,
        };
      }
    }

    return normalized;
  };

  const executeCanvasScript = (javascriptCode: string) => {
    if (!boardEditor || !javascriptCode.trim()) {
      return;
    }

    try {
      const safeEditor = new Proxy(boardEditor, {
        get(target, prop, receiver) {
          const value = Reflect.get(target, prop, receiver);

          if ((prop === "createShape" || prop === "createShapes") && typeof value === "function") {
            return (shape: any) => {
              if (Array.isArray(shape)) {
                return value.call(target, shape.map(normalizeCanvasShape));
              }

              return value.call(target, normalizeCanvasShape(shape));
            };
          }

          if ((prop === "updateShape" || prop === "updateShapes") && typeof value === "function") {
            return (shape: any) => {
              if (Array.isArray(shape)) {
                return value.call(target, shape.map(normalizeCanvasShape));
              }

              return value.call(target, normalizeCanvasShape(shape));
            };
          }

          return typeof value === "function" ? value.bind(target) : value;
        },
      });

      const runner = new Function("editor", javascriptCode);
      runner(safeEditor);
    } catch (error) {
      console.error("Could not execute canvas JavaScript:", error);
      setTutorNotice({
        type: "warning",
        message: "A whiteboard command used an older text format. The board stayed open, but that command was skipped.",
      });
    }
  };

  useEffect(() => {
    if (!boardEditor || pendingBoardCommandsRef.current.length === 0) {
      return;
    }

    const queuedCommands = [...pendingBoardCommandsRef.current];
    pendingBoardCommandsRef.current = [];

    queuedCommands.forEach((command) => {
      applyBoardPayload(command);
    });
  }, [boardEditor]);

  const stopMicStream = () => {
    const socket = tutorSocketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      while (micPendingSamplesRef.current.length > 0) {
        const chunk = micPendingSamplesRef.current.slice(0, 1600);
        micPendingSamplesRef.current = micPendingSamplesRef.current.slice(1600);

        if (chunk.length > 0) {
          socket.send(float32To16BitPCM(chunk));
        }
      }

      socket.send(JSON.stringify({ type: "audio_stream_end" }));
    }

    audioProcessorRef.current?.disconnect();
    audioSourceRef.current?.disconnect();
    audioGainRef.current?.disconnect();
    audioContextRef.current?.close().catch(() => undefined);

    audioProcessorRef.current = null;
    audioSourceRef.current = null;
    audioGainRef.current = null;
    audioContextRef.current = null;
    micPendingSamplesRef.current = new Float32Array(0);
    micPreRollSamplesRef.current = new Float32Array(0);
    micIsSpeakingRef.current = false;
    micLastVoiceAtRef.current = 0;

    audioStreamRef.current?.getTracks().forEach((track) => track.stop());
    audioStreamRef.current = null;

    setIsRecording(false);
    setIsMicConnecting(false);
  };

  const disconnectTutorSession = () => {
    clearBoardSnapshotTimer();
    stopMicStream();
    stopAssistantAudio();
    pendingBoardCommandsRef.current = [];
    lastCanvasSnapshotTextRef.current = "";
    isSendingCanvasSnapshotRef.current = false;

    const socket = tutorSocketRef.current;
    tutorSocketRef.current = null;
    setIsTutorConnected(false);
    setIsTutorConnecting(false);

    if (socket && socket.readyState !== WebSocket.CLOSED) {
      socket.close();
    }
  };

  const sendCanvasSnapshots = async () => {
    const socket = tutorSocketRef.current;

    if (!boardEditor || !socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }

    if (isSendingCanvasSnapshotRef.current) {
      return;
    }

    isSendingCanvasSnapshotRef.current = true;

    try {
      const snapshot = await serializeTldrawJson(boardEditor);
      if (snapshot === lastCanvasSnapshotTextRef.current) {
        return;
      }

      lastCanvasSnapshotTextRef.current = snapshot;

      socket.send(
        JSON.stringify({
          type: "canvas_snapshot_text",
          data: snapshot,
        })
      );

      const shapeIds = [...boardEditor.getCurrentPageShapeIds()];
      if (shapeIds.length === 0) {
        return;
      }

      const imageResult = await boardEditor.toImage(shapeIds, {
        bounds: boardEditor.getViewportPageBounds(),
        format: "png",
        scale: 1,
      });

      if (!imageResult?.blob) {
        return;
      }

      const image = await blobToDataUrl(imageResult.blob);

      socket.send(
        JSON.stringify({
          type: "canvas_snapshot_vision",
          image,
        })
      );
    } catch (error) {
      console.error("Could not serialize board snapshot:", error);
    } finally {
      isSendingCanvasSnapshotRef.current = false;
    }
  };

  const scheduleBoardSnapshot = () => {};

  const connectTutorSession = async () => {
    if (typeof window === "undefined" || !("WebSocket" in window)) {
      setTutorNotice({
        type: "error",
        message: "Your browser does not support WebSocket streaming. Try a current browser with microphone support.",
      });
      return false;
    }

    const token = localStorage.getItem("access_token");
    const sessionId = activeSessionId;

    if (!token) {
      setTutorNotice({
        type: "error",
        message: "Your session is missing. Please sign in again before starting the live call.",
      });
      return false;
    }

    if (!sessionId) {
      setTutorNotice({
        type: "warning",
        message: "No active classroom session was found yet. Generate a course first, then start the call.",
      });
      return false;
    }

    if (tutorSocketRef.current?.readyState === WebSocket.OPEN) {
      return true;
    }

    if (tutorSocketRef.current?.readyState === WebSocket.CONNECTING) {
      return true;
    }

    setIsTutorConnecting(true);

    return await new Promise<boolean>((resolve) => {
      const socket = new WebSocket(
        `${WS_BASE_URL}/live?token=${encodeURIComponent(token)}&session_id=${encodeURIComponent(sessionId)}`
      );
      socket.binaryType = "arraybuffer";
      tutorSocketRef.current = socket;

      socket.onopen = () => {
        console.info("[Tutor WS] Live websocket opened", {
          sessionId,
          socketUrl: socket.url,
        });
        setIsTutorConnected(true);
        setIsTutorConnecting(false);
        setTutorNotice({
          type: "info",
          message: "Live call connected. You can now turn on the microphone to speak with the tutor.",
        });
        socket.send(
          JSON.stringify({
            action: "start_session",
            session_id: sessionId,
          })
        );

        void ensureAssistantAudioContext();
        resolve(true);
      };

      socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          void playAssistantBinaryAudio(event.data);
          return;
        }

        if (event.data instanceof Blob) {
          void event.data.arrayBuffer().then((buffer) => playAssistantBinaryAudio(buffer));
          return;
        }

        if (typeof event.data !== "string") {
          return;
        }

        try {
          const payload = JSON.parse(event.data);

          if (payload?.action === "stop_audio_playback" || payload?.type === "interrupted") {
            stopAssistantAudio();
            return;
          }

        if (payload.type === "system_error") {
          setTutorNotice({
            type: "error",
            message: payload.content ?? payload.detail ?? "Tutor engine error",
          });
          disconnectTutorSession();
          return;
        }

          if (payload.type === "assistant_audio") {
            void playAssistantAudio(payload as AssistantAudioPayload);
            return;
          }

          if (payload.type === "tool_call") {
            applyBoardPayload(payload);
            return;
          }

          if (
            payload &&
            typeof payload === "object" &&
            "action" in payload &&
            typeof payload.action === "string"
          ) {
            applyBoardPayload(payload);
            return;
          }

        } catch {
          return;
        }
      };

      socket.onerror = () => {
        console.error("Tutor websocket error.");
        disconnectTutorSession();
        resolve(false);
      };

      socket.onclose = () => {
        clearBoardSnapshotTimer();
        stopMicStream();

        if (tutorSocketRef.current === socket) {
          tutorSocketRef.current = null;
        }

        setIsTutorConnected(false);
        setIsTutorConnecting(false);
      };
    });
  };

  useEffect(() => {
    if (!boardEditor || !isTutorConnected) {
      return;
    }

    const removeListener = boardEditor.store.listen(
      () => {
        scheduleBoardSnapshot();
      },
      {
        scope: "document",
      }
    );

    return () => {
      removeListener();
      clearBoardSnapshotTimer();
    };
  }, [boardEditor, isTutorConnected]);

  useEffect(() => {
    return () => {
      disconnectTutorSession();
      if (moduleSocketRef.current?.readyState === WebSocket.OPEN) {
        moduleSocketRef.current.send(JSON.stringify({ action: "close" }));
      }
      moduleSocketRef.current?.close();
    };
  }, []);

  const handleMicToggle = async () => {
    if (!isAudioStreamingSupported()) {
      setTutorNotice({
        type: "error",
        message: "Your browser does not support audio streaming. Try a different browser with microphone support.",
      });
      return;
    }

    if (isRecording || isMicConnecting) {
      stopMicStream();
      stopAssistantAudio();
      setTutorNotice(null);
      return;
    }

    if (!isTutorConnected || tutorSocketRef.current?.readyState !== WebSocket.OPEN) {
      setTutorNotice({
        type: "warning",
        message: "Start the live call first, then turn on the microphone to join the session.",
      });
      return;
    }

    setTutorNotice({
      type: "info",
      message: "Requesting microphone access. Your browser may ask for permission to use the mic.",
    });
    setIsMicConnecting(true);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      audioStreamRef.current = stream;

      const socket = tutorSocketRef.current;

      if (!socket || socket.readyState !== WebSocket.OPEN) {
        stopMicStream();
        return;
      }

      const audioContext = new AudioContext({ sampleRate: 16000 });
      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(512, 1, 1);
      const silentGain = audioContext.createGain();
      silentGain.gain.value = 0;
      const targetChunkSamples = 1600;
      const preRollSamples = 2400;
      const voiceThreshold = 0.006;
      const silenceTimeoutMs = 550;

      processor.onaudioprocess = (event) => {
        if (socket.readyState !== WebSocket.OPEN) {
          return;
        }

        const input = event.inputBuffer.getChannelData(0);
        const downsampled = downsampleBuffer(input, audioContext.sampleRate, 16000);

        if (downsampled.length === 0) {
          return;
        }

        const now = Date.now();
        const rms = calculateRms(downsampled);
        const isVoice = rms >= voiceThreshold;

        micPreRollSamplesRef.current = trimFloat32Buffer(
          appendFloat32Chunks(micPreRollSamplesRef.current, downsampled),
          preRollSamples
        );

        if (isVoice) {
          if (!micIsSpeakingRef.current) {
            micPendingSamplesRef.current = appendFloat32Chunks(micPreRollSamplesRef.current, micPendingSamplesRef.current);
            micPreRollSamplesRef.current = new Float32Array(0);
            micIsSpeakingRef.current = true;
          }

          micLastVoiceAtRef.current = now;
          micPendingSamplesRef.current = appendFloat32Chunks(micPendingSamplesRef.current, downsampled);

          while (micPendingSamplesRef.current.length >= targetChunkSamples) {
            const chunk = micPendingSamplesRef.current.slice(0, targetChunkSamples);
            micPendingSamplesRef.current = micPendingSamplesRef.current.slice(targetChunkSamples);
            socket.send(float32To16BitPCM(chunk));

            micChunkCountRef.current += 1;
            if (micChunkCountRef.current % 20 === 0) {
              console.info("[Tutor WS] Sent mic chunk", {
                chunkNumber: micChunkCountRef.current,
                downsampledSamples: chunk.length,
                byteLength: chunk.length * 2,
              });
            }
          }
        } else if (micIsSpeakingRef.current && now - micLastVoiceAtRef.current >= silenceTimeoutMs) {
          while (micPendingSamplesRef.current.length > 0) {
            const chunk = micPendingSamplesRef.current.slice(0, targetChunkSamples);
            micPendingSamplesRef.current = micPendingSamplesRef.current.slice(targetChunkSamples);

            if (chunk.length > 0) {
              socket.send(float32To16BitPCM(chunk));
            }
          }

          socket.send(JSON.stringify({ type: "audio_stream_end" }));
          micIsSpeakingRef.current = false;
          micLastVoiceAtRef.current = 0;
          micPreRollSamplesRef.current = new Float32Array(0);
        }
      };

      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(audioContext.destination);

      audioContextRef.current = audioContext;
      audioSourceRef.current = source;
      audioProcessorRef.current = processor;
      audioGainRef.current = silentGain;

      setIsRecording(true);
      setIsMicConnecting(false);
      setTutorNotice({
        type: "info",
        message: "Microphone is live. Speak normally and the assistant will listen through the call.",
      });
    } catch (error) {
      console.error("Could not get microphone access:", error);
      stopMicStream();
      setTutorNotice({
        type: "error",
        message: "Could not access your microphone. Please allow microphone permission in your browser settings.",
      });
    }
  };

  const handleTutorToggle = async () => {
    if (isTutorConnected || isTutorConnecting) {
      disconnectTutorSession();
      setTutorNotice(null);
      return;
    }

    setTutorNotice({
      type: "info",
      message: "Starting the live call. Once connected, you can enable the microphone from the button beside it.",
    });
    await connectTutorSession();
  };

  const handleChatSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const message = chatInput.trim();
    if (!message || !moduleSocketRef.current || moduleSocketRef.current.readyState !== WebSocket.OPEN) {
      return;
    }

    try {
      await consumeSubscriptionFeature({ feature: "chat_message", quantity: 1 });
      moduleSocketRef.current.send(JSON.stringify({ type: "chat_message", content: message }));
      setChatInput("");
    } catch (error) {
      if (isSubscriptionRequiredError(error)) {
        const params = new URLSearchParams();
        if (error.feature) params.set("feature", error.feature);
        if (error.reason) params.set("reason", error.reason);
        if (error.requiredPlan) params.set("plan", error.requiredPlan);
        navigate(`/upgrade?${params.toString()}`);
        return;
      }

      setTutorNotice({
        type: "error",
        message: error instanceof Error ? error.message : "You cannot send this message right now.",
      });
    }
  };

  const generatedCourse = generatedSession?.generatedCourse;
  const courseTitle = generatedSession?.course?.title ?? generatedCourse?.title ?? generatedCourse?.course_title ?? "Human Resource Management";
  const courseDescription = generatedSession?.course?.description ?? generatedCourse?.description;
  const generatedSummary = generatedCourse?.summary ?? generatedCourse?.content ?? generatedCourse?.message;
  const generatedFiles = Array.isArray(generatedCourse?.files)
    ? generatedCourse.files
    : Array.isArray(generatedCourse?.data?.files)
      ? generatedCourse.data.files
      : Array.isArray(generatedCourse?.resources)
        ? generatedCourse.resources
        : [];

  return (
    <main className="min-h-screen overflow-x-hidden bg-white text-slate-900 p-6">
      <header className="flex flex-col md:flex-row items-start md:items-center justify-between border-b border-blue-100 pb-4 mb-6" aria-label="Course navigation">
        <nav className="flex flex-wrap items-center gap-4 mb-4 md:mb-0">
          <a href="/dashboard" className="text-sm font-semibold text-blue-700 inline-flex items-center gap-2">
            <Home size={15} aria-hidden="true" />
            Dashboard
          </a>
          <div className="text-blue-700 font-semibold " >Kamara </div>
        </nav>

        <div className="flex items-center gap-3 w-full md:w-auto">
          <button className="w-10 h-10 rounded-full bg-blue-50 text-blue-700 flex items-center justify-center" type="button" aria-label="Open profile">
            <UserRound size={20} aria-hidden="true" />
          </button>
        </div>
      </header>

      <section className="grid min-h-[calc(100vh-160px)] gap-4 overflow-hidden xl:grid-cols-[minmax(0,1fr)_auto_auto]" aria-label="Learning workspace">
        <div className="min-w-0 overflow-hidden rounded-lg border border-blue-100 bg-white shadow-sm">
          <div className="border-b border-blue-100 p-5">
            <h1 className="text-2xl font-bold text-slate-900">{courseTitle}</h1>
            {courseDescription && <p className="mt-2 text-sm text-slate-600">{courseDescription}</p>}
            <div className="mt-3 inline-flex items-center gap-2 text-sm text-slate-600">
              <Video size={13} aria-hidden="true" className="text-blue-600" />
              {modules.length} modules
            </div>
          </div>

          <div className="h-[58vh] min-h-[420px] overflow-hidden">
            <LiveBoard key={activeSessionId ?? "live-board"} sessionId={activeSessionId ?? undefined} onEditorReady={setBoardEditor} />
          </div>

          <div className="border-t border-blue-100 p-4">
            <form onSubmit={handleChatSubmit} className="flex w-full items-center gap-2">
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Ask Kamara a question..."
                rows={1}
                className="flex-1 resize-none rounded-lg border border-slate-300 bg-slate-50 p-3 text-sm outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
              />
              <button type="submit" className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white shadow-sm transition hover:bg-blue-700" aria-label="Send message">
                <Send size={18} />
              </button>
              <button
                type="button"
                onClick={handleTutorToggle}
                className={`inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg shadow-sm transition-colors ${
                  isTutorConnected || isTutorConnecting ? "bg-blue-600 text-white hover:bg-blue-700" : "bg-slate-200 text-slate-700 hover:bg-slate-300"
                }`}
                aria-label={isTutorConnected || isTutorConnecting ? "Disconnect tutor call" : "Activate tutor call"}
                title={isTutorConnected || isTutorConnecting ? "Disconnect tutor call" : "Activate tutor call"}
              >
                {isTutorConnecting ? <LoaderCircle size={18} className="animate-spin" /> : isTutorConnected ? <PhoneOff size={18} /> : <Phone size={18} />}
              </button>
              <button
                type="button"
                onClick={handleMicToggle}
                className={`inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg shadow-sm transition-colors ${
                  isRecording || isMicConnecting ? "bg-blue-600 text-white hover:bg-blue-700" : "bg-slate-200 text-slate-700 hover:bg-slate-300"
                }`}
                aria-label={isRecording || isMicConnecting ? "Turn microphone off" : "Turn microphone on"}
                title={isRecording || isMicConnecting ? "Turn microphone off" : "Turn microphone on"}
              >
                {isMicConnecting ? <LoaderCircle size={18} className="animate-spin" /> : isRecording ? <MicOff size={18} /> : <Mic size={18} />}
              </button>
            </form>
            {tutorNotice && (
              <div
                className={`mt-3 rounded-lg border px-4 py-3 text-sm shadow-sm ${
                  tutorNotice.type === "error"
                    ? "border-rose-200 bg-rose-50 text-rose-800"
                    : tutorNotice.type === "warning"
                      ? "border-amber-200 bg-amber-50 text-amber-900"
                      : "border-blue-200 bg-blue-50 text-blue-900"
                }`}
                role={tutorNotice.type === "error" ? "alert" : "status"}
                aria-live="polite"
              >
                <div className="font-semibold">
                  {tutorNotice.type === "error" ? "Microphone needs attention" : tutorNotice.type === "warning" ? "Microphone not ready yet" : "Mic guidance"}
                </div>
                <p className="mt-1 leading-6">{tutorNotice.message}</p>
              </div>
            )}

          </div>
        </div>

        {isLearningPanelOpen && (
          <aside className="max-h-[calc(100vh-160px)] w-full overflow-y-auto rounded-lg border border-blue-100 bg-white shadow-sm xl:w-[350px]" aria-label="Learning modules">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-blue-100 bg-white px-5 py-4">
              <h2 className="text-base font-bold text-slate-900">Learning</h2>
              <button type="button" onClick={() => setIsLearningPanelOpen(false)} className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-slate-600 transition hover:bg-slate-100 hover:text-slate-900" aria-label="Close learning panel">
                <PanelRightClose size={18} />
              </button>
            </div>

            <ModuleLibrary modules={modules} selectedModuleId={selectedModuleId} onModuleSelect={(module) => setSelectedModuleId(module.id)} />
          </aside>
        )}

        <aside className="flex flex-row gap-2 rounded-lg border border-blue-100 bg-white p-2 shadow-sm xl:flex-col" aria-label="Workspace tools">
          <button
            type="button"
            onClick={() => setIsLearningPanelOpen((isOpen) => !isOpen)}
            className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-blue-700 transition hover:bg-blue-50"
            aria-label={isLearningPanelOpen ? "Close learning panel" : "Open learning panel"}
            title={isLearningPanelOpen ? "Close learning panel" : "Open learning panel"}
          >
            {isLearningPanelOpen ? <PanelRightClose size={19} /> : <PanelRightOpen size={19} />}
          </button>
          <button
            type="button"
            onClick={() => setIsLibraryOpen((isOpen) => !isOpen)}
            className={`inline-flex h-11 w-11 items-center justify-center rounded-lg transition ${isLibraryOpen ? "bg-blue-600 text-white" : "text-blue-700 hover:bg-blue-50"}`}
            aria-label="Open generated file library"
            title="Open generated file library"
          >
            <LibraryBig size={19} />
          </button>
        </aside>
      </section>

      {isLibraryOpen && (
        <section className="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-4" aria-label="Generated file library">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-base font-bold text-slate-900">Generated file library</h2>
            <button type="button" onClick={() => setIsLibraryOpen(false)} className="text-sm font-semibold text-blue-700 hover:text-blue-800">
              Close
            </button>
          </div>

          {generatedFiles.length > 0 ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {generatedFiles.map((file: any, index: number) => (
                <a key={String(file.id ?? file.url ?? index)} href={String(file.url ?? file.href ?? "#")} className="rounded-lg border border-blue-100 bg-white p-4 text-sm font-semibold text-slate-800 shadow-sm transition hover:border-blue-300 hover:text-blue-700">
                  {String(file.title ?? file.name ?? `Generated file ${index + 1}`)}
                </a>
              ))}
            </div>
          ) : (
            <p className="mt-3 text-sm text-slate-600">No generated files are available yet.</p>
          )}

          {(generatedSession?.prompt || generatedSummary) && (
            <div className="mt-4 rounded-lg border border-blue-100 bg-white p-4">
              {generatedSession?.prompt && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">Your prompt</h3>
                  <p className="mt-1 text-sm text-slate-700">{generatedSession.prompt}</p>
                </div>
              )}
              {generatedSummary && (
                <div className="mt-4">
                  <h3 className="text-sm font-semibold text-slate-900">Backend response</h3>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">{String(generatedSummary)}</p>
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
