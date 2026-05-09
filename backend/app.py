"""
ClipCure API — AI Video Editor
Routes:
  POST /jobs                           upload video, kick off detection
  GET  /jobs/<id>                      job status + slots + inserts
  POST /jobs/<id>/inserts/<iid>        {status: approved|rejected|cut}
  POST /jobs/<id>/apply                stitch final video
  GET  /jobs/<id>/file/<kind>/<name>   serve anchor/clip/output files
"""
import os
import sys
import threading

# Allow running as `python app.py` from inside the backend directory
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
from backend import config, jobs
from backend.models.schemas import Job, new_id
from backend.pipeline import detect, analyze, generate, critic, splice

app = Flask(__name__)
CORS(app)

jobs.load_all()


def _log(job, agent: str, msg: str):
    import time
    job.logs.append({"agent": agent, "msg": msg, "ts": time.time()})
    jobs.save(job)


def _process(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return
    try:
        print(f"[job {job_id[:8]}] stage=detecting", flush=True)
        job.status = "detecting"
        _log(job, "angel", "I'm scanning your footage frame by frame, hunting for blurry shots, awkward pauses, and visual quality issues...")
        job.slots = detect.find_bad_clips(job.source_path)
        n = len(job.slots)
        print(f"[job {job_id[:8]}] detected {n} slot(s)", flush=True)

        if not job.slots:
            _log(job, "angel", "Great news — I scanned every frame and your footage looks clean! No bad clips detected.")
            job.status = "review"
            jobs.save(job)
            return

        _log(job, "angel", f"Found {n} clip{'s' if n != 1 else ''} that need attention. Let me dig deeper into each one...")

        job.status = "analyzing"
        jobs.save(job)
        slot_contexts = {}
        for i, slot in enumerate(job.slots):
            print(f"[job {job_id[:8]}] analyzing slot {i+1}/{len(job.slots)}", flush=True)
            issues_str = ", ".join(slot.issues) if slot.issues else "quality issues"
            _log(job, "angel", f"Examining clip {i+1}/{n} ({issues_str})... reading the scene composition and emotional tone.")
            ctx = analyze.analyze_anchor(slot.anchor_frame_path, slot.issues)
            print(f"[job {job_id[:8]}] → recommendation={ctx.recommendation} mood={ctx.mood}", flush=True)
            _log(job, "angel", f"Analysis done. Mood: {ctx.mood}. My recommendation: {'replace with something better ✨' if ctx.recommendation == 'replace' else 'cut this clip entirely ✂️'}.")
            slot_contexts[slot.id] = ctx

        job.status = "generating"
        jobs.save(job)
        from backend.models.schemas import Insert
        for i, slot in enumerate(job.slots):
            ctx = slot_contexts[slot.id]
            print(f"[job {job_id[:8]}] generating slot {i+1}/{len(job.slots)} ({ctx.recommendation})", flush=True)
            if ctx.recommendation == "cut":
                _log(job, "angel", f"Clip {i+1} is beyond saving — I'm flagging it for removal. Sometimes less is more.")
                job.inserts.append(Insert(
                    id=new_id(), slot_id=slot.id, clip_path="", prompt="",
                    label="AI recommends cutting this clip", status="pending",
                ))
                jobs.save(job)
                continue

            _log(job, "angel", f"Generating cinematic replacement for clip {i+1}... photorealistic 4K, ARRI camera style, smooth motion. This takes ~2–3 minutes per clip ⏳")
            inserts = generate.generate_for_slot(slot, ctx)
            for ins in inserts:
                try:
                    _log(job, "devil", f"My turn 😈 Let me see if this replacement actually fits the original scene...")
                    critic.review(ins, slot.anchor_frame_path, slot.issues)
                    print(f"[job {job_id[:8]}] critic pass={ins.critic_pass}: {ins.critic_notes}", flush=True)
                    if ins.critic_pass:
                        _log(job, "devil", f"Fine... I'll admit it — this one passes. {ins.critic_notes or 'Looks consistent with the original.'} ✅")
                    else:
                        _log(job, "devil", f"Nice try, but I'm flagging this one ❌ — {ins.critic_notes or 'does not match the scene well enough.'}")
                except Exception as e:
                    ins.critic_notes = f"critic error: {e}"
                    _log(job, "devil", f"Something went wrong during my review: {e}")
                job.inserts.append(ins)
            jobs.save(job)

        job.status = "review"
        jobs.save(job)
        _log(job, "angel", f"All done! 🎬 {len(job.inserts)} replacement option(s) are ready. Head to the review panel to make your decisions.")
        print(f"[job {job_id[:8]}] done — {len(job.inserts)} insert(s) ready", flush=True)
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        _log(job, "devil", f"Something broke in the pipeline 💀 — {str(e)}")
        jobs.save(job)
        import traceback
        print(f"[job {job_id[:8]}] PIPELINE ERROR: {e}", flush=True)
        traceback.print_exc()


@app.post("/jobs")
def create_job():
    if "video" not in request.files:
        return jsonify({"error": "no video file"}), 400
    f = request.files["video"]
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
            return jsonify({"ok": True})
    abort(404)


@app.post("/jobs/<jid>/apply")
def apply_edits(jid):
    job = jobs.get(jid)
    if not job:
        abort(404)
    job.status = "applying"
    try:
        out = splice.apply_decisions(job)
        job.status = "done"
        return jsonify({"output": out})
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        return jsonify({"error": str(e)}), 500


@app.get("/jobs/<jid>/file/<kind>/<name>")
def serve_file(jid, kind, name):
    base = {
        "anchor": str(config.FRAMES),
        "clip": str(config.CLIPS),
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
