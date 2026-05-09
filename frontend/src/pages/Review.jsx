import React, { useEffect, useState, useCallback } from "react";
import { AlertTriangle, CheckCircle, XCircle, Scissors, Sparkles } from "lucide-react";
import { getJob, updateInsert, applyEdits, fileUrl } from "../api";
import SlotCard from "../components/SlotCard";

const POLL_MS = 2000;

export default function Review({ jobId, onDone }) {
  const [job, setJob] = useState(null);
  const [applying, setApplying] = useState(false);
  const [notFound, setNotFound] = useState(false);

  const poll = useCallback(() => {
    getJob(jobId)
      .then(setJob)
      .catch((e) => {
        if (e.status === 404 || (e.message && e.message.includes("404"))) {
          setNotFound(true);
        } else {
          console.error(e);
        }
      });
  }, [jobId]);

  useEffect(() => {
    if (notFound) return;
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll, notFound]);

  if (notFound) {
    return (
      <div className="empty-state">
        <AlertTriangle size={40} style={{ color: "#f87171", marginBottom: 12 }} />
        <h3>Session expired</h3>
        <p>This job no longer exists — the server may have restarted. Please re-upload your video.</p>
      </div>
    );
  }

  if (!job) return null;

  const isProcessing = ["queued", "detecting", "analyzing", "generating"].includes(job.status);
  const isReview = job.status === "review";
  const isDone = job.status === "done";
  const isError = job.status === "error";

  const statusLabels = {
    queued: "Queued...",
    detecting: "Scanning video for bad clips...",
    analyzing: "Analyzing problem clips with AI...",
    generating: "Generating replacement clips...",
    review: "Ready for your review",
    applying: "Applying your edits...",
    done: "Complete!",
    error: "Something went wrong",
  };

  const slotMap = {};
  for (const s of job.slots || []) slotMap[s.id] = s;

  const slotInserts = {};
  for (const ins of job.inserts || []) {
    if (!slotInserts[ins.slot_id]) slotInserts[ins.slot_id] = [];
    slotInserts[ins.slot_id].push(ins);
  }

  const decided = (job.inserts || []).filter(
    (i) => i.status === "approved" || i.status === "cut"
  ).length;
  const totalSlots = (job.slots || []).length;
  const slotsWithDecision = new Set(
    (job.inserts || [])
      .filter((i) => i.status === "approved" || i.status === "cut")
      .map((i) => i.slot_id)
  ).size;

  async function handleDecision(insertId, status) {
    await updateInsert(jobId, insertId, status);
    poll();
  }

  async function handleApply() {
    setApplying(true);
    try {
      const res = await applyEdits(jobId);
      onDone(res.output);
    } catch (e) {
      alert("Apply failed: " + e.message);
      setApplying(false);
    }
  }

  return (
    <div>
      <div className="status-bar">
        <div
          className={`status-dot ${isDone ? "done" : ""} ${isError ? "error" : ""}`}
        />
        <span className="status-text">{statusLabels[job.status] || job.status}</span>
        {isError && (
          <span style={{ color: "#f87171", fontSize: 13, marginLeft: 8 }}>
            {job.error}
          </span>
        )}
      </div>

      {isProcessing && (
        <div className="empty-state">
          <Sparkles size={40} style={{ color: "#818cf8", marginBottom: 12 }} />
          <h3>AI is working on your video...</h3>
          <p>This may take a minute depending on the video length.</p>
        </div>
      )}

      {isReview && totalSlots === 0 && (
        <div className="empty-state">
          <CheckCircle size={40} style={{ color: "#4ade80", marginBottom: 12 }} />
          <h3>Your video looks great!</h3>
          <p>No bad clips were detected. No editing needed.</p>
        </div>
      )}

      {isReview && totalSlots > 0 && (
        <>
          <div className="slots-grid">
            {(job.slots || []).map((slot, idx) => (
              <SlotCard
                key={slot.id}
                index={idx + 1}
                slot={slot}
                inserts={slotInserts[slot.id] || []}
                jobId={jobId}
                onDecision={handleDecision}
              />
            ))}
          </div>

          <div className="apply-bar">
            <div className="summary">
              <span>{slotsWithDecision}</span> of <span>{totalSlots}</span> clips
              reviewed
            </div>
            <button
              className="btn btn-primary"
              disabled={slotsWithDecision === 0 || applying}
              onClick={handleApply}
            >
              {applying ? "Applying..." : "Apply Edits & Export"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
