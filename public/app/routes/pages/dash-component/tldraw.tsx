import { useCallback, useEffect, useMemo, useRef } from 'react';
import { atom } from '@tldraw/state';
import { createTLStore } from '@tldraw/editor';
import { getWebSocketBaseUrl } from '../../api-config';
import {
  type Editor,
  type IndexKey,
  type TLGeoShape,
  type TLShapeId,
  defaultAssetUtils,
  defaultBindingUtils,
  defaultShapeUtils,
  Tldraw,
  createShapeId,
  toRichText,
} from 'tldraw';
import 'tldraw/tldraw.css';
import { getGeneratedCourseStorageKey } from '../genie-api';

const WS_BASE_URL = getWebSocketBaseUrl();
const TUTOR_WRITE_MODE = atom<'readonly' | 'readwrite'>('board-tutor-write-mode', 'readwrite');
// will add cloud run url 
type BoardCommand =
  | {
      action: 'draw_shape';
      data: {
        id: string;
        shape: string;
        x: number;
        y: number;
        width: number;
        height: number;
      };
    }
  | {
      action: 'write_text';
      data: {
        id: string;
        text: string;
        x: number;
        y: number;
      };
    }
  | {
      action: 'move_shape';
      data: {
        shapeId: string;
        x: number;
        y: number;
      };
    }
  | {
      action: 'resize_item';
      data: {
        shapeId: string;
        width: number;
        height: number;
      };
    }
  | {
      action: 'delete_shape';
      data: {
        shapeId: string;
      };
    }
  | {
      action: 'clear_board';
    }
  | {
      action: 'draw_line';
      data: {
        id: string;
        x: number;
        y: number;
        line_type?: string;
      };
    };

type TldrawComponentProps = {
  sessionId?: string;
  onEditorReady?: (editor: Editor | null) => void;
};

const FIXED_SHEET_STYLE = {
  width: "100%",
  height: "100%",
  maxWidth: "100%",
  maxHeight: "100%",
  background: "#fff",
  border: "1px solid #ccc",
  boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
  overflow: "hidden",
  boxSizing: "border-box" as const,
};

function getSessionId(fallback?: string) {
  if (fallback) return fallback;
  if (typeof window === 'undefined') return null;

  const params = new URLSearchParams(window.location.search);
  if (params.get('session_id')) {
    return params.get('session_id');
  }

  const activeSessionId = localStorage.getItem('active_session_id');
  if (activeSessionId) {
    return activeSessionId;
  }

  const storedCourse = sessionStorage.getItem(getGeneratedCourseStorageKey());
  if (!storedCourse) {
    return null;
  }

  try {
    const parsedCourse = JSON.parse(storedCourse) as { session_id?: string; generatedCourse?: { session_id?: string; data?: { session_id?: string } } };
    return parsedCourse.generatedCourse?.session_id ?? parsedCourse.generatedCourse?.data?.session_id ?? parsedCourse.session_id ?? null;
  } catch {
    return null;
  }
}

function getShapeId(id: string): TLShapeId {
  return id.startsWith('shape:') ? (id as TLShapeId) : createShapeId(id);
}

function clearTldrawStorage() {
  if (typeof window === 'undefined') {
    return;
  }

  const storageBuckets = [window.localStorage, window.sessionStorage];
  for (const storage of storageBuckets) {
    const keys: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key && /tldraw/i.test(key)) {
        keys.push(key);
      }
    }

    keys.forEach((key) => storage.removeItem(key));
  }
}

function getGeoShape(shape: string): TLGeoShape['props']['geo'] {
  switch (shape) {
    case 'circle':
    case 'ellipse':
      return 'ellipse';
    case 'triangle':
      return 'triangle';
    case 'diamond':
      return 'diamond';
    case 'rectangle':
    case 'rect':
    case 'square':
    default:
      return 'rectangle';
  }
}

function lockCanvasToSheet(editor: Editor) {
  editor.setCamera({ x: 0, y: 0, z: 1 }, { immediate: true, force: true });
  editor.setCameraOptions({
    isLocked: true,
    panSpeed: 0,
    zoomSpeed: 0,
    wheelBehavior: 'none',
  });
}

function extractBoardCommand(payload: unknown) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const candidate = payload as {
    type?: string;
    action?: string;
    payload?: unknown;
    data?: unknown;
  };

  if (candidate.type === 'tool_call') {
    return candidate.payload ?? candidate.data ?? null;
  }

  if (candidate.action) {
    return payload;
  }

  if (candidate.payload && typeof candidate.payload === 'object') {
    return candidate.payload;
  }

  if (candidate.data && typeof candidate.data === 'object') {
    return candidate.data;
  }

  return null;
}

function applyBoardCommand(editor: Editor, command: BoardCommand) {
  switch (command.action) {
    case 'draw_shape': {
      const id = getShapeId(command.data.id);
      const defaultGeoProps = editor.getShapeUtil('geo').getDefaultProps();

      editor.createShape({
        id,
        type: 'geo',
        x: command.data.x,
        y: command.data.y,
        props: {
          ...defaultGeoProps,
          geo: getGeoShape(command.data.shape),
          w: command.data.width,
          h: command.data.height,
          fill: 'none',
          color: 'blue',
          richText: toRichText(''),
        },
      });
      break;
    }

    case 'write_text': {
      const id = getShapeId(command.data.id.replace(/^text:/, 'text-'));
      const defaultTextProps = editor.getShapeUtil('text').getDefaultProps();

      editor.createShape({
        id,
        type: 'text',
        x: command.data.x,
        y: command.data.y,
        props: {
          ...defaultTextProps,
          richText: toRichText(command.data.text),
          autoSize: true,
          color: 'black',
        },
      });
      break;
    }

    case 'move_shape': {
      const id = getShapeId(command.data.shapeId);
      const shape = editor.getShape(id);
      if (shape) {
        editor.updateShape({ id, type: shape.type, x: command.data.x, y: command.data.y });
      }
      break;
    }

    case 'resize_item': {
      const id = getShapeId(command.data.shapeId);
      const shape = editor.getShape(id);
      if (shape?.type === 'geo') {
        editor.updateShape({
          id,
          type: 'geo',
          props: {
            w: command.data.width,
            h: command.data.height,
          },
        });
      }
      break;
    }

    case 'delete_shape': {
      const id = getShapeId(command.data.shapeId);
      if (editor.getShape(id)) {
        editor.deleteShapes([id]);
      }
      break;
    }

    case 'clear_board': {
      editor.deleteShapes([...editor.getCurrentPageShapeIds()]);
      break;
    }

    case 'draw_line': {
      const id = getShapeId(command.data.id);
      const lineType = command.data.line_type ?? (command.data as { type?: string }).type;
      const isCurve = lineType === 'curve';
      const defaultLineProps = editor.getShapeUtil('line').getDefaultProps();

      editor.createShape({
        id,
        type: 'line',
        x: command.data.x,
        y: command.data.y,
        props: {
          ...defaultLineProps,
          color: 'blue',
          dash: 'solid',
          size: 'm',
          spline: isCurve ? 'cubic' : 'line',
          points: {
            start: { id: 'start', index: 'a1' as IndexKey, x: 0, y: 0 },
            end: { id: 'end', index: 'a2' as IndexKey, x: isCurve ? 180 : 140, y: isCurve ? 80 : 0 },
          },
          scale: 1,
        },
      });
      break;
    }
  }
}

const TldrawComponent = ({ sessionId, onEditorReady }: TldrawComponentProps) => {
  const socketRef = useRef<WebSocket | null>(null);
  const editorRef = useRef<Editor | null>(null);
  const tutorWritableStore = useMemo(
    () =>
      createTLStore({
        shapeUtils: defaultShapeUtils,
        bindingUtils: defaultBindingUtils,
        assetUtils: defaultAssetUtils,
        collaboration: {
          status: null,
          mode: TUTOR_WRITE_MODE,
        },
      }),
    []
  );

  const disconnectBoardSocket = useCallback(() => {
    onEditorReady?.(null);
    editorRef.current = null;

    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ action: 'end_session' }));
    }

    socketRef.current?.close();
    socketRef.current = null;
  }, []);

  const connectBoardSocket = useCallback(() => {
    const editor = editorRef.current;
    const token = localStorage.getItem('access_token');
    const activeSessionId = getSessionId(sessionId);
    const currentSocket = socketRef.current;

    if (!editor || !token || !activeSessionId) {
      return;
    }

    if (currentSocket?.readyState === WebSocket.OPEN || currentSocket?.readyState === WebSocket.CONNECTING) {
      return;
    }

    if (currentSocket) {
      currentSocket.close();
      socketRef.current = null;
    }

    const socket = new WebSocket(
      `${WS_BASE_URL}/live?token=${encodeURIComponent(token)}&session_id=${encodeURIComponent(activeSessionId)}`
    );
    socketRef.current = socket;

    socket.addEventListener('open', () => {
      socket.send(
        JSON.stringify({
          action: 'start_session',
          session_id: activeSessionId,
        })
      );
    });

    socket.addEventListener('close', () => {
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    });

    socket.addEventListener('error', () => {
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    });

    socket.addEventListener('message', (event) => {
      if (typeof event.data !== 'string') {
        return;
      }

      try {
        const payload = JSON.parse(event.data) as Partial<BoardCommand> & {
          type?: string;
          action?: string;
          payload?: unknown;
          data?: unknown;
        };

        const command = extractBoardCommand(payload);

        if (!command || typeof command !== 'object' || typeof (command as { action?: string }).action !== 'string') {
          return;
        }

        const boardCommand = command as BoardCommand;

        if (
          boardCommand.action !== 'draw_shape' &&
          boardCommand.action !== 'write_text' &&
          boardCommand.action !== 'move_shape' &&
          boardCommand.action !== 'resize_item' &&
          boardCommand.action !== 'delete_shape' &&
          boardCommand.action !== 'clear_board' &&
          boardCommand.action !== 'draw_line'
        ) {
          return;
        }

        applyBoardCommand(editor, boardCommand);
      } catch (error) {
        console.error('Could not apply tutor board command', error);
      }
    });
  }, [sessionId]);

  const handleMount = useCallback(
    (editor: Editor) => {
      editorRef.current = editor;
      lockCanvasToSheet(editor);
      onEditorReady?.(editor);
      connectBoardSocket();
    },
    [connectBoardSocket, onEditorReady]
  );

  useEffect(() => {
    clearTldrawStorage();

    connectBoardSocket();

    return () => {
      disconnectBoardSocket();
    };
  }, [connectBoardSocket, disconnectBoardSocket, sessionId]);

  return (
    <div style={{ width: '100%', height: '100%', minHeight: 0, display: 'flex', alignItems: 'stretch', justifyContent: 'center', overflow: 'hidden', padding: 12, boxSizing: 'border-box' as const }}>
      <div style={{ ...FIXED_SHEET_STYLE, position: 'relative' }}>
        <Tldraw
          hideUi
          autoFocus={false}
          store={tutorWritableStore}
          onMount={handleMount}
        />
        <div
          aria-label="Tutor-controlled whiteboard"
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 10,
            cursor: 'default',
          }}
        />
      </div>
    </div>
  );
};

export default TldrawComponent;
