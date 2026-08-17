import asyncio
import logging

from google.genai import types

from connection.connect_manager import manager
from connection.channels import CANVAS_OUTPUT_CHANNEL

from .delete_and_clear import clear_board, delete_board_item
from .draw import draw_on_board
from .draw_line_and_curve import draw_line
from .move_and_adjust import adjust_item_size, move_item_on_screen
from .write import write_on_board
from .tools_description import (
    adjust_item_size_desc,
    clear_tool_desc,
    delete_tool_desc,
    draw_line_tool_desc,
    draw_tool_desc,
    move_item_desc,
    write_tool_desc,
)
logger = logging.getLogger("KamaraLogger")



tools = {
    "function_declarations": [

        # draw tool
        {
            "name": "async_draw",
            "description": draw_tool_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "shape_id": {"type": "STRING"},
                    "shape": {"type": "STRING"},
                    "x": {"type": "INTEGER"},
                    "y": {"type": "INTEGER"},
                    "width": {"type": "INTEGER"},
                    "height": {"type": "INTEGER"},
                },
                "required": ["shape_id", "shape", "x", "y", "width", "height"],
            },
            "behavior": "NON_BLOCKING",
        },

        # clear board tool
        {
            "name": "clear_whiteboard",
            "description": clear_tool_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {},
            },
            "behavior": "NON_BLOCKING",
        },

        # write on board tool
        {
            "name": "write_board",
            "description": write_tool_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "text_id": {"type": "STRING"},
                    "text": {"type": "STRING"},
                    "x": {"type": "INTEGER"},
                    "y": {"type": "INTEGER"},
                    "text_size": {"type": "INTEGER"},
                },
                "required": ["text_id", "text", "x", "y"],
            },
            "behavior": "NON_BLOCKING",
        },

        # delete item tool
        {
            "name": "delete_item",
            "description": delete_tool_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "item_id": {"type": "STRING"},
                },
                "required": ["item_id"],
            },
            "behavior": "NON_BLOCKING",
        },

        # move item tool
        {
            "name": "move_item",
            "description": move_item_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "item_id": {"type": "STRING"},
                    "x": {"type": "INTEGER"},
                    "y": {"type": "INTEGER"},
                },
                "required": ["item_id", "x", "y"],
            },
            "behavior": "NON_BLOCKING",
        },

        # adjust item size tool
        {
            "name": "adjust_item_size",
            "description": adjust_item_size_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "item_id": {"type": "STRING"},
                    "width": {"type": "INTEGER"},
                    "height": {"type": "INTEGER"},
                },
                "required": ["item_id", "width", "height"],
            },
            "behavior": "NON_BLOCKING",
        },

        #draw line tool
        {
            "name": "draw_line",
            "description": draw_line_tool_desc,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "line_id": {"type": "STRING"},
                    "x": {"type": "INTEGER"},
                    "y": {"type": "INTEGER"},
                    "line_type": {"type": "STRING"},
                },
                "required": ["line_id", "x", "y"],
            },
            "behavior": "NON_BLOCKING",
        },
    ]
}




async def tools_handler(student_id: str, session, tool_call, runtime=None):
    """
    Process tool calls from Gemini and broadcast board commands on the canvas channel.
    """
    if not tool_call or not tool_call.function_calls:
        return

    async def _handle_one_function_call(fc) -> types.FunctionResponse | None:
        payload = None
        gemini_receipt = {"success": "true"}

        try:
            if fc.name == "async_draw":
                payload = await draw_on_board(**fc.args)
            elif fc.name == "write_board":
                payload = await write_on_board(**fc.args)
            elif fc.name == "clear_whiteboard":
                payload = await clear_board()
            elif fc.name == "delete_item":
                payload = await delete_board_item(**fc.args)
            elif fc.name == "move_item":
                payload = await move_item_on_screen(**fc.args)
            elif fc.name == "adjust_item_size":
                payload = await adjust_item_size(**fc.args)
            elif fc.name == "draw_line":
                payload = await draw_line(**fc.args)
            else:
                return None

            if payload:
                browser_command = {
                    "type": "tool_call",
                    "name": fc.name,
                    "tool_call_id": getattr(fc, "id", None),
                    "action": payload.get("action"),
                    "data": payload.get("data", {}),
                    "payload": payload,
                }

                if runtime is not None:
                    await runtime.enqueue_canvas_output(browser_command)
                else:
                    await manager.send_json_message(browser_command, student_id, channel=CANVAS_OUTPUT_CHANNEL)
                logger.info("Broadcast tutor tool '%s' to student %s", fc.name, student_id)

                gemini_receipt = {
                    "success": "true",
                    "action_executed": str(fc.name),
                    "status_message": "Tool Call delivered to the frontend.",
                }

        except Exception as exc:
            logger.error("Whiteboard execution failed for tool %s: %s", fc.name, str(exc), exc_info=True)
            gemini_receipt = {
                "success": "false",
                "error_message": str(exc)[:120],
            }

        return types.FunctionResponse(
            name=fc.name,
            id=fc.id,
            response=gemini_receipt,
        )

    function_responses = [
        response
        for response in await asyncio.gather(
            *(_handle_one_function_call(fc) for fc in tool_call.function_calls),
            return_exceptions=False,
        )
        if response is not None
    ]

    try:
        await session.send_tool_response(function_responses=function_responses)
        logger.info("Sent %s tool response receipt(s) back to Gemini.", len(function_responses))
    except Exception as stream_err:
        logger.error("Failed to stream tool confirmation back to Gemini: %s", str(stream_err))
