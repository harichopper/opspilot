# OpsPilot

Autonomous Engineering Work Orchestrator for the All Things Agentic Hackathon 2026 Taskmaster track.

OpsPilot accepts a high-level engineering objective, inspects connected engineering systems, plans work, executes safe steps through tools, verifies outcomes, and reports what happened. This first pass implements the local backend foundation: FastAPI, Gemini/Google ADK configuration, a first OpsPilot agent service, one GitHub read-only tool, and tests.

## Current Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
.\.venv\Scripts\python -m pytest backend\tests
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`.

## API

- `GET /health`
- `POST /api/agent/execute`

Example request:

```json
{
  "goal": "Clean up my highest-priority engineering work.",
  "github_owner": "octocat",
  "github_repo": "Hello-World"
}
```

## Environment Variables

Copy `.env.example` to `.env` for local development. Never commit real secrets.

- `GEMINI_API_KEY`: Gemini API key for live model-backed execution.
- `GEMINI_MODEL`: Gemini model used by the ADK agent configuration.
- `GITHUB_TOKEN`: Optional GitHub token for authenticated GitHub API reads.
- `GITHUB_OWNER` and `GITHUB_REPO`: Optional default repository target.
- `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`: Google Cloud settings for later Cloud Run, Pub/Sub, Firestore, and Secret Manager integration.

## Google Technologies Planned

- Google ADK as the primary agent framework.
- Gemini as the reasoning/model layer.
- Cloud Run for API and worker deployment.
- Pub/Sub for asynchronous jobs.
- Firestore for job state and project memory.
- Secret Manager for runtime secrets.
- Cloud Logging for structured observability.

## What Works Now

- Local FastAPI app.
- Health check endpoint.
- Typed request/response models.
- OpsPilot agent service with ADK agent construction when `google-adk` is installed.
- Read-only GitHub repository metadata tool.
- Structured agent execution report with discovered repository context and first safe plan.

## Next Milestones

1. Add issue, PR, code search, and commit inspection tools.
2. Add repository task discovery and priority scoring.
3. Add safe file modification, test execution, branch/commit, and draft PR creation.
4. Add persisted job state, Pub/Sub worker execution, Firestore memory, and approval flow.
5. Build the polished demo UI and deterministic demo repository workflow.

