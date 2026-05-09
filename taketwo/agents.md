# ClipCure — Full Project Context for Agents

## What ClipCure Does

AI video editor: upload a video → AI detects bad clips (shaky, blurry, corrupt) → generates replacement clips → user reviews/approves → exports final spliced video.

## Stack

- **Backend**: Python/Flask on port 5050, in-memory job store, OpenAI for analysis/generation
- **Frontend**: React 18 + Vite, lucide-react icons, no UI framework

## Backend Architecture

```
backend/
  app.py          — Flask API (routes below)
  config.py       — paths: UPLOADS, FRAMES, CLIPS, OUTPUTS
  jobs.py         — in-memory dict job store
  models/
    schemas.py    — dataclasses: Job, Slot, Insert, SceneContext
  pipeline/
    detect.py     — find_bad_clips(source_path) → List[Slot]
    analyze.py    — analyze_anchor(frame, issues) → SceneContext
    generate.py   — generate_for_slot(slot, ctx) → List[Insert]
    critic.py     — review(insert, anchor, issues) → sets critic_pass/notes
    splice.py     — apply_decisions(job) → output_path
```

### API Routes

| Method | Path | Purpose |
|--------|------|---------|
| POST | /jobs | Upload video, start processing thread |
| GET | /jobs/:id | Job status + slots + inserts |
| POST | /jobs/:id/inserts/:iid | Set insert status: approved/rejected/cut |
| POST | /jobs/:id/apply | Stitch final video |
| GET | /jobs/:id/file/:kind/:name | Serve files (kind: anchor/clip/output) |

### Data Models

```
Job: id, source_path, status (queued→detecting→analyzing→generating→review→applying→done|error), slots[], inserts[], output_path, error
Slot: id, start_frame, end_frame, fps, quality_score (0-1), anchor_frame_path, issues[]
Insert: id, slot_id, clip_path, prompt, label, critic_pass, critic_notes, status (pending/approved/rejected/cut/applied)
```

### Processing Pipeline

1. `detect` scans video → finds bad clips as Slots with quality scores + issues
2. `analyze` examines anchor frames → produces SceneContext with mood, description, replacement prompts, recommendation (replace/cut)
3. `generate` creates 1-3 replacement clips per slot
4. `critic` reviews each replacement for quality match
5. Status moves to "review" — user decides per slot
6. `splice` stitches final video from decisions

## Frontend Architecture

```
frontend/src/
  main.jsx        — ReactDOM entry
  App.jsx         — page router (upload/review/done)
  index.css       — all styles
  api.js          — fetch wrappers: uploadVideo, getJob, updateInsert, applyEdits, fileUrl
  pages/
    Upload.jsx    — drag-drop file upload
    Review.jsx    — polls job, renders SlotCards, apply bar
    Done.jsx      — download + preview final video
  components/
    SlotCard.jsx  — original vs replacement comparison, insert options, approve/cut/reject
```

### API Client (api.js)

```js
uploadVideo(file)           → POST /jobs (FormData)
getJob(jobId)               → GET /jobs/:id
updateInsert(jobId,iid,st)  → POST /jobs/:id/inserts/:iid {status}
applyEdits(jobId)           → POST /jobs/:id/apply
fileUrl(jobId, kind, name)  → /jobs/:id/file/:kind/:name
```

### Current UI Flow

1. **Upload page**: drag-drop zone, sends file, gets job_id
2. **Review page**: polls job status every 2s, shows processing spinner, then slot cards with:
   - Side-by-side original (bad) vs selected replacement
   - List of AI-generated insert options with critic pass/fail
   - Approve/Cut/Reject buttons per slot
   - Bottom bar: X of Y reviewed + "Apply Edits & Export"
3. **Done page**: video preview + download button + reset

## Target UI Redesign — 3-Panel Colorful Editor

### Layout

```
┌─────────────────────────────────────────────────────────┐
│ TOP BAR: Logo | Steps (Upload→Analyze→Review→Export) | Actions │
├──────────┬──────────────────────────┬───────────────────┤
│ LEFT     │ CENTER                   │ RIGHT             │
│ 280px    │ flex:1                   │ 320px             │
│          │                          │                   │
│ Source   │ Main Video Preview       │ AI Replacement    │
│ Clips    │ (16:9, gradient border)  │ Preview           │
│ (scroll) │                          │                   │
│          │ PiP original in corner   │ Critic badge      │
│ Card:    │ when viewing replacement │ Prompt display    │
│ -thumb   │                          │                   │
│ -quality │ ────────────────────     │ Alternatives list │
│  bar     │ Splice Timeline          │ (cards w/ gauge)  │
│ -issues  │ (colored blocks)         │                   │
│          │ Prev/Next nav            │ ─────────────     │
│ Mini     │                          │ [Keep][Cut][Use]  │
│ Timeline │                          │                   │
└──────────┴──────────────────────────┴───────────────────┘
```

### Color System

| Token | Value | Use |
|-------|-------|-----|
| --bg-deep | #06060E | Main canvas |
| --bg-panel | #0C0C1A | Panel backgrounds |
| --bg-card | #12122A | Cards |
| --gradient-brand | #7C3AED → #EC4899 | Brand, CTAs |
| --gradient-good | #10B981 → #34D399 | Approved/pass |
| --gradient-bad | #F43F5E → #FB7185 | Bad clips, errors |
| --gradient-warn | #F59E0B → #FBBF24 | Warnings, critic flags |
| --gradient-ai | #6366F1 → #818CF8 | AI states |
| --gradient-cyan | #06B6D4 → #22D3EE | Timeline highlights |

### SVG Elements Required

1. **LogoIcon** — film frame + play triangle + sparkle, gradient filled
2. **FilmStripBorder** — repeating sprocket-hole pattern for left panel edge
3. **GridDotsBG** — subtle dot grid pattern (3% opacity) for center panel
4. **NeuralMeshBG** — dot-and-line network for right panel AI area
5. **QualityGauge** — arc/ring SVG showing quality percentage
6. **Timeline blocks** — rounded rects with gradient fills, hatch patterns for bad clips
7. **Playhead** — glowing cyan vertical line
8. **Issue icons** — per-issue-type SVG icons

### Component Plan

| Component | File | Purpose |
|-----------|------|---------|
| TopBar | components/TopBar.jsx | Logo, step progress, circular progress ring, export btn |
| LeftPanel | components/LeftPanel.jsx | Clip cards (thumb + quality bar + issues), mini-timeline |
| CenterPanel | components/CenterPanel.jsx | Video preview with rotating gradient border, PiP, splice timeline, prev/next |
| RightPanel | components/RightPanel.jsx | AI preview with critic badge, alternatives list with gauge SVGs, Keep/Cut/Use buttons |
| UploadOverlay | components/UploadOverlay.jsx | Full-screen modal with glow effect |
| DoneOverlay | components/DoneOverlay.jsx | Full-screen done state with gradient text |
| SVGBackgrounds | components/SVGBackgrounds.jsx | All SVG decorative elements |

### Key Interactions

- **Left panel**: click clip card → selects it, shows in center + populates right panel
- **Center panel**: shows original or replacement video; PiP toggle when viewing replacement; timeline blocks are clickable
- **Right panel**: click alternative → selects it, previews in center; action buttons apply decision
- **Processing**: overlay on center panel with animated dots when AI is working
- **Upload/Done**: full-screen overlays, not pages

### Animations

- Card selection: spring scale + glow bloom (150ms)
- Video border: conic-gradient rotates 360° continuously
- Timeline blocks: replaced segments pulse violet
- Cut blocks: red strikethrough line
- Processing: 3 bouncing dots
- Approval: green ripple (future: confetti burst)

### Responsive

- < 1200px: right panel shrinks to 280px
- < 900px: left panel becomes horizontal scrollable strip at top, right panel at bottom
- mini-timeline hides on mobile

### State Shape (App.jsx)

```
jobId, job (from polling), page (upload|editor|done), outputPath,
selectedSlotId, selectedInsertId, applying
```

Derived: slots = job.slots, inserts = job.inserts, selectedSlotInserts = inserts filtered by selectedSlotId, hasDecision, slotsWithDecision count, currentIndex.
