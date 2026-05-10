# TakeTwo

AI video editor: detects bad clips → generates AI replacements → user reviews → exports.

## Quick Ref

- Backend: Flask (Python) at `backend/app.py`, port 5050
- Frontend: React 18 + Vite at `frontend/`, port 5173
- Run: `./run.sh` or `cd frontend && npm run dev` + `python -m backend.app`
- No tests yet. No TypeScript.

## Architecture

See `taketwo/agents.md` for full context: data models, API routes, pipeline stages, component plan, and UI redesign spec.

## Conventions

- Styles in `frontend/src/index.css` (no CSS modules, no Tailwind)
- Icons from `lucide-react`
- API client in `frontend/src/api.js` — all routes go through these wrappers
- Backend uses dataclasses in `backend/models/schemas.py`
- Jobs stored in-memory (dict), no DB
- File serving: `/jobs/:id/file/:kind/:name` (kind = anchor|clip|output)
