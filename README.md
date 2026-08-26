# OpsPilot — Autonomous Engineering Work Orchestrator

OpsPilot is an autonomous AI-driven engineering work orchestrator designed for modern software development teams. Given a high-level goal, OpsPilot inspects repository state, prioritizes critical issues, plans multi-step fixes, evaluates security/risk policies, executes changes safely through tools, verifies outcomes via test suites, creates Pull Requests, and retains long-term project memory.

---

## 1. What OpsPilot Is

OpsPilot acts as a digital pair programmer and operations engineer. It automates repetitive engineering workflows such as bug fixing, dependency maintenance, flaky test resolution, and CI/CD triage while enforcing strict human-in-the-loop safety policies for high-risk actions.

---

## 2. Problem It Solves

- **Triage Fatigue**: Engineers spend hours sifting through bug reports, CI logs, and dependency updates.
- **Unsafe Automation**: Traditional scripts execute changes without risk assessment or safety guardrails.
- **Context Loss**: AI assistants often forget historical fixes, team conventions, and test setups between runs.

---

## 3. Key Features

- **Autonomous Work Orchestration**: Goal-to-PR workflow with investigation, planning, execution, verification, and reporting.
- **Policy Engine & Guardrails**: Evaluates risk level (Low, Medium, High, Blocked) for every tool action. Requires human approval for high-risk operations (e.g. modifying core code, pushing to `main`).
- **Persistent Project Memory**: Stores testing conventions, repository structures, and past fix outcomes in Firestore / InMemoryStore for contextual reasoning.
- **Deterministic Hackathon Demo Mode**: Zero-dependency offline demo workflow for reliable, reproducible judge evaluation.
- **Full Observability**: Step-by-step event stream, tool call logs, and structured reports.

---

## 4. Architecture

```text
[ User / Next.js UI ] ──(HTTP/REST)──► [ FastAPI Backend ]
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
          [ OpsPilot Orchestrator ]   [ Policy Engine ]       [ Memory Service ]
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
 [ GitHub Toolkit ]    [ Testing Toolkit ]
```

---

## 5. Tech Stack

- **Backend**: Python 3.12+, FastAPI, Pydantic v2, Pytest, Asyncio, Google ADK / Gemini API
- **Frontend**: Next.js 15 (React 19, TypeScript, Vanilla CSS Design System)
- **Infrastructure / Cloud**: Google Cloud Run, Cloud Build, Google Pub/Sub, Firestore, Secret Manager

---

## 6. Local Setup

### Prerequisites
- Python 3.12+
- Node.js 20+

### Clone & Initialize Environment

```powershell
# Create & activate Virtual Environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install backend dependencies
pip install -r backend/requirements.txt

# Install frontend dependencies
cd frontend
npm install
cd ..
```

---

## 7. Environment Variables

Copy `.env.example` to `.env`:

```env
# Gemini / LLM Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash

# Google Cloud Settings
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1

# GitHub Token (for live GitHub operations)
GITHUB_TOKEN=your_github_personal_access_token
GITHUB_OWNER=opspilot
GITHUB_REPO=demo-repo

# Runtime Settings
OPSPILOT_ENV=local
OPSPILOT_LOG_LEVEL=INFO
```

---

## 8. Backend Startup

Start the backend API server locally on port 8000:

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload --port 8000
```

Access API docs at `http://127.0.0.1:8000/docs` or health check at `http://127.0.0.1:8000/health`.

---

## 9. Frontend Startup

Start the Next.js frontend development server on port 3000:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:3000` in your web browser.

---

## 10. Test Commands

### Backend Test Suite (39 Tests)
```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python -m pytest backend/tests -v
```

### Frontend Build & Type Check
```powershell
cd frontend
npm run build
```

---

## 11. Demo Mode

OpsPilot features a built-in, fully deterministic **Demo Mode** (`demo_mode=True`) that does not require live GitHub credentials or external LLM API keys. It simulates a realistic repository with seeded issues (flaky authentication test, missing JWT error handling), runs autonomous investigation, creates a fix branch, applies patches, runs test verification, generates a Pull Request (`#151`), and saves memory.

---

## 12. Real GitHub Workflow

When `demo_mode=False` and a valid `GITHUB_TOKEN` is configured:
1. OpsPilot fetches real repository metadata, open issues, commits, and CI status via the GitHub API.
2. It prioritizes open issues based on labels (`bug`, `critical`, `high-priority`) and issue age.
3. It searches repository code, creates git branches, applies code edits, executes test suites, and opens real Pull Requests.

---

## 13. Deployment Instructions (Google Cloud Run)

OpsPilot includes production Dockerfiles and a Cloud Build pipeline (`infrastructure/cloudrun/cloudbuild.yaml`):

```powershell
# Submit build and deployment to Google Cloud Run
gcloud builds submit --config=infrastructure/cloudrun/cloudbuild.yaml --substitutions=_REGION=us-central1
```

---

## 14. Security Considerations

- **Least Privilege Tools**: All tool calls are scoped to specific repositories and branch targets.
- **Policy Enforcement**: Dangerous actions (e.g. force pushing, direct main branch modifications, shell commands) are evaluated through `PolicyEngine`.
- **Zero Credentials Leakage**: Secrets are loaded strictly via environment variables / Secret Manager and sanitized from logs and state responses.

---

## 15. Known Limitations

- Multi-repo cross-dependencies require sequential jobs.
- Automated code modifications are bounded by max tool call budget (25 steps).

---

## 16. Hackathon Evaluator Quickstart Guide

To evaluate OpsPilot end-to-end in **under 2 minutes**:

### Step 1: Start Backend
```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
```

### Step 2: Start Frontend (in a new terminal)
```powershell
cd frontend
npm run dev
```

### Step 3: Run Demo Workflow in UI
1. Open `http://localhost:3000`.
2. Click **"Start Demo Run"** on the Dashboard (or go to **Execute** and click **"Run Demo Job"**).
3. Watch live step execution:
   - **Goal & Investigation**: Discovers top-priority bug (`#101`: Auth test suite flakiness).
   - **Planning & Memory**: Retrieves testing conventions (`pytest`).
   - **Branch & Modify**: Creates `opspilot/fix-auth-test` and patches `demo_project/auth/token.py`.
   - **Verification**: Simulates test run (`pytest` 0 exit code).
   - **Pull Request**: Opens PR `#151` (`https://github.com/opspilot/demo-repo/pull/151`).
   - **Memory Updated**: Persists successful fix context into memory store.

---
