"""
tests/test_tier9_api.py
End-to-end FastAPI TestClient integration tests for Tier 9 Transport & Web API Layer.
Parameterized across standalone 'memory' and 'relational' storage backends.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trellis.api.main import create_app
from trellis.domain.models import StudentProfile, StudentTopicState
from trellis.ports.storage_port import get_storage_adapter


def _build_test_graph_payload() -> dict:
    def _make_kps(node_id: str, title: str, num_kps: int = 2) -> list[dict]:
        return [
            {
                "kp_index": idx,
                "kp_id": f"{node_id}#kp_{idx}",
                "scaffolding_tier": f"KP_{idx + 1}_TIER",
                "kp_focus": f"Focus {idx} for {title}",
                "novel_chunk_count": 2,
                "key_prerequisite_id": None,
                "rule_statement": f"Rule {idx} for {title}.",
                "canonical_worked_example": {
                    "problem": f"Evaluate 3 + {idx}",
                    "subgoal_steps": [
                        {
                            "subgoal_label": "Add terms",
                            "operation": f"Compute 3 + {idx}",
                            "justification": "Addition definition",
                        }
                    ],
                    "final_answer": f"{3 + idx}",
                },
                "problem_blueprint": {
                    "parameter_constraints": "p_1 in {2..9}",
                    "stem_template": f"Compute {{p_1}} + {idx}",
                    "answer_format": "NUMERIC_EXACT",
                    "verification_solver_expr": "sympy.simplify(target_expr - student_expr) == 0",
                },
                "diagnostic_misconceptions": [
                    {
                        "error_tag": "ERR_SIGN_INVERSION",
                        "trap_answer_pattern": f"-{{p_1}} + {idx}",
                        "violated_rule": "Sign inversion rule violated.",
                        "tier1_remediation_template": "Do not negate the leading term.",
                    }
                ],
            }
            for idx in range(num_kps)
        ]

    return {
        "schema_version": "2.0.0-trellis",
        "course_catalog": [
            {
                "course_id": "CRS_ALG1",
                "course_code": "alg1",
                "title": "Algebra 1",
                "grade_band": "8-9",
                "benchmark_xp": 1500,
                "mastery_floor_course_ids": [],
            }
        ],
        "nodes": [
            {
                "id": "trl_alg_root",
                "title": "Integer Addition",
                "course_id": "CRS_ALG1",
                "canonical_domain": "NUMBER_OPERATIONS",
                "domain_cluster": "CRS_ALG1::num",
                "skill_type": "FLUENCY_FACT",
                "bloom_cognitive_tier": "RETRIEVAL_AUTOMATICITY",
                "grade_level_min": 6,
                "grade_level_max": 8,
                "is_core": True,
                "in_foundations_sequence": True,
                "requires_automaticity": True,
                "automaticity_Defining_latency_ms": 3000,
                "base_difficulty": 0.3,
                "estimated_xp": 3,
                "mastery_gate_n": 3,
                "knowledge_points": _make_kps("trl_alg_root", "Integer Addition"),
                "prerequisites": [],
                "encompassed_skills": [],
            },
            {
                "id": "trl_alg_linear",
                "title": "One-Step Linear Equations",
                "course_id": "CRS_ALG1",
                "canonical_domain": "ALGEBRA",
                "domain_cluster": "CRS_ALG1::lin",
                "skill_type": "PROCEDURAL",
                "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
                "grade_level_min": 8,
                "grade_level_max": 9,
                "is_core": True,
                "in_foundations_sequence": True,
                "requires_automaticity": False,
                "base_difficulty": 0.45,
                "estimated_xp": 4,
                "mastery_gate_n": 3,
                "knowledge_points": _make_kps("trl_alg_linear", "One-Step Linear Equations"),
                "prerequisites": [
                    {
                        "prerequisite_id": "trl_alg_root",
                        "strength": "hard",
                        "rationale": "Requires integer addition",
                    }
                ],
                "encompassed_skills": [
                    {
                        "encompassed_id": "trl_alg_root",
                        "fire_weight": 0.6,
                        "is_non_ancestor": False,
                        "rationale": "Linear solving exercises integer addition",
                    }
                ],
            },
        ],
    }


@pytest.fixture(params=["memory", "relational"])
def api_client(request):
    storage = get_storage_adapter(request.param)
    storage.ingest_knowledge_graph(_build_test_graph_payload())
    app = create_app(storage=storage, auto_ingest=False)
    with TestClient(app) as client:
        yield client


def test_health_and_swagger_docs(api_client: TestClient) -> None:
    resp = api_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["courses_loaded"] == 1

    docs_resp = api_client.get("/docs")
    assert docs_resp.status_code == 200
    openapi_resp = api_client.get("/openapi.json")
    assert openapi_resp.status_code == 200


def test_auth_jwt_issuance_and_verification(api_client: TestClient) -> None:
    token_resp = api_client.post(
        "/api/v1/auth/token",
        json={"subject": "stu_1", "role": "student"},
    )
    assert token_resp.status_code == 200
    token = token_resp.json()["access_token"]

    me_resp = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json() == {"sub": "stu_1", "role": "student"}

    unauth_resp = api_client.get("/api/v1/auth/me")
    assert unauth_resp.status_code == 401


def test_session_dashboard_and_refresh(api_client: TestClient) -> None:
    reg = api_client.post(
        "/api/v1/students",
        json={"student_id": "stu_dash", "enrolled_course_id": "CRS_ALG1"},
    )
    assert reg.status_code == 200

    dash = api_client.get("/api/v1/session/dashboard", params={"student_id": "stu_dash"})
    assert dash.status_code == 200
    items = dash.json()
    assert len(items) >= 1
    assert items[0]["topic_id"] == "trl_alg_root"

    refreshed = api_client.post("/api/v1/session/refresh", json={"student_id": "stu_dash"})
    assert refreshed.status_code == 200
    assert refreshed.json()["student_id"] == "stu_dash"


def test_practice_lesson_progression_and_remediation(api_client: TestClient) -> None:
    api_client.post(
        "/api/v1/students",
        json={"student_id": "stu_prac", "enrolled_course_id": "CRS_ALG1"},
    )

    start = api_client.post(
        "/api/v1/practice/lesson/start",
        json={"student_id": "stu_prac", "topic_id": "trl_alg_root", "seed": 7},
    )
    assert start.status_code == 200
    step = start.json()
    assert step["kp_id"] == "trl_alg_root#kp_0"
    assert step["show_instruction_card"] is True
    target_ans = step["problem_instance"]["target_answer"]
    params = step["problem_instance"]["parameters"]

    # Submit wrong answer -> Tier 1 remediation
    wrong = api_client.post(
        "/api/v1/practice/lesson/submit",
        json={
            "student_id": "stu_prac",
            "topic_id": "trl_alg_root",
            "kp_id": "trl_alg_root#kp_0",
            "kp_index": 0,
            "student_answer": "9999",
            "duration_ms": 1200,
            "target_answer": target_ans,
            "parameters": params,
        },
    )
    assert wrong.status_code == 200
    wrong_data = wrong.json()
    assert wrong_data["evaluation"]["correct"] is False
    assert wrong_data["evaluation"]["tier_remediation"] == 1
    assert wrong_data["remediation_card"] is not None

    # Submit correct answer -> advances consecutive_correct
    right = api_client.post(
        "/api/v1/practice/lesson/submit",
        json={
            "student_id": "stu_prac",
            "topic_id": "trl_alg_root",
            "kp_id": "trl_alg_root#kp_0",
            "kp_index": 0,
            "student_answer": target_ans,
            "duration_ms": 1500,
            "target_answer": target_ans,
            "parameters": params,
        },
    )
    assert right.status_code == 200
    right_data = right.json()
    assert right_data["evaluation"]["correct"] is True
    assert right_data["evaluation"]["consecutive_correct"] == 1


def test_review_session_spotter_and_completion(api_client: TestClient) -> None:
    api_client.post(
        "/api/v1/students",
        json={"student_id": "stu_rev", "enrolled_course_id": "CRS_ALG1"},
    )
    container = api_client.app.state.container
    container.storage.upsert_student_topic_state(
        StudentTopicState(
            student_id="stu_rev",
            topic_id="trl_alg_root",
            status="DUE_FOR_REVIEW",
            rep_num=1.0,
            stability_interval_days=2.0,
            memory_strength=0.4,
        )
    )

    rev_start = api_client.post(
        "/api/v1/practice/review/start",
        json={"student_id": "stu_rev", "topic_id": "trl_alg_root", "seed": 11},
    )
    assert rev_start.status_code == 200
    session = rev_start.json()
    session_id = session["session_id"]

    # Spotter fails when input_locked is False
    unlocked_peek = api_client.post(
        "/api/v1/practice/spotter/peek",
        json={"session_id": session_id, "question_index": 0, "input_locked": False},
    )
    assert unlocked_peek.status_code == 400

    # Spotter succeeds when input_locked is True
    locked_peek = api_client.post(
        "/api/v1/practice/spotter/peek",
        json={"session_id": session_id, "question_index": 0, "input_locked": True},
    )
    assert locked_peek.status_code == 200
    assert locked_peek.json()["kp_id"] == session["questions"][0]["kp_id"]

    # Answer 3 questions correctly to pass clean 3-for-3
    last_eval = None
    for idx in range(3):
        ans = session["questions"][idx]["problem_instance"]["target_answer"]
        sub = api_client.post(
            "/api/v1/practice/review/submit",
            json={
                "session_id": session_id,
                "question_index": idx,
                "student_answer": ans,
                "duration_ms": 1400,
            },
        )
        assert sub.status_code == 200
        last_eval = sub.json()["evaluation"]

    assert last_eval is not None
    assert last_eval["passed"] is True
    assert last_eval["reference_peeked_count"] == 1


def test_diagnostic_start_and_submit(api_client: TestClient) -> None:
    start = api_client.post(
        "/api/v1/diagnostic/start",
        json={
            "student_id": "stu_diag",
            "enrolled_course_id": "CRS_ALG1",
            "min_questions": 1,
            "max_questions": 2,
            "seed": 5,
        },
    )
    assert start.status_code == 200
    data = start.json()
    assert data["compressed_cover_size"] >= 1
    q = data["next_question"]
    assert q is not None

    # Deprecated 'I_DONT_KNOW' flag is rejected with 400
    deprecated_sub = api_client.post(
        "/api/v1/diagnostic/submit",
        json={
            "session_id": data["session_id"],
            "submission": {
                "student_id": "stu_diag",
                "topic_id": q["topic_id"],
                "kp_id": q["kp_id"],
                "submission_flag": "I_DONT_KNOW",
                "duration_ms": 2000,
            },
        },
    )
    assert deprecated_sub.status_code == 400

    # Standard answer submission succeeds and propagates signed weight
    target_ans = q["blueprint_instance"]["target_answer"]
    params = q["blueprint_instance"]["parameters"]
    sub = api_client.post(
        "/api/v1/diagnostic/submit",
        json={
            "session_id": data["session_id"],
            "submission": {
                "student_id": "stu_diag",
                "topic_id": q["topic_id"],
                "kp_id": q["kp_id"],
                "student_answer": target_ans,
                "target_answer": target_ans,
                "parameters": params,
                "duration_ms": 2000,
            },
        },
    )
    assert sub.status_code == 200
    sub_data = sub.json()
    assert sub_data["propagation"]["assigned_weight"] == 1.0


def test_parent_portal_summary_and_rewards(api_client: TestClient) -> None:
    api_client.post(
        "/api/v1/students",
        json={"student_id": "stu_parent", "enrolled_course_id": "CRS_ALG1"},
    )

    rew = api_client.post(
        "/api/v1/parent/rewards",
        json={"student_id": "stu_parent", "title": "Ice Cream Night", "xp_target": 100},
    )
    assert rew.status_code == 200
    assert rew.json()["title"] == "Ice Cream Night"
    assert rew.json()["status"] == "PENDING"

    summary = api_client.get("/api/v1/parent/summary", params={"student_id": "stu_parent"})
    assert summary.status_code == 200
    s_data = summary.json()
    assert s_data["student_id"] == "stu_parent"
    assert len(s_data["rewards"]) == 1


def test_latency_websocket_stream(api_client: TestClient) -> None:
    api_client.post(
        "/api/v1/students",
        json={"student_id": "stu_ws", "enrolled_course_id": "CRS_ALG1"},
    )
    with api_client.websocket_connect("/api/v1/ws/latency/stu_ws") as ws:
        ws.send_json({"topic_id": "trl_alg_root", "is_correct": True, "duration_ms": 1800})
        resp = ws.receive_json()
        assert resp["is_automatic"] is True
        assert resp["idle_timer_frozen"] is False


def test_courses_student_read_author_queue_and_ui_serving(api_client: TestClient) -> None:
    courses = api_client.get("/api/v1/courses")
    assert courses.status_code == 200
    assert courses.json()[0]["course_id"] == "CRS_ALG1"

    api_client.post(
        "/api/v1/students",
        json={"student_id": "stu_ui", "enrolled_course_id": "CRS_ALG1"},
    )
    profile = api_client.get("/api/v1/students/stu_ui")
    assert profile.status_code == 200
    assert profile.json()["student_id"] == "stu_ui"

    flag_resp = api_client.post(
        "/api/v1/author/flags",
        json={
            "student_id": "stu_ui",
            "topic_id": "trl_alg_root",
            "flag_type": "AMBIGUOUS_WORDING",
            "student_comment": "Needs clearer units",
        },
    )
    assert flag_resp.status_code == 200
    flag_id = flag_resp.json()["flag_id"]

    queue = api_client.get("/api/v1/author/remediation-queue")
    assert queue.status_code == 200
    assert queue.json()["open_flags_count"] >= 1

    resolve = api_client.post(f"/api/v1/author/flags/{flag_id}/resolve")
    assert resolve.status_code == 200
    assert resolve.json()["status"] == "RESOLVED"

    ui_html = api_client.get("/")
    assert ui_html.status_code == 200
    assert "Trellis K–16" in ui_html.text

    ui_js = api_client.get("/static/app.js")
    assert ui_js.status_code == 200
    assert "loadBuffet" in ui_js.text
