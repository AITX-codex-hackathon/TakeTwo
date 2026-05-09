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


def _process(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return
    try:
        job.status = "detecting"
        job.slots = detect.find_bad_clips(job.source_path)

        if not job.slots:
            job.status = "review"
            return

        job.status = "analyzing"
        slot_contexts = {}
        for slot in job.slots:
            ctx = analyze.analyze_anchor(slot.anchor_frame_path, slot.issues)
            slot_contexts[slot.id] = ctx

        job.status = "generating"
        for slot in job.slots:
            ctx = slot_contexts[slot.id]
            if ctx.recommendation == "cut":
                cut_insert = new_id()
                from backend.models.schemas import Insert
                job.inserts.append(Insert(
                    id=cut_insert,
                    slot_id=slot.id,
                    clip_path="",
                    prompt="",
                    label="AI recommends cutting this clip entirely",
                    status="pending",
                ))
                continue

            inserts = generate.generate_for_slot(slot, ctx)
            for ins in inserts:
                try:
                    critic.review(ins, slot.anchor_frame_path, slot.issues)
                except Exception as e:
                    ins.critic_notes = f"critic error: {e}"
                job.inserts.append(ins)

        job.status = "review"
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        import traceback
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
    app.run(host="0.0.0.0", port=5050, debug=True)
