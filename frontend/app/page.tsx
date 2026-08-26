"use client";

import { useEffect, useMemo, useState, useCallback } from "react";

type Tab = "dashboard" | "repo" | "execute" | "approvals" | "history" | "memory" | "tools" | "report";

type Job = {
  job_id: string;
  project_id: string;
  goal: string;
  status: string;
  github_owner: string | null;
  github_repo: string | null;
  current_step: string | null;
  steps: { name: string; status: string; detail: string }[];
  tools_used: { tool_name: string; risk: string; status: string; duration_ms: number; error?: string }[];
  plan: string[];
  events: { event_id: string; event_type: string; message: string; timestamp: string; step_index?: number }[];
  report: string;
  memory_updated: string[];
  approval_requested_ids: string[];
  approval_resolved_ids: string[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  retry_count: number;
  checkpoint: Record<string, any>;
};

type Approval = {
  approval_id: string;
  job_id: string;
  project_id: string;
  tool_name: string;
  risk: string;
  reason: string;
  status: string;
  requested_by: string;
  approved_by: string | null;
  rejected_by: string | null;
  requested_at: string;
  resolved_at: string | null;
  expires_at: string;
  resolution_note: string | null;
};

type MemoryEntry = {
  memory_id: string;
  project_id: string;
  type: string;
  content: string;
  source: string;
  confidence: number;
  created_at: string;
  updated_at: string;
  metadata: Record<string, any>;
};

type ToolInfo = {
  name: string;
  risk: string;
  needs_approval: boolean;
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_OPSPILOT_API_BASE || "http://127.0.0.1:8000";

const DEFAULT_GOAL = "Clean up my highest-priority engineering work.";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "dashboard", label: "Dashboard", icon: "▦" },
  { id: "repo", label: "Repository", icon: "⌂" },
  { id: "execute", label: "Execute", icon: "▶" },
  { id: "approvals", label: "Approvals", icon: "✓" },
  { id: "history", label: "History", icon: "≡" },
  { id: "memory", label: "Memory", icon: "◉" },
  { id: "tools", label: "Tools", icon: "⚙" },
  { id: "report", label: "Report", icon: "▤" },
];

function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s === "completed") return "var(--success)";
  if (s === "running") return "var(--accent)";
  if (s === "failed" || s.includes("partial")) return "var(--warn)";
  if (s === "needs_approval" || s === "needs_attention") return "var(--warn)";
  if (s === "queued") return "var(--muted)";
  if (s === "cancelled") return "var(--muted)";
  return "var(--muted)";
}

function riskColor(risk: string): string {
  const r = risk.toLowerCase();
  if (r === "low") return "var(--success)";
  if (r === "medium") return "var(--accent)";
  if (r === "high") return "var(--warn)";
  return "var(--danger)";
}

function formatTs(ts: string | null): string {
  if (!ts) return "-";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("dashboard");

  const [goal, setGoal] = useState(DEFAULT_GOAL);
  const [githubOwner, setGithubOwner] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [demoMode, setDemoMode] = useState(true);
  const [autoApprove, setAutoApprove] = useState(true);

  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  const [health, setHealth] = useState<{ status: string; environment: string; version: string } | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);

  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [recentJobs, setRecentJobs] = useState<Job[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [memoryEntries, setMemoryEntries] = useState<MemoryEntry[]>([]);
  const [memoryProjectId, setMemoryProjectId] = useState<string>("");
  const [memoryQuery, setMemoryQuery] = useState<string>("");

  const [actionLoading, setActionLoading] = useState<string>("");
  const [toast, setToast] = useState<{ kind: "info" | "success" | "error"; msg: string } | null>(null);

  const showToast = useCallback((kind: "info" | "success" | "error", msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3200);
  }, []);

  const projectId = useMemo(() => {
    if (activeJob) return activeJob.project_id;
    const o = githubOwner || "opspilot";
    const r = githubRepo || "demo-repo";
    return `github:${o.toLowerCase()}/${r.toLowerCase()}`;
  }, [activeJob, githubOwner, githubRepo]);

  useEffect(() => {
    setMemoryProjectId(projectId);
  }, [projectId]);

  const fetchHealth = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/health`);
      const d = await r.json();
      setHealth(d);
      setHealthOk(d.status === "ok");
    } catch {
      setHealthOk(false);
    }
  }, []);

  const fetchTools = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/tools`);
      const d = await r.json();
      setTools(d.tools || []);
    } catch {}
  }, []);

  const fetchRecentJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/jobs?limit=20`);
      const d = await r.json();
      setRecentJobs(d.jobs || []);
    } catch {}
  }, []);

  const fetchApprovals = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/approvals`);
      const d = await r.json();
      setApprovals(d.approvals || []);
    } catch {}
  }, []);

  const fetchMemory = useCallback(async (projId: string, q: string) => {
    try {
      const params = new URLSearchParams();
      if (q) params.set("query", q);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const r = await fetch(`${API_BASE}/api/memory/${encodeURIComponent(projId)}${suffix}`);
      if (!r.ok) {
        setMemoryEntries([]);
        return;
      }
      const d = await r.json();
      setMemoryEntries(d.entries || []);
    } catch {
      setMemoryEntries([]);
    }
  }, []);

  const fetchActiveJob = useCallback(async (jobId: string) => {
    try {
      const r = await fetch(`${API_BASE}/api/jobs/${encodeURIComponent(jobId)}`);
      if (!r.ok) return;
      const d: Job = await r.json();
      setActiveJob(d);
    } catch {}
  }, []);

  useEffect(() => {
    fetchHealth();
    fetchTools();
    fetchRecentJobs();
    fetchApprovals();
  }, [fetchHealth, fetchTools, fetchRecentJobs, fetchApprovals]);

  useEffect(() => {
    if (memoryProjectId) fetchMemory(memoryProjectId, memoryQuery);
  }, [memoryProjectId, memoryQuery, fetchMemory]);

  useEffect(() => {
    if (!activeJobId) return;
    fetchActiveJob(activeJobId);
    const running = new Set(["running", "queued", "needs_approval"]);
    if (activeJob && running.has(activeJob.status.toLowerCase())) {
      const id = setInterval(() => fetchActiveJob(activeJobId), 1500);
      return () => clearInterval(id);
    }
    return;
  }, [activeJobId, activeJob?.status, fetchActiveJob]);

  useEffect(() => {
    const id = setInterval(() => {
      fetchRecentJobs();
      fetchApprovals();
    }, 3000);
    return () => clearInterval(id);
  }, [fetchRecentJobs, fetchApprovals]);

  const startJob = async (demo: boolean) => {
    setActionLoading("start");
    try {
      if (demo) {
        const r = await fetch(`${API_BASE}/api/demo/start`, { method: "POST" });
        if (!r.ok) throw new Error("demo start failed");
        const d = await r.json();
        setActiveJobId(d.job_id);
        setTab("execute");
        showToast("success", `Demo job created: ${d.job_id}. Auto-polling live status.`);
      } else {
        const body: any = {
          goal,
          demo_mode: demoMode,
          auto_approve: autoApprove,
        };
        if (githubOwner) body.github_owner = githubOwner;
        if (githubRepo) body.github_repo = githubRepo;
        const r = await fetch(`${API_BASE}/api/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error("job creation failed");
        const d = await r.json();
        setActiveJobId(d.job_id);
        setTab("execute");
        showToast("success", `Job ${d.job_id} queued. It continues even if you close this page.`);
      }
    } catch (e: any) {
      showToast("error", e?.message || "Could not create job");
    } finally {
      setActionLoading("");
    }
  };

  const cancelJob = async (jobId: string) => {
    try {
      await fetch(`${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      fetchRecentJobs();
      if (activeJobId === jobId) fetchActiveJob(jobId);
      showToast("info", `Cancellation requested for ${jobId}`);
    } catch (e: any) {
      showToast("error", e?.message || "Cancel failed");
    }
  };

  const resolveApproval = async (approvalId: string, action: "approve" | "reject") => {
    try {
      const r = await fetch(`${API_BASE}/api/approvals/${encodeURIComponent(approvalId)}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor: "user", note: action === "approve" ? "Approved via OpsPilot UI" : "Rejected via OpsPilot UI" }),
      });
      if (!r.ok) throw new Error(`Could not ${action}`);
      fetchApprovals();
      if (activeJobId) fetchActiveJob(activeJobId);
      showToast("success", `${action === "approve" ? "Approved" : "Rejected"} ${approvalId}`);
    } catch (e: any) {
      showToast("error", e?.message || "Approval action failed");
    }
  };

  return (
    <div className="app-root">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">OP</div>
          <div className="brand-text">
            <div className="brand-name">OpsPilot</div>
            <div className="brand-tagline">Autonomous Engineering Work Orchestrator</div>
          </div>
        </div>
        <div className="topbar-right">
          <div className="health-pill">
            <span
              className="health-dot"
              style={{
                background: healthOk === true ? "var(--success)" : healthOk === false ? "var(--danger)" : "var(--muted)",
              }}
            />
            <span className="health-label">
              {healthOk === true ? "API online" : healthOk === false ? "API offline" : "Checking API…"}
            </span>
            {health ? <span className="health-version">v{health.version}</span> : null}
          </div>
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? "tab-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            <span className="tab-icon">{t.icon}</span>
            <span>{t.label}</span>
            {t.id === "approvals" && approvals.filter((a) => a.status === "requested").length > 0 && (
              <span className="tab-badge">{approvals.filter((a) => a.status === "requested").length}</span>
            )}
          </button>
        ))}
      </nav>

      <main className="main">
        {toast ? (
          <div className={`toast toast-${toast.kind}`} onClick={() => setToast(null)}>
            {toast.msg}
          </div>
        ) : null}

        {tab === "dashboard" && (
          <Dashboard
            recentJobs={recentJobs}
            approvals={approvals}
            tools={tools}
            activeJob={activeJob}
            onSelectJob={(id) => {
              setActiveJobId(id);
              setTab("execute");
            }}
            onStartDemo={() => startJob(true)}
            actionLoading={actionLoading}
          />
        )}

        {tab === "repo" && (
          <RepositoryPanel
            owner={githubOwner}
            repo={githubRepo}
            demoMode={demoMode}
            setOwner={setGithubOwner}
            setRepo={setGithubRepo}
            setDemo={setDemoMode}
            onStart={() => startJob(demoMode)}
            actionLoading={actionLoading}
            projectId={projectId}
          />
        )}

        {tab === "execute" && (
          <ExecutePanel
            goal={goal}
            setGoal={setGoal}
            owner={githubOwner}
            setOwner={setGithubOwner}
            repo={githubRepo}
            setRepo={setGithubRepo}
            demoMode={demoMode}
            setDemo={setDemoMode}
            autoApprove={autoApprove}
            setAuto={setAutoApprove}
            onStart={(demo) => startJob(demo)}
            activeJob={activeJob}
            activeJobId={activeJobId}
            onSelectJob={(id) => setActiveJobId(id)}
            recentJobs={recentJobs}
            onCancel={cancelJob}
            actionLoading={actionLoading}
            onGoApprovals={() => setTab("approvals")}
            onGoReport={() => setTab("report")}
          />
        )}

        {tab === "approvals" && (
          <ApprovalsPanel approvals={approvals} onResolve={resolveApproval} onRefresh={fetchApprovals} />
        )}

        {tab === "history" && (
          <HistoryPanel jobs={recentJobs} onOpen={(id) => { setActiveJobId(id); setTab("execute"); }} onCancel={cancelJob} />
        )}

        {tab === "memory" && (
          <MemoryPanel
            projectId={memoryProjectId}
            setProjectId={setMemoryProjectId}
            query={memoryQuery}
            setQuery={setMemoryQuery}
            entries={memoryEntries}
            onRefresh={() => fetchMemory(memoryProjectId, memoryQuery)}
          />
        )}

        {tab === "tools" && <ToolsPanel tools={tools} onRefresh={fetchTools} />}

        {tab === "report" && (
          <ReportPanel job={activeJob} jobId={activeJobId} />
        )}
      </main>

      <footer className="footer">
        <div>OpsPilot · All Things Agentic Hackathon 2026 · Taskmaster track</div>
        <div className="footer-right">
          <span>Frontend: Next.js</span>
          <span className="sep">·</span>
          <span>Backend: FastAPI + Google ADK + Gemini</span>
          <span className="sep">·</span>
          <span>Cloud: Cloud Run · Pub/Sub · Firestore</span>
        </div>
      </footer>
    </div>
  );
}

function Dashboard(props: {
  recentJobs: Job[];
  approvals: Approval[];
  tools: ToolInfo[];
  activeJob: Job | null;
  onSelectJob: (id: string) => void;
  onStartDemo: () => void;
  actionLoading: string;
}) {
  const { recentJobs, approvals, tools, onSelectJob, onStartDemo, actionLoading } = props;
  const pendingApprovals = approvals.filter((a) => a.status === "requested").length;
  const completed = recentJobs.filter((j) => j.status === "completed").length;
  const running = recentJobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const failed = recentJobs.filter((j) => j.status === "failed" || j.status === "needs_attention").length;
  const lastJob = recentJobs[0];

  return (
    <div className="grid-2col">
      <section className="panel panel-hero">
        <div className="hero-goal-label">Goal</div>
        <h1 className="hero-goal">Give OpsPilot a goal, walk away, and return to verified engineering work.</h1>
        <p className="hero-sub">
          OpsPilot inspects the repository, discovers unfinished work, prioritizes tasks,
          executes safely, runs tests, opens a PR, and stores project memory — all
          autonomously in a background job that survives page reloads.
        </p>
        <div className="hero-actions">
          <button
            className="btn btn-primary"
            disabled={actionLoading === "start"}
            onClick={onStartDemo}
          >
            {actionLoading === "start" ? "Launching demo…" : "▶  Launch 4-Minute Demo"}
          </button>
          <span className="hero-note">
            Demo mode: seeded repo · 5 seeded issues · 3 failing auth tests · deterministic PR outcome
          </span>
        </div>
        <div className="hero-metrics">
          <Metric label="Pending Approvals" value={pendingApprovals} tone="warn" />
          <Metric label="Running Jobs" value={running} tone="accent" />
          <Metric label="Completed Jobs" value={completed} tone="success" />
          <Metric label="Needs Attention" value={failed} tone="danger" />
        </div>
      </section>

      <section className="panel">
        <h2>Latest Job Live State</h2>
        {lastJob ? (
          <JobSummaryMini job={lastJob} onOpen={() => onSelectJob(lastJob.job_id)} />
        ) : (
          <p className="muted">No jobs yet. Launch the demo to see the live agent loop.</p>
        )}

        <h2 style={{ marginTop: 24 }}>Approval Center</h2>
        {pendingApprovals === 0 ? (
          <p className="muted">No pending approvals. Medium/High risk actions will surface here.</p>
        ) : (
          <ul className="list">
            {approvals.filter((a) => a.status === "requested").slice(0, 4).map((a) => (
              <li key={a.approval_id} className="list-item">
                <div>
                  <strong>{a.tool_name}</strong>{" "}
                  <span className="tag" style={{ background: riskColor(a.risk) + "22", color: riskColor(a.risk) }}>
                    {a.risk}
                  </span>
                </div>
                <div className="muted small">{a.reason}</div>
                <div className="small muted">Job {a.job_id}</div>
              </li>
            ))}
          </ul>
        )}

        <h2 style={{ marginTop: 24 }}>Tool Surface</h2>
        <div className="chip-row">
          {tools.map((t) => (
            <span
              key={t.name}
              className="chip"
              style={{
                borderColor: riskColor(t.risk),
                color: riskColor(t.risk),
              }}
              title={t.needs_approval ? "Requires human approval" : "Auto-executed (safe)"}
            >
              {t.name}{" "}
              <span className="chip-suffix">{t.needs_approval ? "🔒" : "✓"}</span>
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}

function Metric(props: { label: string; value: number; tone: "accent" | "success" | "warn" | "danger" }) {
  const colorMap = {
    accent: "var(--accent)",
    success: "var(--success)",
    warn: "var(--warn)",
    danger: "var(--danger)",
  };
  return (
    <div className="metric">
      <div className="metric-value" style={{ color: colorMap[props.tone] }}>{props.value}</div>
      <div className="metric-label">{props.label}</div>
    </div>
  );
}

function JobSummaryMini(props: { job: Job; onOpen: () => void }) {
  const { job, onOpen } = props;
  return (
    <div className="card">
      <div className="card-top">
        <div>
          <div className="small muted">Goal</div>
          <div className="goal-inline">{job.goal}</div>
        </div>
        <span className="status-pill" style={{ background: statusColor(job.status) + "22", color: statusColor(job.status) }}>
          {job.status}
        </span>
      </div>
      <div className="muted small">
        {job.github_owner || "opspilot"}/{job.github_repo || "demo-repo"} · job {job.job_id} · started {formatTs(job.started_at || job.created_at)}
      </div>
      <div className="progress-list">
        {job.steps.slice(-5).map((s, i) => (
          <div key={i} className="progress-row">
            <span className="step-dot" style={{ background: statusColor(s.status) }} />
            <span className="progress-step-name">{s.name}</span>
            <span className="progress-step-detail muted small">{s.detail}</span>
          </div>
        ))}
      </div>
      <div className="card-row small">
        <span>Steps: {job.steps.length}</span>
        <span>Tools: {job.tools_used.length}</span>
        <span>Plan: {job.plan.length}</span>
        <span>Events: {job.events.length}</span>
      </div>
      <button className="btn btn-ghost" onClick={onOpen}>Open live view →</button>
    </div>
  );
}

function RepositoryPanel(props: {
  owner: string;
  repo: string;
  demoMode: boolean;
  setOwner: (v: string) => void;
  setRepo: (v: string) => void;
  setDemo: (v: boolean) => void;
  onStart: () => void;
  actionLoading: string;
  projectId: string;
}) {
  return (
    <div className="grid-2col">
      <section className="panel">
        <h2>Connect Repository</h2>
        <p className="muted">
          OpsPilot connects to any GitHub repository. For hackathon judging, use <strong>Demo mode</strong>:
          it runs against a deterministic, seeded repository so the demo always works.
        </p>

        <div className="form-row">
          <label className="switch">
            <input type="checkbox" checked={props.demoMode} onChange={(e) => props.setDemo(e.target.checked)} />
            <span>Enable demo mode (recommended for judging)</span>
          </label>
        </div>

        <div className="form-stack">
          <div className="form-row">
            <label>GitHub owner / org</label>
            <input
              value={props.owner}
              onChange={(e) => props.setOwner(e.target.value)}
              placeholder="e.g. octocat"
              disabled={props.demoMode}
            />
          </div>
          <div className="form-row">
            <label>Repository name</label>
            <input
              value={props.repo}
              onChange={(e) => props.setRepo(e.target.value)}
              placeholder="e.g. Hello-World"
              disabled={props.demoMode}
            />
          </div>
        </div>

        {props.demoMode ? (
          <div className="info-box">
            <strong>Demo mode</strong> uses a seeded repository:
            <ul style={{ marginTop: 8 }}>
              <li>5 seeded issues (bug / critical / dependency / docs / staging)</li>
              <li>3 deliberately failing auth tests (clock skew bug)</li>
              <li>Deterministic PR creation with title + body</li>
              <li>Project memory is persisted after the run</li>
            </ul>
          </div>
        ) : (
          <div className="warn-box">
            In live mode, the agent uses the GitHub token from <code>.env</code>. Keep token permissions minimal (repo read/write).
          </div>
        )}

        <button className="btn btn-primary" onClick={props.onStart} disabled={props.actionLoading === "start"}>
          Start background job with this repository
        </button>
      </section>

      <section className="panel">
        <h2>Project Identity</h2>
        <div className="kv">
          <div><span className="kv-label">Project ID</span><code>{props.projectId}</code></div>
          <div><span className="kv-label">Memory namespace</span><code>opspilot_memory / {props.projectId}</code></div>
          <div><span className="kv-label">Job namespace</span><code>opspilot_jobs / *</code></div>
          <div><span className="kv-label">Default base branch</span><code>main</code></div>
          <div><span className="kv-label">Default test command</span><code>pytest</code></div>
        </div>

        <h2 style={{ marginTop: 24 }}>Risk Model</h2>
        <table className="table">
          <thead><tr><th>Risk</th><th>Examples</th><th>Approval</th></tr></thead>
          <tbody>
            <tr><td><span className="tag" style={{ background: "#10b98122", color: "#10b981" }}>LOW</span></td><td>Read issues, PRs, files, commits, CI</td><td>auto</td></tr>
            <tr><td><span className="tag" style={{ background: "#0f766e22", color: "#0f766e" }}>MEDIUM</span></td><td>Modify files, commit, open PR</td><td>human unless auto_approve</td></tr>
            <tr><td><span className="tag" style={{ background: "#b4530922", color: "#b45309" }}>HIGH</span></td><td>Merge PR, deploy production</td><td>human required</td></tr>
            <tr><td><span className="tag" style={{ background: "#b91c1c22", color: "#b91c1c" }}>BLOCKED</span></td><td>Drop DB, expose secrets, disable security</td><td>never</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}

function ExecutePanel(props: {
  goal: string; setGoal: (v: string) => void;
  owner: string; setOwner: (v: string) => void;
  repo: string; setRepo: (v: string) => void;
  demoMode: boolean; setDemo: (v: boolean) => void;
  autoApprove: boolean; setAuto: (v: boolean) => void;
  onStart: (demo: boolean) => void;
  activeJob: Job | null;
  activeJobId: string | null;
  onSelectJob: (id: string) => void;
  recentJobs: Job[];
  onCancel: (id: string) => void;
  actionLoading: string;
  onGoApprovals: () => void;
  onGoReport: () => void;
}) {
  return (
    <div className="grid-3col">
      <section className="panel panel-wide">
        <h2>Start Agent Run</h2>

        <div className="form-stack">
          <div className="form-row">
            <label>Goal</label>
            <textarea
              value={props.goal}
              onChange={(e) => props.setGoal(e.target.value)}
              rows={4}
              placeholder="Describe the engineering outcome you want…"
            />
          </div>
          <div className="form-row-grid">
            <div className="form-row">
              <label>Owner</label>
              <input
                value={props.owner}
                onChange={(e) => props.setOwner(e.target.value)}
                disabled={props.demoMode}
                placeholder={props.demoMode ? "opspilot (demo)" : ""}
              />
            </div>
            <div className="form-row">
              <label>Repo</label>
              <input
                value={props.repo}
                onChange={(e) => props.setRepo(e.target.value)}
                disabled={props.demoMode}
                placeholder={props.demoMode ? "demo-repo (demo)" : ""}
              />
            </div>
          </div>
          <div className="form-row-grid">
            <label className="switch">
              <input type="checkbox" checked={props.demoMode} onChange={(e) => props.setDemo(e.target.checked)} />
              <span>Demo mode (seeded)</span>
            </label>
            <label className="switch">
              <input type="checkbox" checked={props.autoApprove} onChange={(e) => props.setAuto(e.target.checked)} />
              <span>Auto-approve MEDIUM actions</span>
            </label>
          </div>
        </div>

        <div className="row-btns">
          <button className="btn btn-primary" onClick={() => props.onStart(props.demoMode)} disabled={props.actionLoading === "start"}>
            {props.actionLoading === "start" ? "Starting…" : "Start Background Job"}
          </button>
          <button className="btn btn-ghost" onClick={() => props.onStart(true)} disabled={props.actionLoading === "start"}>
            Quick Launch Demo
          </button>
        </div>

        <div className="note small">
          Background jobs are fire-and-forget. Close this tab and return later; GET /api/jobs/{'{job_id}'} returns the live state.
        </div>
      </section>

      <section className="panel panel-wide">
        <div className="panel-header">
          <h2>Agent Execution Live</h2>
          {props.activeJob ? (
            <span className="status-pill" style={{ background: statusColor(props.activeJob.status) + "22", color: statusColor(props.activeJob.status) }}>
              {props.activeJob.status}
            </span>
          ) : null}
        </div>

        {!props.activeJobId ? (
          <p className="muted">No job selected. Start one above, or pick one from History.</p>
        ) : !props.activeJob ? (
          <p className="muted">Loading job {props.activeJobId}…</p>
        ) : (
          <>
            <div className="sub-grid">
              <div className="sub-col">
                <div className="muted small">Goal</div>
                <div className="goal-inline">{props.activeJob.goal}</div>
                <div className="muted small" style={{ marginTop: 4 }}>
                  {props.activeJob.github_owner || "opspilot"}/{props.activeJob.github_repo || "demo-repo"}
                </div>
              </div>
              <div className="sub-col">
                <div className="mini-kv">
                  <div><span>Tools</span><strong>{props.activeJob.tools_used.length}</strong></div>
                  <div><span>Steps</span><strong>{props.activeJob.steps.length}</strong></div>
                  <div><span>Plan</span><strong>{props.activeJob.plan.length}</strong></div>
                  <div><span>Events</span><strong>{props.activeJob.events.length}</strong></div>
                  <div><span>Memory</span><strong>{props.activeJob.memory_updated.length}</strong></div>
                </div>
                <div className="mini-kv-actions">
                  <button className="btn btn-ghost small" onClick={() => props.onCancel(props.activeJob!.job_id)}>Cancel job</button>
                  <button className="btn btn-ghost small" onClick={props.onGoReport}>Open report →</button>
                </div>
              </div>
            </div>

            <h3>Current Activity</h3>
            <div className="progress-list">
              {props.activeJob.steps.length === 0 ? (
                <div className="muted">Agent is warming up. Polling every 1.5s.</div>
              ) : (
                props.activeJob.steps.map((s, i) => (
                  <div key={i} className="progress-row">
                    <span className="step-dot" style={{ background: statusColor(s.status) }} />
                    <span className="progress-step-name">{s.name}</span>
                    <span className="progress-step-detail muted small">{s.detail}</span>
                  </div>
                ))
              )}
            </div>

            {props.activeJob.approval_requested_ids.length > 0 ? (
              <div className="warn-box" onClick={props.onGoApprovals} style={{ cursor: "pointer" }}>
                🔒 {props.activeJob.approval_requested_ids.length} approval(s) requested — open Approvals to act.
              </div>
            ) : null}

            <h3>Tools Used</h3>
            <div className="tools-table-wrap">
              <table className="table small">
                <thead><tr><th>Tool</th><th>Risk</th><th>Status</th><th>ms</th></tr></thead>
                <tbody>
                  {props.activeJob.tools_used.length === 0 ? (
                    <tr><td colSpan={4} className="muted">No tool calls yet.</td></tr>
                  ) : props.activeJob.tools_used.slice(-16).map((t, i) => (
                    <tr key={i}>
                      <td>{t.tool_name}</td>
                      <td><span className="tag" style={{ background: riskColor(t.risk) + "22", color: riskColor(t.risk) }}>{t.risk}</span></td>
                      <td style={{ color: statusColor(t.status) }}>{t.status}</td>
                      <td>{t.duration_ms}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3>Event Log</h3>
            <div className="event-log">
              {props.activeJob.events.slice(-30).map((e) => (
                <div key={e.event_id} className="event-row">
                  <span className="muted small">{formatTs(e.timestamp)}</span>
                  <span className="tag tag-neutral">{e.event_type}</span>
                  <span>{e.message}</span>
                </div>
              ))}
            </div>

            {props.activeJob.report ? (
              <>
                <h3>Final Report</h3>
                <pre className="report-pre">{props.activeJob.report}</pre>
              </>
            ) : null}
          </>
        )}
      </section>

      <section className="panel panel-side">
        <h2>Recent Jobs</h2>
        <ul className="job-list">
          {props.recentJobs.length === 0 ? <li className="muted">No jobs yet.</li> : null}
          {props.recentJobs.slice(0, 10).map((j) => (
            <li
              key={j.job_id}
              className={`job-list-item ${j.job_id === props.activeJobId ? "active" : ""}`}
              onClick={() => props.onSelectJob(j.job_id)}
            >
              <div className="job-list-top">
                <span className="status-dot" style={{ background: statusColor(j.status) }} />
                <span className="job-list-id">{j.job_id}</span>
                <span className="status-chip" style={{ color: statusColor(j.status) }}>{j.status}</span>
              </div>
              <div className="job-list-goal muted small">{j.goal}</div>
              <div className="job-list-meta small muted">
                {j.steps.length} steps · {j.tools_used.length} tools · {formatTs(j.created_at)}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function ApprovalsPanel(props: {
  approvals: Approval[];
  onResolve: (id: string, action: "approve" | "reject") => void;
  onRefresh: () => void;
}) {
  const [filter, setFilter] = useState<"all" | "requested" | "resolved">("all");
  const filtered = props.approvals.filter((a) => {
    if (filter === "all") return true;
    if (filter === "requested") return a.status === "requested";
    return a.status !== "requested";
  });
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Human Approval Center</h2>
        <div className="row-btns">
          <div className="segmented">
            {(["all", "requested", "resolved"] as const).map((v) => (
              <button
                key={v}
                className={`seg-btn ${filter === v ? "seg-active" : ""}`}
                onClick={() => setFilter(v)}
              >{v}</button>
            ))}
          </div>
          <button className="btn btn-ghost small" onClick={props.onRefresh}>Refresh</button>
        </div>
      </div>
      <p className="muted">
        MEDIUM and HIGH risk actions pause the agent until a human approves.
        Backend re-validates approval state before the tool runs.
      </p>
      <div className="approval-grid">
        {filtered.length === 0 ? <p className="muted">No approvals in this view.</p> : null}
        {filtered.map((a) => (
          <div key={a.approval_id} className="approval-card">
            <div className="approval-top">
              <div>
                <strong>{a.tool_name}</strong>{" "}
                <span className="tag" style={{ background: riskColor(a.risk) + "22", color: riskColor(a.risk) }}>{a.risk}</span>
              </div>
              <span className="status-pill" style={{ background: statusColor(a.status) + "22", color: statusColor(a.status) }}>
                {a.status}
              </span>
            </div>
            <p className="approval-reason">{a.reason}</p>
            <div className="kv small">
              <div><span className="kv-label">Job</span><code>{a.job_id}</code></div>
              <div><span className="kv-label">Project</span><code>{a.project_id}</code></div>
              <div><span className="kv-label">Requested by</span><span>{a.requested_by}</span></div>
              <div><span className="kv-label">Requested at</span><span>{formatTs(a.requested_at)}</span></div>
              <div><span className="kv-label">Expires at</span><span>{formatTs(a.expires_at)}</span></div>
              {a.resolution_note ? <div><span className="kv-label">Note</span><span>{a.resolution_note}</span></div> : null}
            </div>
            {a.status === "requested" ? (
              <div className="row-btns">
                <button className="btn btn-primary small" onClick={() => props.onResolve(a.approval_id, "approve")}>✓ Approve</button>
                <button className="btn btn-danger small" onClick={() => props.onResolve(a.approval_id, "reject")}>✕ Reject</button>
              </div>
            ) : (
              <div className="small muted">
                Resolved by {a.approved_by || a.rejected_by || "-"} at {formatTs(a.resolved_at)}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function HistoryPanel(props: { jobs: Job[]; onOpen: (id: string) => void; onCancel: (id: string) => void }) {
  return (
    <section className="panel">
      <h2>Task History</h2>
      <p className="muted">All jobs (queued / running / completed / failed) visible across reloads.</p>
      <table className="table">
        <thead>
          <tr>
            <th>Job</th>
            <th>Goal</th>
            <th>Project</th>
            <th>Status</th>
            <th>Steps</th>
            <th>Tools</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {props.jobs.length === 0 ? (
            <tr><td colSpan={8} className="muted">No jobs yet.</td></tr>
          ) : props.jobs.map((j) => (
            <tr key={j.job_id}>
              <td><code>{j.job_id}</code></td>
              <td className="td-goal">{j.goal}</td>
              <td className="muted small">{j.project_id}</td>
              <td><span className="status-chip" style={{ color: statusColor(j.status) }}>{j.status}</span></td>
              <td>{j.steps.length}</td>
              <td>{j.tools_used.length}</td>
              <td className="small muted">{formatTs(j.created_at)}</td>
              <td>
                <div className="row-btns compact">
                  <button className="btn btn-ghost small" onClick={() => props.onOpen(j.job_id)}>Open</button>
                  {new Set(["running", "queued", "needs_approval"]).has(j.status) ? (
                    <button className="btn btn-danger small" onClick={() => props.onCancel(j.job_id)}>Cancel</button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function MemoryPanel(props: {
  projectId: string;
  setProjectId: (v: string) => void;
  query: string;
  setQuery: (v: string) => void;
  entries: MemoryEntry[];
  onRefresh: () => void;
}) {
  const byType = useMemo(() => {
    const g: Record<string, MemoryEntry[]> = {};
    for (const e of props.entries) {
      (g[e.type] ||= []).push(e);
    }
    return g;
  }, [props.entries]);
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Project Memory</h2>
        <button className="btn btn-ghost small" onClick={props.onRefresh}>Refresh</button>
      </div>
      <p className="muted">
        OpsPilot does not just store chat history. It stores engineering knowledge: conventions,
        testing rules, successful fixes, failed approaches, previous decisions. Memory is consulted
        at planning time so every run gets smarter.
      </p>
      <div className="form-row-grid">
        <div className="form-row">
          <label>Project ID</label>
          <input value={props.projectId} onChange={(e) => props.setProjectId(e.target.value)} />
        </div>
        <div className="form-row">
          <label>Search memory</label>
          <input value={props.query} onChange={(e) => props.setQuery(e.target.value)} placeholder="search keywords…" />
        </div>
      </div>
      <div className="memory-types">
        {Object.keys(byType).length === 0 ? (
          <div className="muted">No memory entries yet for this project. Run a job to seed conventions, fixes, and decisions.</div>
        ) : (
          Object.entries(byType).map(([type, list]) => (
            <div key={type} className="memory-section">
              <h3>{type} <span className="muted small">({list.length})</span></h3>
              <ul className="memory-list">
                {list.map((e) => (
                  <li key={e.memory_id} className="memory-item">
                    <div className="memory-head">
                      <span className="memory-conf">conf {e.confidence.toFixed(2)}</span>
                      <span className="muted small">{e.source}</span>
                      <span className="muted small">{formatTs(e.updated_at)}</span>
                    </div>
                    <div className="memory-content">{e.content}</div>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function ToolsPanel(props: { tools: ToolInfo[]; onRefresh: () => void }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Tool Catalog & Permissions</h2>
        <button className="btn btn-ghost small" onClick={props.onRefresh}>Refresh</button>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Tool</th>
            <th>Risk</th>
            <th>Approval required</th>
            <th>Category</th>
          </tr>
        </thead>
        <tbody>
          {props.tools.length === 0 ? (
            <tr><td colSpan={4} className="muted">Tool list not loaded. Check API connectivity.</td></tr>
          ) : props.tools.map((t) => {
            const cat =
              t.name.startsWith("list") || t.name.startsWith("get") || t.name.startsWith("search") ? "Read / discover" :
              t.name.startsWith("modify") || t.name.startsWith("create_branch") || t.name.startsWith("create_commit") || t.name.startsWith("create_pull_request") ? "Write" :
              t.name.startsWith("run") ? "Verification" : "Other";
            return (
              <tr key={t.name}>
                <td><code>{t.name}</code></td>
                <td><span className="tag" style={{ background: riskColor(t.risk) + "22", color: riskColor(t.risk) }}>{t.risk}</span></td>
                <td>{t.needs_approval ? "🔒 Yes (human)" : "✓ Automatic"}</td>
                <td>{cat}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function ReportPanel(props: { job: Job | null; jobId: string | null }) {
  if (!props.job) {
    return (
      <section className="panel">
        <h2>Final Report</h2>
        <p className="muted">
          {props.jobId ? `Loading report for ${props.jobId}…` : "Start and complete a job to see the final verified report."}
        </p>
      </section>
    );
  }
  const j = props.job;
  return (
    <section className="panel report-panel">
      <div className="panel-header">
        <h2>OpsPilot Final Report</h2>
        <span className="status-pill" style={{ background: statusColor(j.status) + "22", color: statusColor(j.status) }}>
          {j.status}
        </span>
      </div>
      <div className="kv report-meta">
        <div><span className="kv-label">Job</span><code>{j.job_id}</code></div>
        <div><span className="kv-label">Project</span><code>{j.project_id}</code></div>
        <div><span className="kv-label">Repository</span><span>{j.github_owner || "opspilot"}/{j.github_repo || "demo-repo"}</span></div>
        <div><span className="kv-label">Started</span><span>{formatTs(j.started_at)}</span></div>
        <div><span className="kv-label">Completed</span><span>{formatTs(j.completed_at)}</span></div>
      </div>

      <h3>Goal</h3>
      <blockquote className="goal-block">{j.goal}</blockquote>

      <h3>Execution Plan</h3>
      <ol className="ordered-plan">
        {j.plan.map((p, i) => <li key={i}>{p}</li>)}
      </ol>

      <h3>Completed Steps</h3>
      <ul className="result-list">
        {j.steps.map((s, i) => (
          <li key={i} className="result-item">
            <span className="step-dot" style={{ background: statusColor(s.status) }} />
            <div>
              <strong>{s.name}</strong> <span className="muted small">({s.status})</span>
              <div className="muted">{s.detail}</div>
            </div>
          </li>
        ))}
      </ul>

      <h3>Tools Executed</h3>
      <table className="table small">
        <thead><tr><th>Tool</th><th>Risk</th><th>Status</th><th>Duration (ms)</th><th>Error</th></tr></thead>
        <tbody>
          {j.tools_used.map((t, i) => (
            <tr key={i}>
              <td>{t.tool_name}</td>
              <td><span className="tag" style={{ background: riskColor(t.risk) + "22", color: riskColor(t.risk) }}>{t.risk}</span></td>
              <td style={{ color: statusColor(t.status) }}>{t.status}</td>
              <td>{t.duration_ms}</td>
              <td className="muted small">{t.error || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {j.checkpoint && Object.keys(j.checkpoint).length > 0 ? (
        <>
          <h3>Verified Artifacts</h3>
          <ul className="result-list">
            {Object.entries(j.checkpoint).map(([k, v]) => (
              <li key={k} className="result-item">
                <span className="step-dot" style={{ background: "var(--success)" }} />
                <div>
                  <strong>{k}</strong>
                  <div className="muted">{typeof v === "string" ? v : JSON.stringify(v)}</div>
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {j.memory_updated.length > 0 ? (
        <>
          <h3>Memory Updated</h3>
          <ul className="result-list">
            {j.memory_updated.map((mid) => (
              <li key={mid} className="result-item">
                <span className="step-dot" style={{ background: "var(--accent)" }} />
                <div>
                  <strong>Stored memory entry</strong>
                  <div className="muted"><code>{mid}</code></div>
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3>Report Block</h3>
      <pre className="report-pre">{j.report || "(Report not yet available.)"}</pre>

      {j.error ? (
        <>
          <h3>Failure</h3>
          <pre className="report-pre error">{j.error}</pre>
        </>
      ) : null}
    </section>
  );
}
