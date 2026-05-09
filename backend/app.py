"""
ClipCure API — AI Video Editor
Routes:
  POST /jobs                           upload video, kick off pipeline
  GET  /jobs/<id>                      job status + slots + inserts + logs
  POST /jobs/<id>/inserts/<iid>        {status: approved|rejected|cut}
  POST /jobs/<id>/apply                stitch final video
  GET  /jobs/<id>/file/<kind>/<name>   serve anchor/clip/output files
"""
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Cap simultaneous GPT vision calls: 5 slots × 3 frames each = 15 images at once
# without this — guaranteed 429 storm. Semaphore keeps it at ≤3 in-flight.
_API_SEMAPHORE = threading.Semaphore(3)

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
from backend import config, jobs
from backend.models.schemas import Job, Insert, new_id
from backend.pipeline import detect, analyze, generate, critic, splice

app = Flask(__name__)
CORS(app)

jobs.load_all()


# ─── logging ────────────────────────────────────────────────────────────────

def _log(job: Job, agent: str, msg: str):
    job.logs.append({"agent": agent, "msg": msg, "ts": time.time()})
    jobs.save(job)


# ─── pipeline ───────────────────────────────────────────────────────────────

def _process_slot(job: Job, slot, ctx, idx: int, total: int) -> list:
    """
    Run generate + critic for a single slot.
    Returns a list of Insert objects.
    Thread-safe: does not mutate job directly.
    """
    inserts = []
    vtype   = job.video_meta.get("video_type", "general")
    palette = job.video_meta.get("color_palette", "neutral")

    if ctx.recommendation == "cut":
        _log(job, "angel",
             f"Clip {idx}/{total} is beyond saving — flagging for removal. "
             "Sometimes less is more.")
        inserts.append(Insert(
            id=new_id(), slot_id=slot.id, clip_path="", prompt="",
            label="AI recommends cutting this clip", status="pending",
        ))
        return inserts

    _log(job, "angel",
         f"Generating cinematic replacement for clip {idx}/{total} — "
         f"{vtype} style, {palette} tones, ARRI-quality footage. "
         "This takes ~2–3 min per clip ⏳")

    if slot.replace_end_frame != -1 and slot.replace_end_frame != slot.end_frame:
        _log(job, "angel",
             f"I'm taking over the scene until the next clean cut "
             f"({slot.replacement_duration_sec:.1f}s total) so the splice lands on a hard edit, "
             "not a shaky-to-smooth glitch.")
    elif getattr(slot, "resume_frame_path", ""):
        _log(job, "angel",
             "No nearby hard cut, so I'm using the resume frame as an outro target "
             "to match back into the original shot cleanly.")

    raw_inserts = generate.generate_for_slot(slot, ctx)

    for ins in raw_inserts:
        try:
            _log(job, "devil",
                 f"My turn 😈 — reviewing replacement {ins.label[:60]}...")
            with _API_SEMAPHORE:
                critic.review(ins, slot.anchor_frame_path, slot.issues,
                              video_meta=job.video_meta)
            if ins.critic_pass:
                _log(job, "devil",
                     f"Fine… I'll admit this one passes ✅ — "
                     f"{ins.critic_notes or 'looks consistent with the original.'}")
            else:
                _log(job, "devil",
                     f"Flagging this one ❌ — "
                     f"{ins.critic_notes or 'does not match the scene well enough.'}")
        except Exception as e:
            ins.critic_notes = f"critic error: {e}"
            _log(job, "devil", f"Something went wrong during my review: {e}")

        inserts.append(ins)

    return inserts


def _process(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return
    try:
        # ── Stage 1: Detect ──────────────────────────────────────────────
        print(f"[job {job_id[:8]}] stage=detecting", flush=True)
        job.status = "detecting"
        _log(job, "angel",
             "I'm scanning your footage frame by frame — hunting for blurry shots, "
             "awkward pauses, and visual quality issues...")

        slots, video_meta = detect.find_bad_clips(job.source_path)
        job.slots      = slots
        job.video_meta = video_meta
        jobs.save(job)

        vtype = video_meta.get("video_type", "general")
        palette = video_meta.get("color_palette", "neutral")
        print(f"[job {job_id[:8]}] detected {len(slots)} slot(s), "
              f"video_type={vtype}", flush=True)

        if not slots:
            _log(job, "angel",
                 "Great news — I scanned every frame and your footage looks clean! "
                 "No bad clips detected.")
            job.status = "review"
            jobs.save(job)
            return

        n = len(slots)
        _log(job, "angel",
             f"Found {n} clip{'s' if n != 1 else ''} that need attention. "
             f"This looks like a {vtype} video with {palette} tones — "
             "I'll tailor all replacements to match your style.")

        # ── Stage 2: Analyze (parallel for multiple slots) ───────────────
        job.status = "analyzing"
        jobs.save(job)
        slot_contexts = {}

        def _analyze_one(slot, i):
            issues_str = ", ".join(slot.issues) if slot.issues else "quality issues"
            _log(job, "angel",
                 f"Examining clip {i}/{n} ({issues_str}) — reading scene "
                 "composition, temporal context, and emotional tone.")
            with _API_SEMAPHORE:
                ctx = analyze.analyze_anchor(
                    slot.anchor_frame_path,
                    slot.issues,
                    video_path=job.source_path,
                    slot=slot,
                    video_meta=job.video_meta,
                )
            _log(job, "angel",
                 f"Analysis done for clip {i}. Mood: {ctx.mood}. "
                 f"Recommendation: "
                 f"{'replace with something better ✨' if ctx.recommendation == 'replace' else 'cut this clip ✂️'}.")
            print(f"[job {job_id[:8]}] slot {slot.id[:8]}: "
                  f"rec={ctx.recommendation} mood={ctx.mood}", flush=True)
            return slot.id, ctx

        if n == 1:
            sid, ctx = _analyze_one(slots[0], 1)
            slot_contexts[sid] = ctx
        else:
            with ThreadPoolExecutor(max_workers=min(n, 3)) as pool:
                futures = {pool.submit(_analyze_one, slot, i + 1): slot
                           for i, slot in enumerate(slots)}
                for fut in as_completed(futures):
                    sid, ctx = fut.result()
                    slot_contexts[sid] = ctx

        # ── Stage 3: Generate + Critic (parallel for multiple slots) ─────
        job.status = "generating"
        jobs.save(job)

        def _gen_one(slot, i):
            ctx = slot_contexts[slot.id]
            return _process_slot(job, slot, ctx, i, n)

        if n == 1:
            all_inserts = _gen_one(slots[0], 1)
        else:
            all_inserts = []
            with ThreadPoolExecutor(max_workers=min(n, 2)) as pool:
                futures = {pool.submit(_gen_one, slot, i + 1): slot
                           for i, slot in enumerate(slots)}
                for fut in as_completed(futures):
                    all_inserts.extend(fut.result())

        job.inserts = all_inserts
        job.status  = "review"
        jobs.save(job)

        _log(job, "angel",
             f"All done! 🎬 {len(all_inserts)} replacement option(s) ready. "
             "Head to the review panel to make your decisions.")
        print(f"[job {job_id[:8]}] done — {len(all_inserts)} insert(s)", flush=True)

    except Exception as e:
        job.status = "error"
        job.error  = str(e)
        _log(job, "devil", f"Something broke in the pipeline 💀 — {str(e)}")
        jobs.save(job)
        import traceback
        print(f"[job {job_id[:8]}] PIPELINE ERROR: {e}", flush=True)
        traceback.print_exc()


# ─── routes ─────────────────────────────────────────────────────────────────

@app.post("/jobs")
def create_job():
    if "video" not in request.files:
        return jsonify({"error": "no video file"}), 400
    f   = request.files["video"]
    jid = new_id()
    src_path = os.path.join(config.UPLOADS, f"{jid}_{f.filename}")
    f.save(src_path)
    job = Job(id=jid, source_path=src_path)
    jobs.put(job)
    threading.Thread(target=_process, args=(jid,), daemon=True).start()
    return jsonify({"job_id": jid})


@app.get("/jobs/<jid>")
def get_job(jid):
    job = jobs.get(jid)
    if not job:
        abort(404)
    return jsonify(job.to_dict())


@app.post("/jobs/<jid>/inserts/<iid>")
def update_insert(jid, iid):
    job = jobs.get(jid)
    if not job:
        abort(404)
    new_status = (request.json or {}).get("status")
    if new_status not in ("approved", "rejected", "cut"):
        return jsonify({"error": "status must be approved, rejected, or cut"}), 400
    for ins in job.inserts:
        if ins.id == iid:
            ins.status = new_status
            jobs.save(job)
            return jsonify({"ok": True})
    abort(404)


@app.post("/jobs/<jid>/apply")
def apply_edits(jid):
    job = jobs.get(jid)
    if not job:
        abort(404)
    job.status = "applying"
    jobs.save(job)
    try:
        out = splice.apply_decisions(job)
        job.status = "done"
        jobs.save(job)
        return jsonify({"output": out})
    except Exception as e:
        job.status = "error"
        job.error  = str(e)
        jobs.save(job)
        return jsonify({"error": str(e)}), 500


@app.get("/jobs/<jid>/file/<kind>/<name>")
def serve_file(jid, kind, name):
    base = {
        "anchor": str(config.FRAMES),
        "clip":   str(config.CLIPS),
        "output": str(config.OUTPUTS),
    }.get(kind)
    if not base:
        abort(400)
    path = os.path.join(base, name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True, use_reloader=False)
