"""
src/trellis/api/websocket/latency_stream.py
Real-time WebSocket latency & automaticity sensor (<= 3000 ms ceiling & idle freeze).
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from trellis.api.deps import TrellisServiceContainer

router = APIRouter(tags=["Latency Telemetry WebSocket"])


@router.websocket("/api/v1/ws/latency/{student_id}")
async def latency_stream(websocket: WebSocket, student_id: str) -> None:
    """
    Streams real-time latency telemetry for automaticity (<= 3000 ms)
    and idle timer freeze detection (> 3x median latency).
    """
    await websocket.accept()
    container: TrellisServiceContainer = websocket.app.state.container

    try:
        while True:
            payload = await websocket.receive_json()
            topic_id = payload.get("topic_id")
            duration_ms = int(payload.get("duration_ms", 0))
            is_correct = bool(payload.get("is_correct", False))

            if not topic_id:
                await websocket.send_json({"error": "Missing 'topic_id' in telemetry frame."})
                continue

            is_auto, intervention = container.practice.evaluate_automaticity_latency(
                student_id=student_id,
                topic_id=topic_id,
                is_correct=is_correct,
                duration_ms=duration_ms,
            )
            median_ms = container.practice.config.automaticity_ceiling_ms
            try:
                profile = container.storage.get_student_profile(student_id)
                median_ms = profile.rolling_latency_ms.get(
                    topic_id,
                    profile.rolling_latency_ms.get("math_facts", median_ms),
                )
            except KeyError:
                pass

            idle_frozen = duration_ms > max(3000, 3 * median_ms)
            await websocket.send_json(
                {
                    "student_id": student_id,
                    "topic_id": topic_id,
                    "duration_ms": duration_ms,
                    "is_automatic": is_auto,
                    "fluency_intervention_triggered": intervention,
                    "idle_timer_frozen": idle_frozen,
                }
            )
    except WebSocketDisconnect:
        return
