// Trellis Tier 10 Client UI Controller (Zero-Build ES Module)

const state = {
  studentId: "stu_demo",
  courseId: "CRS_ALG1",
  activeLesson: null,
  activeReview: null,
  activeDiagnostic: null,
  stepStartedAt: 0,
  latencyWs: null,
};

const $ = (id) => document.getElementById(id);

function showBanner(msg, tone = "info") {
  const el = $("global-banner");
  const styles = {
    info: "bg-sky-950/60 border-sky-700 text-sky-200",
    error: "bg-rose-950/60 border-rose-700 text-rose-200",
    success: "bg-emerald-950/60 border-emerald-700 text-emerald-200",
  };
  el.className = `mb-4 p-3 rounded-lg border text-sm ${styles[tone] || styles.info}`;
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 5000);
}

function renderMath(el) {
  if (window.renderMathInElement && el) {
    window.renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
      ],
      throwOnError: false,
    });
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return body;
}

// --- Navigation Tabs ---
function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  ["buffet", "practice", "diagnostic", "parent", "author"].forEach((name) => {
    $(`view-${name}`).classList.toggle("hidden", name !== tabName);
  });
  if (tabName === "parent") loadParentSummary();
  if (tabName === "author") loadAuthorQueue();
}

// --- Student Registration & Telemetry ---
async function loadCourses() {
  const courses = await api("/api/v1/courses");
  const select = $("course-select");
  select.innerHTML = "";
  courses.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.course_id;
    opt.textContent = `${c.title} (${c.grade_band})`;
    select.appendChild(opt);
  });
  if (courses.some((c) => c.course_id === "CRS_ALG1")) {
    select.value = "CRS_ALG1";
  } else if (courses.length > 0) {
    select.value = courses[0].course_id;
  }
  state.courseId = select.value;
}

async function loadOrRegisterStudent() {
  state.studentId = $("student-id-input").value.trim() || "stu_demo";
  state.courseId = $("course-select").value || "CRS_ALG1";
  let profile;
  try {
    profile = await api(`/api/v1/students/${encodeURIComponent(state.studentId)}`);
  } catch {
    profile = await api("/api/v1/students", {
      method: "POST",
      body: JSON.stringify({
        student_id: state.studentId,
        enrolled_course_id: state.courseId,
      }),
    });
    showBanner(`Registered new student '${state.studentId}' in ${state.courseId}.`, "success");
  }
  updateHeaderMetrics(profile);
  connectLatencyWs();
  await loadBuffet();
}

function updateHeaderMetrics(profile) {
  $("metric-progress").textContent = `${(profile.progress_pct || 0).toFixed(1)}%`;
  $("metric-xp").textContent = `${profile.xp_total || 0} XP`;
  $("metric-league").textContent = profile.league_tier || "APPRENTICE";
}

async function refreshStudentMetrics() {
  try {
    const profile = await api(`/api/v1/students/${encodeURIComponent(state.studentId)}`);
    updateHeaderMetrics(profile);
  } catch {
    // ignore if unregistered
  }
}

// --- WebSocket Latency Sensor ---
function connectLatencyWs() {
  if (state.latencyWs) {
    state.latencyWs.close();
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/api/v1/ws/latency/${encodeURIComponent(state.studentId)}`);
  ws.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    const gauge = $("latency-gauge");
    const status = data.idle_timer_frozen
      ? "IDLE FROZEN"
      : data.is_automatic
      ? "AUTO"
      : "SLOW";
    gauge.textContent = `${data.duration_ms} ms (${status})`;
    gauge.className = `font-mono font-semibold ${
      data.is_automatic ? "text-emerald-400" : "text-amber-300"
    }`;
  };
  state.latencyWs = ws;
}

function sendLatencyPing(topicId, isCorrect, durationMs) {
  if (state.latencyWs && state.latencyWs.readyState === WebSocket.OPEN) {
    state.latencyWs.send(
      JSON.stringify({ topic_id: topicId, is_correct: isCorrect, duration_ms: durationMs })
    );
  }
}

// --- Slice 1A: Math Buffet ---
async function loadBuffet() {
  const items = await api(`/api/v1/session/dashboard?student_id=${encodeURIComponent(state.studentId)}`);
  renderBuffet(items);
}

async function refreshBuffet() {
  const res = await api("/api/v1/session/refresh", {
    method: "POST",
    body: JSON.stringify({ student_id: state.studentId }),
  });
  renderBuffet(res.menu || []);
  showBanner("Set-Cover Math Buffet refreshed.", "info");
}

function renderBuffet(items) {
  const grid = $("buffet-grid");
  grid.innerHTML = "";
  if (!items.length) {
    grid.innerHTML = `<div class="card p-5 text-sm text-slate-400 col-span-full">No tasks available yet. Try running Placement Diagnostic or refreshing the buffet.</div>`;
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "card p-4 flex flex-col justify-between gap-3 hover:border-sky-500/60 transition";
    const badgeColor =
      item.task_type === "FRONTIER_LESSON"
        ? "bg-sky-500/20 text-sky-300 border-sky-500/30"
        : "bg-purple-500/20 text-purple-300 border-purple-500/30";
    card.innerHTML = `
      <div class="space-y-2">
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs px-2 py-0.5 rounded border ${badgeColor}">${item.task_type}</span>
          <span class="text-xs text-slate-400 font-mono">Rank ${Number(item.rank_score || 0).toFixed(1)}</span>
        </div>
        <div class="font-mono text-sm text-slate-100 break-all">${item.topic_id}</div>
      </div>
      <button class="launch-task-btn w-full bg-sky-600 hover:bg-sky-500 py-2 rounded-lg text-sm font-medium">
        Start Task
      </button>
    `;
    card.querySelector(".launch-task-btn").addEventListener("click", () => launchTask(item));
    grid.appendChild(card);
  });
}

// --- Slice 1B: Active Practice Loop ---
async function launchTask(item) {
  switchTab("practice");
  $("remediation-drawer").classList.add("hidden");
  $("wooden-coach-banner").textContent = "";
  if (item.task_type === "SPACED_REVIEW" || item.task_type === "TARGETED_REMEDIATION") {
    const session = await api("/api/v1/practice/review/start", {
      method: "POST",
      body: JSON.stringify({
        student_id: state.studentId,
        topic_id: item.topic_id,
        task_type: item.task_type,
      }),
    });
    state.activeLesson = null;
    state.activeReview = { session, index: 0 };
    renderReviewStep();
  } else {
    const step = await api("/api/v1/practice/lesson/start", {
      method: "POST",
      body: JSON.stringify({
        student_id: state.studentId,
        topic_id: item.topic_id,
      }),
    });
    state.activeReview = null;
    state.activeLesson = step;
    renderLessonStep(step);
  }
}

function clearInputs() {
  $("plain-answer-input").value = "";
  const mf = $("math-input");
  if (mf && "value" in mf) mf.value = "";
  $("plain-answer-input").disabled = false;
  state.stepStartedAt = performance.now();
}

function getStudentAnswer() {
  const plain = $("plain-answer-input").value.trim();
  if (plain) return plain;
  const mf = $("math-input");
  return mf && mf.value ? String(mf.value).trim() : "";
}

function renderInstructionCard(cardData) {
  const card = $("ido-card");
  if (!cardData) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  $("ido-tier").textContent = cardData.scaffolding_tier || "";
  $("ido-rule").textContent = cardData.rule_statement || "";
  const ex = cardData.canonical_worked_example || cardData.worked_example;
  const exEl = $("ido-example");
  if (ex) {
    const stepsHtml = (ex.subgoal_steps || [])
      .map(
        (s) =>
          `<div class="border-l-2 border-sky-500/60 pl-2"><div class="text-xs font-semibold text-sky-300">${s.subgoal_label}</div><div>${s.operation}</div><div class="text-xs text-slate-400">${s.justification}</div></div>`
      )
      .join("");
    exEl.innerHTML = `<div class="font-medium text-slate-200">Worked Example: ${ex.problem}</div>${stepsHtml}<div class="text-emerald-300 font-semibold">Answer: ${ex.final_answer}</div>`;
  } else {
    exEl.innerHTML = "";
  }
  renderMath(card);
}

function renderLessonStep(step) {
  $("practice-title").textContent = `Lesson: ${step.topic_id}`;
  $("practice-subtitle").textContent = `KP ${step.kp_index + 1} of ${step.total_kps}`;
  $("practice-progress-label").textContent = `Mastery Gate: ${step.consecutive_correct} / ${step.mastery_gate_n}`;
  $("practice-mode-badge").textContent = "FRONTIER_LESSON";
  $("spotter-btn").classList.add("hidden");
  renderInstructionCard(step.show_instruction_card ? step.instruction_card : null);
  $("problem-stem").textContent = step.problem_instance.rendered_stem || step.problem_instance.stem || "Solve:";
  renderMath($("problem-stem"));
  clearInputs();
}

function renderReviewStep() {
  const { session, index } = state.activeReview;
  const q = session.questions[index];
  $("practice-title").textContent = `Spaced Review: ${session.topic_id}`;
  $("practice-subtitle").textContent = `Question ${index + 1} of ${session.questions.length}`;
  $("practice-progress-label").textContent = `Sequence: ${session.answer_sequence.map((b) => (b ? "✔" : "✖")).join(" ") || "—"}`;
  $("practice-mode-badge").textContent = session.task_type;
  $("spotter-btn").classList.remove("hidden");
  $("ido-card").classList.add("hidden");
  $("problem-stem").textContent = q.problem_instance.rendered_stem || q.problem_instance.stem || "Solve:";
  renderMath($("problem-stem"));
  clearInputs();
}

async function submitPractice() {
  const answer = getStudentAnswer();
  if (!answer) {
    showBanner("Please enter an answer before submitting.", "error");
    return;
  }
  const durationMs = Math.max(100, Math.round(performance.now() - state.stepStartedAt));

  if (state.activeLesson) {
    const step = state.activeLesson;
    const outcome = await api("/api/v1/practice/lesson/submit", {
      method: "POST",
      body: JSON.stringify({
        student_id: state.studentId,
        topic_id: step.topic_id,
        kp_id: step.kp_id,
        kp_index: step.kp_index,
        student_answer: answer,
        duration_ms: durationMs,
        target_answer: step.problem_instance.target_answer,
        parameters: step.problem_instance.parameters,
      }),
    });
    sendLatencyPing(step.topic_id, outcome.evaluation.correct, durationMs);
    await refreshStudentMetrics();

    if (outcome.coaching_feedback) {
      $("wooden-coach-banner").textContent = outcome.coaching_feedback.message;
    }
    if (outcome.remediation_card) {
      $("remediation-drawer").classList.remove("hidden");
      $("remediation-rule").textContent = outcome.remediation_card.violated_rule || "";
      $("remediation-explanation").textContent = outcome.remediation_card.explanation || "";
    } else {
      $("remediation-drawer").classList.add("hidden");
    }

    if (outcome.evaluation.node_mastered) {
      showBanner(`Topic '${step.topic_id}' mastered! (+${outcome.evaluation.xp_awarded} XP)`, "success");
      state.activeLesson = null;
      await loadBuffet();
      switchTab("buffet");
    } else if (outcome.next_step) {
      state.activeLesson = outcome.next_step;
      renderLessonStep(outcome.next_step);
    }
  } else if (state.activeReview) {
    const { session, index } = state.activeReview;
    const res = await api("/api/v1/practice/review/submit", {
      method: "POST",
      body: JSON.stringify({
        session_id: session.session_id,
        question_index: index,
        student_answer: answer,
        duration_ms: durationMs,
      }),
    });
    state.activeReview.session = res.session;
    await refreshStudentMetrics();

    if (res.evaluation) {
      showBanner(
        res.evaluation.passed
          ? `Review passed (${res.evaluation.sequence_pattern})! +${res.evaluation.xp_awarded} XP`
          : `Review failed (${res.evaluation.sequence_pattern}). Scheduled for remediation.`,
        res.evaluation.passed ? "success" : "error"
      );
      state.activeReview = null;
      await loadBuffet();
      switchTab("buffet");
    } else {
      state.activeReview.index = index + 1;
      renderReviewStep();
    }
  }
}

async function triggerSpotterPeek() {
  if (!state.activeReview) return;
  $("plain-answer-input").disabled = true;
  const card = await api("/api/v1/practice/spotter/peek", {
    method: "POST",
    body: JSON.stringify({
      session_id: state.activeReview.session.session_id,
      question_index: state.activeReview.index,
      input_locked: true,
    }),
  });
  renderInstructionCard(card);
  showBanner("Spotter Reference opened (input locked, credit discounted).", "info");
}

async function flagActiveQuestion() {
  const topicId = state.activeLesson?.topic_id || state.activeReview?.session.topic_id;
  if (!topicId) return;
  await api("/api/v1/author/flags", {
    method: "POST",
    body: JSON.stringify({
      student_id: state.studentId,
      topic_id: topicId,
      kp_id: state.activeLesson?.kp_id || null,
      problem_stem: $("problem-stem").textContent,
      flag_type: "AMBIGUOUS_WORDING",
      student_comment: "Flagged from Tier 10 Practice UI",
    }),
  });
  showBanner("Question flagged for Curriculum Author review.", "info");
}

// --- Canvas Scratchpad ---
function initScratchpad() {
  const canvas = $("scratchpad");
  const ctx = canvas.getContext("2d");
  let drawing = false;
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2.2;
  ctx.lineCap = "round";

  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) * canvas.width) / r.width,
      y: ((e.clientY - r.top) * canvas.height) / r.height,
    };
  };
  canvas.addEventListener("pointerdown", (e) => {
    drawing = true;
    const p = pos(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    const p = pos(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  });
  window.addEventListener("pointerup", () => {
    drawing = false;
  });
  $("clear-scratchpad-btn").addEventListener("click", () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  });
}

// --- Slice 2: Placement Diagnostic ---
async function startDiagnostic() {
  const res = await api("/api/v1/diagnostic/start", {
    method: "POST",
    body: JSON.stringify({
      student_id: state.studentId,
      enrolled_course_id: state.courseId,
      min_questions: 3,
      max_questions: 10,
    }),
  });
  state.activeDiagnostic = { sessionId: res.session_id, question: res.next_question, coverSize: res.compressed_cover_size };
  renderDiagnosticQuestion();
}

function renderDiagnosticQuestion() {
  const el = $("diag-container");
  const d = state.activeDiagnostic;
  if (!d || !d.question) {
    el.innerHTML = `<p class="text-sm text-emerald-300">Diagnostic complete.</p>`;
    return;
  }
  const stem = d.question.blueprint_instance?.rendered_stem || d.question.topic_id;
  el.innerHTML = `
    <div class="flex items-center justify-between text-xs text-slate-400">
      <span>Topic: <code class="text-sky-300">${d.question.topic_id}</code></span>
      <span>Compressed Cover: ${d.coverSize} nodes</span>
    </div>
    <div id="diag-stem" class="text-lg font-medium py-2">${stem}</div>
    <div class="flex gap-3">
      <input id="diag-answer-input" type="text" placeholder="Enter your answer" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm" />
      <button id="diag-submit-btn" class="bg-sky-600 hover:bg-sky-500 px-4 py-2 rounded-lg text-sm font-semibold">Submit</button>
    </div>
  `;
  renderMath($("diag-stem"));
  state.stepStartedAt = performance.now();
  $("diag-submit-btn").addEventListener("click", submitDiagnostic);
}

async function submitDiagnostic() {
  const ans = $("diag-answer-input").value.trim();
  if (!ans) return;
  const d = state.activeDiagnostic;
  const q = d.question;
  const durationMs = Math.max(100, Math.round(performance.now() - state.stepStartedAt));
  const res = await api("/api/v1/diagnostic/submit", {
    method: "POST",
    body: JSON.stringify({
      session_id: d.sessionId,
      submission: {
        student_id: state.studentId,
        topic_id: q.topic_id,
        kp_id: q.kp_id,
        student_answer: ans,
        target_answer: q.blueprint_instance?.target_answer || null,
        parameters: q.blueprint_instance?.parameters || null,
        duration_ms: durationMs,
      },
    }),
  });
  if (res.is_complete && res.placement_result) {
    const p = res.placement_result;
    $("diag-container").innerHTML = `
      <div class="space-y-2">
        <div class="text-base font-semibold text-emerald-300">Placement Complete (${p.course_progress_pct.toFixed(1)}%)</div>
        <div class="text-sm text-slate-300">Mastered: ${p.mastered_topic_ids.length} | Frontier Available: ${p.available_frontier_topic_ids.length} | Locked: ${p.locked_topic_ids.length}</div>
      </div>
    `;
    await refreshStudentMetrics();
    await loadBuffet();
  } else {
    state.activeDiagnostic.question = res.next_question;
    renderDiagnosticQuestion();
  }
}

// --- Slice 3: Parent Portal ---
async function loadParentSummary() {
  const data = await api(`/api/v1/parent/summary?student_id=${encodeURIComponent(state.studentId)}`);
  $("parent-summary-grid").innerHTML = `
    <div class="card p-4 space-y-1">
      <div class="text-xs text-slate-400">Independence Stage</div>
      <div class="text-lg font-semibold text-sky-300">${data.independence.current_stage}</div>
      <div class="text-xs text-slate-300">${data.independence.recommendation}</div>
    </div>
    <div class="card p-4 space-y-1">
      <div class="text-xs text-slate-400">Habit Health &amp; Streak</div>
      <div class="text-lg font-semibold text-emerald-300">${data.habit.health} (${data.habit.streak_days}d)</div>
      <div class="text-xs text-slate-300">${data.habit.recommendation}</div>
    </div>
    <div class="card p-4 space-y-1">
      <div class="text-xs text-slate-400">Active Coaching Alerts</div>
      <div class="text-lg font-semibold text-amber-300">${data.active_alerts.length}</div>
      <div class="text-xs text-slate-300">Pace: ${Number(data.rolling_pace_xp || 0).toFixed(1)} XP/day</div>
    </div>
  `;
  const list = $("rewards-list");
  list.innerHTML = "";
  data.rewards.forEach((r) => {
    const el = document.createElement("div");
    el.className = "bg-slate-900 border border-slate-700 rounded-lg p-3 flex items-center justify-between";
    el.innerHTML = `<div><div class="text-sm font-medium">${r.title}</div><div class="text-xs text-slate-400">Target: ${r.xp_target} XP</div></div><span class="text-xs px-2 py-0.5 rounded bg-slate-800 text-amber-300">${r.status}</span>`;
    list.appendChild(el);
  });
}

// --- Slice 4: Author Triage ---
async function loadAuthorQueue() {
  const q = await api("/api/v1/author/remediation-queue");
  const container = $("author-queue-container");
  container.innerHTML = `
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="card p-4"><div class="text-xs text-slate-400">Critical Defects (&lt;99%)</div><div class="text-2xl font-bold text-rose-400">${q.critical_defects_count}</div></div>
      <div class="card p-4"><div class="text-xs text-slate-400">Scaffolding Deficits (&lt;95%)</div><div class="text-2xl font-bold text-amber-300">${q.scaffolding_deficits_count}</div></div>
      <div class="card p-4"><div class="text-xs text-slate-400">Open Student Flags</div><div class="text-2xl font-bold text-sky-300">${q.open_flags_count}</div></div>
    </div>
  `;
  if (q.open_flags.length > 0) {
    const list = document.createElement("div");
    list.className = "card p-4 space-y-2";
    q.open_flags.forEach((f) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between gap-3 bg-slate-900 p-3 rounded-lg border border-slate-700";
      row.innerHTML = `<div><div class="text-sm font-mono text-sky-300">${f.topic_id}</div><div class="text-xs text-slate-400">${f.flag_type}: ${f.student_comment || "No comment"}</div></div><button class="resolve-flag-btn text-xs bg-emerald-600 hover:bg-emerald-500 px-3 py-1 rounded">Resolve</button>`;
      row.querySelector(".resolve-flag-btn").addEventListener("click", async () => {
        await api(`/api/v1/author/flags/${encodeURIComponent(f.flag_id)}/resolve`, { method: "POST", body: JSON.stringify({ status: "RESOLVED" }) });
        await loadAuthorQueue();
      });
      list.appendChild(row);
    });
    container.appendChild(list);
  }
}

// --- Bootstrap ---
window.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  $("load-student-btn").addEventListener("click", () => loadOrRegisterStudent().catch((e) => showBanner(e.message, "error")));
  $("refresh-buffet-btn").addEventListener("click", () => refreshBuffet().catch((e) => showBanner(e.message, "error")));
  $("submit-practice-btn").addEventListener("click", () => submitPractice().catch((e) => showBanner(e.message, "error")));
  $("spotter-btn").addEventListener("click", () => triggerSpotterPeek().catch((e) => showBanner(e.message, "error")));
  $("flag-content-btn").addEventListener("click", () => flagActiveQuestion().catch((e) => showBanner(e.message, "error")));
  $("start-diag-btn").addEventListener("click", () => startDiagnostic().catch((e) => showBanner(e.message, "error")));
  $("refresh-parent-btn").addEventListener("click", () => loadParentSummary().catch((e) => showBanner(e.message, "error")));
  $("refresh-author-btn").addEventListener("click", () => loadAuthorQueue().catch((e) => showBanner(e.message, "error")));
  $("reward-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/v1/parent/rewards", {
        method: "POST",
        body: JSON.stringify({
          student_id: state.studentId,
          title: $("reward-title").value.trim(),
          xp_target: Number($("reward-xp").value),
        }),
      });
      $("reward-title").value = "";
      await loadParentSummary();
      showBanner("Custom reward added.", "success");
    } catch (err) {
      showBanner(err.message, "error");
    }
  });

  initScratchpad();
  try {
    await loadCourses();
    await loadOrRegisterStudent();
  } catch (err) {
    showBanner(err.message, "error");
  }
});
