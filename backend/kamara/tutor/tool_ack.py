import asyncio
from collections import defaultdict


_pending_tool_results: dict[str, dict[str, asyncio.Future[dict]]] = defaultdict(dict)


def register_tool_result(student_id: str, tool_call_id: str) -> asyncio.Future[dict]:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict] = loop.create_future()
    _pending_tool_results[student_id][tool_call_id] = future
    return future


def resolve_tool_result(student_id: str, tool_call_id: str, result: dict):
    future = _pending_tool_results.get(student_id, {}).pop(tool_call_id, None)

    if future and not future.done():
        future.set_result(result)


def clear_tool_result(student_id: str, tool_call_id: str):
    future = _pending_tool_results.get(student_id, {}).pop(tool_call_id, None)

    if future and not future.done():
        future.cancel()
