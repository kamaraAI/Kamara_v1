import { useCallback, useEffect, useMemo, useRef } from "react";
import { atom } from "@tldraw/state";
import { createTLStore } from "@tldraw/editor";
import { type Editor, type IndexKey, type TLGeoShape, type TLShapeId, Tldraw, createShapeId, defaultAssetUtils, defaultBindingUtils, defaultShapeUtils, toRichText } from "tldraw";
import "tldraw/tldraw.css";

export type BoardCommand =
  | {
      action: "draw_shape";
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
      action: "write_text";
      data: {
        id: string;
        text: string;
        x: number;
        y: number;
      };
    }
  | {
      action: "move_shape";
      data: {
        shapeId: string;
        x: number;
        y: number;
      };
    }
  | {
      action: "resize_item";
      data: {
        shapeId: string;
        width: number;
        height: number;
      };
    }
  | {
      action: "delete_shape";
      data: {
        shapeId: string;
      };
    }
  | {
      action: "clear_board";
    }
  | {
      action: "draw_line";
      data: {
        id: string;
        x: number;
        y: number;
        line_type?: string;
      };
    };

export type LiveBoardProps = {
  sessionId?: string;
  onEditorReady?: (editor: Editor | null) => void;
};

const TUTOR_WRITE_MODE = atom<"readonly" | "readwrite">("board-tutor-write-mode", "readwrite");

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

function clearTldrawStorage() {
  if (typeof window === "undefined") {
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

function getShapeId(id: string): TLShapeId {
  return id.startsWith("shape:") ? (id as TLShapeId) : createShapeId(id);
}

function getGeoShape(shape: string): TLGeoShape["props"]["geo"] {
  switch (shape) {
    case "circle":
    case "ellipse":
      return "ellipse";
    case "triangle":
      return "triangle";
    case "diamond":
      return "diamond";
    case "rectangle":
    case "rect":
    case "square":
    default:
      return "rectangle";
  }
}

function lockCanvasToSheet(editor: Editor) {
  editor.setCamera({ x: 0, y: 0, z: 1 }, { immediate: true, force: true });
  editor.setCameraOptions({
    isLocked: true,
    panSpeed: 0,
    zoomSpeed: 0,
    wheelBehavior: "none",
  });
}

export function applyBoardCommand(editor: Editor, command: BoardCommand) {
  switch (command.action) {
    case "draw_shape": {
      const id = getShapeId(command.data.id);
      const defaultGeoProps = editor.getShapeUtil("geo").getDefaultProps();

      editor.createShape({
        id,
        type: "geo",
        x: command.data.x,
        y: command.data.y,
        props: {
          ...defaultGeoProps,
          geo: getGeoShape(command.data.shape),
          w: command.data.width,
          h: command.data.height,
          fill: "none",
          color: "blue",
          richText: toRichText(""),
        },
      });
      break;
    }

    case "write_text": {
      const id = getShapeId(command.data.id.replace(/^text:/, "text-"));
      const defaultTextProps = editor.getShapeUtil("text").getDefaultProps();

      editor.createShape({
        id,
        type: "text",
        x: command.data.x,
        y: command.data.y,
        props: {
          ...defaultTextProps,
          richText: toRichText(command.data.text),
          autoSize: true,
          color: "black",
        },
      });
      break;
    }

    case "move_shape": {
      const id = getShapeId(command.data.shapeId);
      const shape = editor.getShape(id);
      if (shape) {
        editor.updateShape({ id, type: shape.type, x: command.data.x, y: command.data.y });
      }
      break;
    }

    case "resize_item": {
      const id = getShapeId(command.data.shapeId);
      const shape = editor.getShape(id);
      if (shape?.type === "geo") {
        editor.updateShape({
          id,
          type: "geo",
          props: {
            w: command.data.width,
            h: command.data.height,
          },
        });
      }
      break;
    }

    case "delete_shape": {
      const id = getShapeId(command.data.shapeId);
      if (editor.getShape(id)) {
        editor.deleteShapes([id]);
      }
      break;
    }

    case "clear_board": {
      editor.deleteShapes([...editor.getCurrentPageShapeIds()]);
      break;
    }

    case "draw_line": {
      const id = getShapeId(command.data.id);
      const lineType = command.data.line_type ?? (command.data as { type?: string }).type;
      const isCurve = lineType === "curve";
      const defaultLineProps = editor.getShapeUtil("line").getDefaultProps();

      editor.createShape({
        id,
        type: "line",
        x: command.data.x,
        y: command.data.y,
        props: {
          ...defaultLineProps,
          color: "blue",
          dash: "solid",
          size: "m",
          spline: isCurve ? "cubic" : "line",
          points: {
            start: { id: "start", index: "a1" as IndexKey, x: 0, y: 0 },
            end: { id: "end", index: "a2" as IndexKey, x: isCurve ? 180 : 140, y: isCurve ? 80 : 0 },
          },
          scale: 1,
        },
      });
      break;
    }
  }
}

export default function LiveBoard({ onEditorReady }: LiveBoardProps) {
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

  const handleMount = useCallback(
    (editor: Editor) => {
      editorRef.current = editor;
      lockCanvasToSheet(editor);
      onEditorReady?.(editor);
    },
    [onEditorReady]
  );

  useEffect(() => {
    clearTldrawStorage();
    return () => {
      onEditorReady?.(null);
      editorRef.current = null;
    };
  }, [onEditorReady]);

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: 0,
        display: "flex",
        alignItems: "stretch",
        justifyContent: "center",
        overflow: "hidden",
        padding: 12,
        boxSizing: "border-box" as const,
      }}
    >
      <div style={{ ...FIXED_SHEET_STYLE, position: "relative" }}>
        <Tldraw hideUi autoFocus={false} store={tutorWritableStore} onMount={handleMount} />
        <div
          aria-label="Tutor-controlled whiteboard"
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 10,
            cursor: "default",
          }}
        />
      </div>
    </div>
  );
}
