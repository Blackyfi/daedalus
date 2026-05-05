const state = {
  email: "",
  authed: false,
  projects: [],
  connectors: [],
  tasks: [],
  ideas: [],
  notes: [],
  plans: [],
  runs: [],
  selectedProjectId: null,
  selectedRunId: null,
  terminalSocket: null,
  terminalBuffer: "",
  refreshTimer: null,
};

const els = {
  banner: document.querySelector("#status-banner"),
  authView: document.querySelector("#auth-view"),
  dashboardView: document.querySelector("#dashboard-view"),
  logout: document.querySelector("#logout"),
  refreshAll: document.querySelector("#refresh-all"),
  passwordForm: document.querySelector("#password-form"),
  otpForm: document.querySelector("#otp-form"),
  totpForm: document.querySelector("#totp-form"),
  projectList: document.querySelector("#project-list"),
  projectTitle: document.querySelector("#project-title"),
  projectMeta: document.querySelector("#project-meta"),
  connectorList: document.querySelector("#connector-list"),
  ideaList: document.querySelector("#idea-list"),
  noteList: document.querySelector("#note-list"),
  taskList: document.querySelector("#task-list"),
  planList: document.querySelector("#plan-list"),
  runList: document.querySelector("#run-list"),
  projectForm: document.querySelector("#project-form"),
  taskForm: document.querySelector("#task-form"),
  ideaForm: document.querySelector("#idea-form"),
  noteForm: document.querySelector("#note-form"),
  triggerPlan: document.querySelector("#trigger-plan"),
  loadTranscript: document.querySelector("#load-transcript"),
  terminalMeta: document.querySelector("#terminal-meta"),
  terminalOutput: document.querySelector("#terminal-output"),
  terminalForm: document.querySelector("#terminal-form"),
  terminalText: document.querySelector("#terminal-text"),
  argusReport: document.querySelector("#argus-report"),
  healthApi: document.querySelector("#health-api"),
  healthRealtime: document.querySelector("#health-realtime"),
};

function setBanner(message, tone = "") {
  els.banner.textContent = message;
  els.banner.className = `banner${tone ? ` ${tone}` : ""}`;
}

function parseError(error, fallback) {
  if (!error) return fallback;
  if (typeof error === "string") return error;
  if (error.detail) {
    if (typeof error.detail === "string") return error.detail;
    return JSON.stringify(error.detail);
  }
  return fallback;
}

async function api(path, options = {}) {
  const init = {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (init.body && typeof init.body !== "string") {
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    throw payload || { detail: response.statusText };
  }
  return payload;
}

function tagsFromInput(value) {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function formatTime(value) {
  if (!value) return "n/a";
  return new Date(value).toLocaleString();
}

function selectedProject() {
  return state.projects.find((project) => project.id === state.selectedProjectId) || null;
}

function selectedRun() {
  return state.runs.find((run) => run.id === state.selectedRunId) || null;
}

function ensureRefreshLoop() {
  if (state.refreshTimer) return;
  state.refreshTimer = window.setInterval(async () => {
    if (!state.authed) return;
    try {
      await refreshProjectData(false);
    } catch {
      return;
    }
  }, 5000);
}

function stopRefreshLoop() {
  if (state.refreshTimer) {
    window.clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
}

async function checkHealth() {
  try {
    const result = await api("/api/health", { credentials: "same-origin" });
    els.healthApi.textContent = result.status === "ok" ? "Healthy" : "Unexpected response";
  } catch {
    els.healthApi.textContent = "Unavailable";
  }
}

function showAuthStep(step) {
  els.passwordForm.classList.toggle("hidden", step !== 1);
  els.otpForm.classList.toggle("hidden", step !== 2);
  els.totpForm.classList.toggle("hidden", step !== 3);
}

function showDashboard(authed) {
  state.authed = authed;
  els.authView.classList.toggle("hidden", authed);
  els.dashboardView.classList.toggle("hidden", !authed);
  els.logout.classList.toggle("hidden", !authed);
}

async function detectSession() {
  try {
    await loadDashboard();
    showDashboard(true);
    setBanner("Authenticated. Control room ready.", "success");
    ensureRefreshLoop();
  } catch {
    showDashboard(false);
    showAuthStep(1);
    setBanner("Client certificate accepted. Complete the three login steps to reach the dashboard.");
  }
}

async function loadConnectors() {
  state.connectors = await api("/api/v1/connectors");
  renderConnectorList();
  populateConnectorSelects();
}

async function loadProjects() {
  state.projects = await api("/api/v1/projects");
  if (!state.selectedProjectId && state.projects.length) {
    state.selectedProjectId = state.projects[0].id;
  }
  if (state.selectedProjectId && !state.projects.some((project) => project.id === state.selectedProjectId)) {
    state.selectedProjectId = state.projects[0]?.id || null;
  }
  renderProjects();
}

async function refreshProjectData(showMessage = true) {
  const project = selectedProject();
  if (!project) {
    state.tasks = [];
    state.ideas = [];
    state.notes = [];
    state.plans = [];
    state.runs = [];
    renderSelectedProject();
    renderIdeas();
    renderNotes();
    renderPlans();
    renderTasks();
    renderRuns();
    return;
  }

  const [tasks, ideas, notes, plans, runs] = await Promise.all([
    api(`/api/v1/projects/${project.id}/tasks`),
    api(`/api/v1/projects/${project.id}/ideas`),
    api(`/api/v1/projects/${project.id}/notes`),
    api(`/api/v1/projects/${project.id}/plans?status=pending`),
    api(`/api/v1/runs/projects/${project.id}`),
  ]);
  state.tasks = tasks;
  state.ideas = ideas;
  state.notes = notes;
  state.plans = plans;
  state.runs = runs;
  renderSelectedProject();
  renderIdeas();
  renderNotes();
  renderPlans();
  renderTasks();
  renderRuns();

  if (showMessage) {
    setBanner(`Loaded ${project.name}.`, "success");
  }
}

async function loadDashboard() {
  await Promise.all([loadConnectors(), loadProjects()]);
  await refreshProjectData(false);
  showDashboard(true);
}

function populateConnectorSelects() {
  const options = ['<option value="">Use project default</option>']
    .concat(state.connectors.map((connector) => `<option value="${connector.connector_id}">${connector.display_name}</option>`))
    .join("");
  els.projectForm.elements.default_connector_id.innerHTML = options;
  els.taskForm.elements.connector_id.innerHTML = options;
}

function renderProjects() {
  if (!state.projects.length) {
    els.projectList.innerHTML = '<p class="muted">No projects yet.</p>';
    return;
  }
  els.projectList.innerHTML = state.projects.map((project) => `
    <button class="list-card ${project.id === state.selectedProjectId ? "active" : ""}" data-project-id="${project.id}">
      <header>
        <h3>${escapeHtml(project.name)}</h3>
        <span class="status">${project.archived ? "archived" : "active"}</span>
      </header>
      <p>${escapeHtml(project.description || "No description")}</p>
      <p class="muted">${escapeHtml(project.workspace_path)}</p>
    </button>
  `).join("");
}

function renderSelectedProject() {
  const project = selectedProject();
  if (!project) {
    els.projectTitle.textContent = "No project selected";
    els.projectMeta.textContent = "Create a project or select one from the left rail.";
    return;
  }
  const doneCount = state.tasks.filter((task) => task.status === "done").length;
  els.projectTitle.textContent = project.name;
  els.projectMeta.textContent = `${project.workspace_path} · ${doneCount}/${state.tasks.length} tasks done · default connector: ${project.default_connector_id || "none"}`;
}

function renderConnectorList() {
  if (!state.connectors.length) {
    els.connectorList.innerHTML = '<p class="muted">Import connectors with the CLI before creating tasks.</p>';
    return;
  }
  els.connectorList.innerHTML = state.connectors.map((connector) => `
    <span class="pill">${escapeHtml(connector.display_name)} <span class="muted">${escapeHtml(connector.connector_id)}</span></span>
  `).join("");
}

function renderIdeas() {
  if (!state.ideas.length) {
    els.ideaList.innerHTML = '<p class="muted">No ideas in the box.</p>';
    return;
  }
  els.ideaList.innerHTML = state.ideas.map((idea) => `
    <article class="list-card">
      <header>
        <h4>${escapeHtml(firstLine(idea.text))}</h4>
        <span class="status">${idea.archived ? "archived" : "open"}</span>
      </header>
      <p>${escapeHtml(idea.text)}</p>
      ${renderTags(idea.tags)}
    </article>
  `).join("");
}

function renderNotes() {
  if (!state.notes.length) {
    els.noteList.innerHTML = '<p class="muted">No notes yet.</p>';
    return;
  }
  els.noteList.innerHTML = state.notes.map((note) => `
    <article class="list-card">
      <header>
        <div>
          <h4>${escapeHtml(note.title)}</h4>
          <div class="muted">Updated ${formatTime(note.updated_at)}</div>
        </div>
        <button class="ghost small" data-delete-note="${note.id}">Delete</button>
      </header>
      <p>${escapeHtml(note.body || "No body")}</p>
    </article>
  `).join("");
}

function renderPlans() {
  if (!state.plans.length) {
    els.planList.innerHTML = '<p class="muted">No pending proposals. Use "Plan From Ideas" to draft one.</p>';
    return;
  }
  els.planList.innerHTML = state.plans.map((plan) => `
    <article class="list-card" data-plan-id="${plan.id}">
      <header>
        <div>
          <h4>${plan.proposed_tasks.length} proposed task(s)</h4>
          <div class="muted">Drafted ${formatTime(plan.created_at)}</div>
        </div>
        <span class="status">${plan.status}</span>
      </header>
      <p class="muted">${escapeHtml(plan.rationale || "No rationale provided.")}</p>
      <textarea class="plan-editor" rows="10" data-plan-edit="${plan.id}">${escapeHtml(JSON.stringify(plan.proposed_tasks, null, 2))}</textarea>
      <div class="list-card-actions">
        <button class="small" data-confirm-plan="${plan.id}">Confirm</button>
        <button class="ghost small" data-discard-plan="${plan.id}">Discard</button>
      </div>
    </article>
  `).join("");
}

function renderTasks() {
  if (!state.tasks.length) {
    els.taskList.innerHTML = '<p class="muted">No tasks yet.</p>';
    return;
  }
  els.taskList.innerHTML = state.tasks.map((task) => `
    <article class="list-card">
      <header>
        <div>
          <h4>${escapeHtml(task.title)}</h4>
          <div class="muted">${task.priority} · ${escapeHtml(task.connector_id || "project default")}</div>
        </div>
        <span class="status ${task.status}">${task.status.replaceAll("_", " ")}</span>
      </header>
      <p>${escapeHtml(task.description || "No description")}</p>
      <div class="tag-row">
        <span class="tag">${escapeHtml(task.profile)}</span>
        ${task.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
      </div>
      <div class="list-card-actions">
        <button class="small" data-run-task="${task.id}">Run Task</button>
      </div>
    </article>
  `).join("");
}

function renderRuns() {
  if (!state.runs.length) {
    els.runList.innerHTML = '<p class="muted">No runs yet.</p>';
    return;
  }
  els.runList.innerHTML = state.runs.map((run) => `
    <article class="list-card ${run.id === state.selectedRunId ? "active" : ""}">
      <header>
        <div>
          <h4>${run.kind} · ${run.id.slice(0, 8)}</h4>
          <div class="muted">${formatTime(run.started_at || run.finished_at)}</div>
        </div>
        <span class="status ${run.state}">${run.state.replaceAll("_", " ")}</span>
      </header>
      <p>Started: ${formatTime(run.started_at)} · Finished: ${formatTime(run.finished_at)} · Exit: ${run.exit_code ?? "n/a"}</p>
      <div class="list-card-actions">
        <button class="ghost small" data-open-run="${run.id}">Attach</button>
        <button class="ghost small" data-open-transcript="${run.id}">Transcript</button>
        ${run.kind === "argus" ? `<button class="ghost small" data-open-argus="${run.id}">Argus Report</button>` : ""}
      </div>
    </article>
  `).join("");
}

function firstLine(text) {
  return text.split("\n").find((line) => line.trim()) || "Untitled";
}

function renderTags(tags) {
  if (!tags?.length) return "";
  return `<div class="tag-row">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function closeTerminalSocket() {
  if (state.terminalSocket) {
    state.terminalSocket.close();
    state.terminalSocket = null;
  }
}

async function attachRun(runId) {
  state.selectedRunId = runId;
  state.terminalBuffer = "";
  els.terminalOutput.textContent = "";
  els.argusReport.textContent = "No Argus report loaded.";
  renderRuns();

  const run = selectedRun();
  if (!run) return;

  closeTerminalSocket();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/pty/${runId}`);
  state.terminalSocket = socket;
  els.terminalMeta.textContent = `Attached to ${run.kind} run ${run.id}.`;

  socket.addEventListener("open", () => {
    els.healthRealtime.textContent = "Connected";
  });
  socket.addEventListener("message", (event) => {
    state.terminalBuffer += event.data;
    els.terminalOutput.textContent = state.terminalBuffer;
    els.terminalOutput.scrollTop = els.terminalOutput.scrollHeight;
  });
  socket.addEventListener("close", () => {
    if (state.terminalSocket === socket) {
      els.healthRealtime.textContent = "Disconnected";
    }
  });
}

async function loadArgusReport(runId) {
  try {
    const report = await api(`/api/v1/runs/${runId}/argus`);
    const findings = report.findings?.length
      ? report.findings.map((finding) => `- [${finding.severity}] ${finding.description}${finding.evidence ? ` (${finding.evidence})` : ""}`).join("\n")
      : "No findings.";
    els.argusReport.textContent = `${report.verdict.toUpperCase()}\n${report.summary}\n\n${findings}`;
  } catch (error) {
    els.argusReport.textContent = parseError(error, "Argus report unavailable.");
  }
}

async function loadTranscript(runId) {
  try {
    const text = await api(`/api/v1/runs/${runId}/transcript/text`, {
      headers: { Accept: "text/plain" },
    });
    state.selectedRunId = runId;
    closeTerminalSocket();
    renderRuns();
    state.terminalBuffer = typeof text === "string" ? text : JSON.stringify(text, null, 2);
    els.terminalOutput.textContent = state.terminalBuffer;
    els.terminalMeta.textContent = `Loaded persisted transcript for run ${runId}.`;
    setBanner(`Transcript loaded for ${runId}.`, "success");
  } catch (error) {
    setBanner(parseError(error, "Transcript load failed."), "error");
  }
}

async function runSelectedTask(taskId) {
  const run = await api(`/api/v1/tasks/${taskId}/run`, { method: "POST" });
  setBanner(`Queued run ${run.id}.`, "success");
  await refreshProjectData(false);
  await attachRun(run.id);
}

async function sendRunAction(action) {
  if (!state.selectedRunId) {
    setBanner("Select a run before sending lifecycle actions.", "error");
    return;
  }
  await api(`/api/v1/runs/${state.selectedRunId}/${action}`, { method: "POST" });
  setBanner(`Sent ${action} to ${state.selectedRunId}.`, "success");
}

els.passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(els.passwordForm);
  state.email = String(data.get("email") || "").trim().toLowerCase();
  try {
    await api("/api/v1/auth/password", {
      method: "POST",
      body: { email: state.email, password: data.get("password") },
    });
    showAuthStep(2);
    setBanner("Password accepted. Check email for the OTP code or link.", "success");
  } catch (error) {
    setBanner(parseError(error, "Password step failed."), "error");
  }
});

els.otpForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(els.otpForm);
  try {
    await api("/api/v1/auth/email-otp", {
      method: "POST",
      body: { email: state.email, code: data.get("code") },
    });
    showAuthStep(3);
    setBanner("Email OTP accepted. Finish with TOTP or a recovery code.", "success");
  } catch (error) {
    setBanner(parseError(error, "OTP step failed."), "error");
  }
});

els.totpForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(els.totpForm);
  try {
    await api("/api/v1/auth/totp", {
      method: "POST",
      body: { email: state.email, code: data.get("code") },
    });
    await loadDashboard();
    ensureRefreshLoop();
    setBanner("Login complete.", "success");
  } catch (error) {
    setBanner(parseError(error, "TOTP step failed."), "error");
  }
});

els.logout.addEventListener("click", async () => {
  try {
    await api("/api/v1/auth/logout", { method: "POST" });
  } catch {}
  closeTerminalSocket();
  stopRefreshLoop();
  showDashboard(false);
  showAuthStep(1);
  setBanner("Logged out.");
});

els.projectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(els.projectForm);
  try {
    const project = await api("/api/v1/projects", {
      method: "POST",
      body: {
        name: data.get("name"),
        description: data.get("description"),
        workspace_path: data.get("workspace_path"),
        default_connector_id: data.get("default_connector_id") || null,
      },
    });
    els.projectForm.reset();
    state.selectedProjectId = project.id;
    await loadProjects();
    await refreshProjectData(false);
    setBanner(`Created project ${project.name}.`, "success");
  } catch (error) {
    setBanner(parseError(error, "Project creation failed."), "error");
  }
});

els.ideaForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const project = selectedProject();
  if (!project) return;
  const data = new FormData(els.ideaForm);
  try {
    await api(`/api/v1/projects/${project.id}/ideas`, {
      method: "POST",
      body: { text: data.get("text"), tags: tagsFromInput(String(data.get("tags") || "")) },
    });
    els.ideaForm.reset();
    await refreshProjectData(false);
    setBanner("Idea added.", "success");
  } catch (error) {
    setBanner(parseError(error, "Idea creation failed."), "error");
  }
});

els.noteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const project = selectedProject();
  if (!project) return;
  const data = new FormData(els.noteForm);
  try {
    await api(`/api/v1/projects/${project.id}/notes`, {
      method: "POST",
      body: { title: data.get("title"), body: data.get("body") },
    });
    els.noteForm.reset();
    await refreshProjectData(false);
    setBanner("Note saved.", "success");
  } catch (error) {
    setBanner(parseError(error, "Note save failed."), "error");
  }
});

els.taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const project = selectedProject();
  if (!project) return;
  const data = new FormData(els.taskForm);
  try {
    await api(`/api/v1/projects/${project.id}/tasks`, {
      method: "POST",
      body: {
        title: data.get("title"),
        description: data.get("description"),
        acceptance_criteria: data.get("acceptance_criteria"),
        priority: data.get("priority"),
        connector_id: data.get("connector_id") || null,
        tags: tagsFromInput(String(data.get("tags") || "")),
      },
    });
    els.taskForm.reset();
    await refreshProjectData(false);
    setBanner("Task created.", "success");
  } catch (error) {
    setBanner(parseError(error, "Task creation failed."), "error");
  }
});

els.triggerPlan.addEventListener("click", async () => {
  const project = selectedProject();
  if (!project) return;
  try {
    const result = await api(`/api/v1/projects/${project.id}/plan`, { method: "POST" });
    setBanner(`Planning run queued: ${result.run_id}.`, "success");
    await refreshProjectData(false);
  } catch (error) {
    setBanner(parseError(error, "Planning trigger failed."), "error");
  }
});

els.refreshAll.addEventListener("click", async () => {
  try {
    if (state.authed) {
      await loadDashboard();
      setBanner("Dashboard refreshed.", "success");
    } else {
      await checkHealth();
      setBanner("Health refreshed.");
    }
  } catch (error) {
    setBanner(parseError(error, "Refresh failed."), "error");
  }
});

document.querySelector("#reload-projects").addEventListener("click", () => loadProjects().then(() => refreshProjectData(false)));
document.querySelector("#reload-connectors").addEventListener("click", () => loadConnectors());
document.querySelector("#reload-ideas").addEventListener("click", () => refreshProjectData(false));
document.querySelector("#reload-notes").addEventListener("click", () => refreshProjectData(false));
document.querySelector("#reload-plans").addEventListener("click", () => refreshProjectData(false));
document.querySelector("#reload-tasks").addEventListener("click", () => refreshProjectData(false));
document.querySelector("#reload-runs").addEventListener("click", () => refreshProjectData(false));

els.planList.addEventListener("click", async (event) => {
  const confirmId = event.target.closest("[data-confirm-plan]")?.dataset.confirmPlan;
  if (confirmId) {
    const editor = document.querySelector(`[data-plan-edit="${confirmId}"]`);
    let proposed_tasks = null;
    if (editor && editor.value.trim()) {
      try {
        proposed_tasks = JSON.parse(editor.value);
        if (!Array.isArray(proposed_tasks)) {
          throw new Error("must be a JSON array");
        }
      } catch (error) {
        setBanner(`Plan JSON invalid: ${error.message}`, "error");
        return;
      }
    }
    try {
      await api(`/api/v1/plans/${confirmId}/confirm`, {
        method: "POST",
        body: { proposed_tasks, archive_source_ideas: true },
      });
      setBanner("Plan confirmed.", "success");
      await refreshProjectData(false);
    } catch (error) {
      setBanner(parseError(error, "Plan confirm failed."), "error");
    }
    return;
  }
  const discardId = event.target.closest("[data-discard-plan]")?.dataset.discardPlan;
  if (discardId) {
    try {
      await api(`/api/v1/plans/${discardId}/discard`, { method: "POST" });
      setBanner("Plan discarded.", "success");
      await refreshProjectData(false);
    } catch (error) {
      setBanner(parseError(error, "Plan discard failed."), "error");
    }
  }
});

els.projectList.addEventListener("click", async (event) => {
  const projectId = event.target.closest("[data-project-id]")?.dataset.projectId;
  if (!projectId) return;
  state.selectedProjectId = projectId;
  renderProjects();
  await refreshProjectData(false);
});

els.taskList.addEventListener("click", async (event) => {
  const taskId = event.target.closest("[data-run-task]")?.dataset.runTask;
  if (!taskId) return;
  try {
    await runSelectedTask(taskId);
  } catch (error) {
    setBanner(parseError(error, "Task run failed."), "error");
  }
});

els.runList.addEventListener("click", async (event) => {
  const runId = event.target.closest("[data-open-run]")?.dataset.openRun;
  if (runId) {
    await attachRun(runId);
    return;
  }
  const transcriptId = event.target.closest("[data-open-transcript]")?.dataset.openTranscript;
  if (transcriptId) {
    await loadTranscript(transcriptId);
    return;
  }
  const argusId = event.target.closest("[data-open-argus]")?.dataset.openArgus;
  if (argusId) {
    await loadArgusReport(argusId);
  }
});

els.noteList.addEventListener("click", async (event) => {
  const noteId = event.target.closest("[data-delete-note]")?.dataset.deleteNote;
  if (!noteId) return;
  try {
    await api(`/api/v1/notes/${noteId}`, { method: "DELETE" });
    await refreshProjectData(false);
    setBanner("Note deleted.", "success");
  } catch (error) {
    setBanner(parseError(error, "Note delete failed."), "error");
  }
});

document.querySelectorAll("[data-run-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await sendRunAction(button.dataset.runAction);
    } catch (error) {
      setBanner(parseError(error, "Run action failed."), "error");
    }
  });
});

els.terminalForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedRunId) return;
  const text = els.terminalText.value;
  if (!text) return;
  try {
    await api(`/api/v1/runs/${state.selectedRunId}/inject`, {
      method: "POST",
      body: { text },
    });
    els.terminalText.value = "";
  } catch (error) {
    setBanner(parseError(error, "Terminal input failed."), "error");
  }
});

window.addEventListener("beforeunload", () => closeTerminalSocket());

els.loadTranscript.addEventListener("click", async () => {
  if (!state.selectedRunId) {
    setBanner("Select a run first.", "error");
    return;
  }
  await loadTranscript(state.selectedRunId);
});

checkHealth().then(detectSession);
