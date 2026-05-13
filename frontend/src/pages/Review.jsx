import React, { useEffect, useRef, useState, useCallback } from "react";
import {
  AlertTriangle,
  CheckCircle,
  XCircle,
  Scissors,
  Sparkles,
  Bot,
  Clock,
  RotateCcw,
} from "lucide-react";
import { getJob, retryJob, updateInsert, applyEdits, fileUrl } from "../api";

const POLL_MS = 2000;

const STATUS_LABELS = {
  queued: "Queued…",
  detecting: "Scanning video for bad clips…",
  analyzing: "Analyzing problem clips with AI…",
  generating: "Generating replacement clips…",
  review: "Ready for your review",
  applying: "Applying your edits…",
  done: "Complete!",
  error: "Something went wrong",
};

export default function Review({ jobId, onDone, onReset }) {
  const [job, setJob] = useState(null);
  const [applying, setApplying] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [pollError, setPollError] = useState("");
  const [activeSlotId, setActiveSlotId] = useState(null);
  const [selectedInsertId, setSelectedInsertId] = useState(null);
  const convoBottomRef = useRef(null);

  const poll = useCallback(() => {
    getJob(jobId)
      .then((nextJob) => {
        setPollError("");
        setJob(nextJob);
      })
      .catch((e) => {
        if (e.status === 404 || (e.message && e.message.includes("404"))) {
          setNotFound(true);
        } else if (e.status >= 500) {
          setPollError("Connection hiccup while reading job status. Retrying...");
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

  // Auto-select first slot when job enters review
  useEffect(() => {
    if (job?.status === "review" && job.slots?.length && !activeSlotId) {
      setActiveSlotId(job.slots[0].id);
    }
  }, [job, activeSlotId]);

  // Reset selected insert when active slot changes
  useEffect(() => {
    setSelectedInsertId(null);
  }, [activeSlotId]);

  // Auto-scroll conversation to bottom on new log entries
  useEffect(() => {
    convoBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [job?.logs?.length]);

  if (notFound) {
    return (
      <section className="editor-page">
        <div className="workspace-panel" style={{ gridColumn: "1 / -1", display: "grid", placeItems: "center" }}>
          <div className="preview-empty" style={{ color: "#1F2937" }}>
            <AlertTriangle size={40} style={{ color: "#f87171" }} />
            <h2>Session expired</h2>
            <p>This job no longer exists — the server may have restarted. Please re-upload your video.</p>
            <button className="btn btn-primary" onClick={onReset} style={{ marginTop: 16 }}>Re-upload Video</button>
          </div>
        </div>
      </section>
    );
  }

  if (!job) return null;

  const isProcessing = ["queued", "detecting", "analyzing", "generating"].includes(job.status);
  const isReview = job.status === "review";
  const isError = job.status === "error";

  const slots = job.slots || [];
  const slotInserts = {};
  for (const ins of job.inserts || []) {
    if (!slotInserts[ins.slot_id]) slotInserts[ins.slot_id] = [];
    slotInserts[ins.slot_id].push(ins);
  }

  const slotsWithDecision = new Set(
    (job.inserts || [])
      .filter((i) => i.status === "approved" || i.status === "cut")
      .map((i) => i.slot_id)
  ).size;

  const activeSlot = slots.find((s) => s.id === activeSlotId);
  const activeInserts = activeSlot ? (slotInserts[activeSlot.id] || []) : [];
  const generatedInserts = activeInserts.filter((i) => i.clip_path);
  const selectedInsert = activeInserts.find((i) => i.id === selectedInsertId);
  const slotHasDecision = activeSlot
    ? activeInserts.some((i) => i.status === "approved" || i.status === "cut")
    : false;

  const originalClipFilename = activeSlot ? `${activeSlot.id}_original.mp4` : null;
  const anchorFilename = activeSlot ? activeSlot.anchor_frame_path.split("/").pop() : null;

  async function handleDecision(insertId, status) {
    await updateInsert(jobId, insertId, status);
    poll();
  }

  async function handleApprove() {
    if (!selectedInsertId) return;
    await handleDecision(selectedInsertId, "approved");
  }

  async function handleCut() {
    const cutInsert = activeInserts.find((i) => i.label?.includes("cutting"));
    const target = cutInsert || activeInserts[0];
    if (target) await handleDecision(target.id, "cut");
  }

  async function handleKeepOriginal() {
    for (const i of activeInserts) {
      if (i.status !== "applied") await updateInsert(jobId, i.id, "rejected");
    }
    poll();
  }

  async function handleApplyAll() {
    setApplying(true);
    try {
      const res = await applyEdits(jobId);
      onDone(res.output);
    } catch (e) {
      alert("Apply failed: " + e.message);
      setApplying(false);
    }
  }

  async function handleRetry() {
    setRetrying(true);
    try {
      const nextJob = await retryJob(jobId);
      setJob(nextJob);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <section className="editor-page">
      {/* Left: Slot list */}
      <aside className="editor-panel source-panel">
        <div className="panel-heading">
          <div>
            <p>Pipeline</p>
            <h2>{STATUS_LABELS[job.status] || job.status}</h2>
          </div>
          <div
            className="status-dot"
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: isError ? "#f87171" : isReview ? "#4ade80" : "#818cf8",
              animation: isProcessing ? "pulse 1.5s infinite" : "none",
              flexShrink: 0,
            }}
          />
        </div>

        {isError && (
          <>
            <div className="error-message">{job.error}</div>
            <button
              className="btn btn-primary"
              onClick={handleRetry}
              disabled={retrying}
              style={{ marginTop: 12 }}
            >
              <RotateCcw size={15} />
              {retrying ? "Retrying..." : "Retry job"}
            </button>
          </>
        )}

        {pollError && !isError && (
          <div className="error-message" style={{ marginTop: 12 }}>{pollError}</div>
        )}

        {isProcessing && (
          <div className="empty-source" style={{ minHeight: 200 }}>
            <div className="agent-pulse">
              <span>✨</span>
              <span>😈</span>
            </div>
            <p style={{ fontSize: 12, color: "#6B7280", marginTop: 8 }}>~2–3 min per clip</p>
          </div>
        )}

        {isReview && slots.length === 0 && (
          <div className="empty-source" style={{ minHeight: 200 }}>
            <CheckCircle size={32} style={{ color: "#4ade80" }} />
            <p>No bad clips found!</p>
            <button className="btn btn-primary" onClick={onReset} style={{ marginTop: 12, padding: "8px 16px", fontSize: 14 }}>
              Re-upload Video
            </button>
          </div>
        )}

        {isReview && slots.length > 0 && (
          <div className="source-list">
            {slots.map((slot, idx) => {
              const inserts = slotInserts[slot.id] || [];
              const decided = inserts.some((i) => i.status === "approved" || i.status === "cut");
              const startSec = (slot.start_frame / slot.fps).toFixed(1);
              const resumeFrame = slot.replace_end_frame !== -1 ? slot.replace_end_frame : slot.end_frame;
              const endSec = (resumeFrame / slot.fps).toFixed(1);
              return (
                <article
                  key={slot.id}
                  className={`source-card ${activeSlotId === slot.id ? "active" : ""}`}
                  onClick={() => setActiveSlotId(slot.id)}
                  style={{ opacity: decided ? 0.6 : 1 }}
                >
                  <div style={{
                    width: 74,
                    aspectRatio: "16/9",
                    borderRadius: 10,
                    background: "rgba(255, 255, 255, 0.6)",
                    display: "grid",
                    placeItems: "center",
                    color: "#6B7280",
                    fontSize: 13,
                    fontWeight: 700,
                  }}>
                    #{idx + 1}
                  </div>
                  <div className="source-meta">
                    <strong>Clip #{idx + 1}</strong>
                    <span>{startSec}s – {endSec}s</span>
                    <small style={{ color: decided ? "#4ade80" : "#9CA3AF" }}>
                      {decided ? "✓ Decided" : `${inserts.filter(i => i.clip_path).length} options`}
                    </small>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {isReview && slots.length > 0 && (
          <button
            className="btn btn-primary"
            disabled={slotsWithDecision === 0 || applying}
            onClick={handleApplyAll}
            style={{ marginTop: "auto" }}
          >
            {applying ? "Applying…" : `Export (${slotsWithDecision}/${slots.length} reviewed)`}
          </button>
        )}
      </aside>

      {/* Center: conversation during processing OR comparison during review */}
      <main className="workspace-panel">
        <div className="workspace-header">
          <div>
            <p>{isProcessing ? "AI Pipeline" : "Review workspace"}</p>
            <h2>{isProcessing ? "Agents at work…" : "Original vs AI Replacement"}</h2>
          </div>
          {activeSlot && !isProcessing && (
            <span>
              {(activeSlot.start_frame / activeSlot.fps).toFixed(1)}s –{" "}
              {((activeSlot.replace_end_frame !== -1 ? activeSlot.replace_end_frame : activeSlot.end_frame) / activeSlot.fps).toFixed(1)}s
            </span>
          )}
        </div>

        {isProcessing ? (
          <div className="agent-convo-stage">
            <div className="agent-convo-header">
              <div className="agent-badge angel-badge">
                <span>✨</span> The Analyzer
              </div>
              <div className="agent-badge devil-badge">
                <span>😈</span> The Critic
              </div>
            </div>
            <div className="agent-convo-thread">
              {(job.logs || []).length === 0 && (
                <div className="agent-msg angel-msg typing">
                  <span className="agent-avatar">✨</span>
                  <div className="agent-bubble">
                    <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
                  </div>
                </div>
              )}
              {(job.logs || []).map((entry, idx) => (
                <div
                  key={idx}
                  className={`agent-msg ${entry.agent === "devil" ? "devil-msg" : "angel-msg"}`}
                >
                  <span className="agent-avatar">{entry.agent === "devil" ? "😈" : "✨"}</span>
                  <div className="agent-bubble">{entry.msg}</div>
                </div>
              ))}
              <div ref={convoBottomRef} />
              {/* typing indicator on the last active agent */}
              {(job.logs || []).length > 0 && (
                <div className={`agent-msg ${(job.logs[job.logs.length - 1].agent === "devil") ? "angel-msg" : "devil-msg"} typing`}>
                  <span className="agent-avatar">{(job.logs[job.logs.length - 1].agent === "devil") ? "✨" : "😈"}</span>
                  <div className="agent-bubble">
                    <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
                  </div>
                </div>
              )}
            </div>
            <div className="agent-time-note">
              <Clock size={13} />
              Generation takes ~2–3 min per clip — grab a coffee ☕
            </div>
          </div>
        ) : (
          <div className="preview-stage">
            <div className="compare-pane original-pane">
              <div className="pane-label">
                <AlertTriangle size={13} style={{ display: "inline", marginRight: 5 }} />
                Original (Bad Clip)
              </div>
              {activeSlot && originalClipFilename ? (
                <video
                  key={originalClipFilename}
                  src={fileUrl(jobId, "clip", originalClipFilename)}
                  controls muted loop playsInline
                />
              ) : (
                <div className="preview-empty">
                  <AlertTriangle size={36} />
                  <h2>Select a clip</h2>
                  <p>Pick a clip from the left panel to start reviewing.</p>
                </div>
              )}
            </div>

            <div className="compare-pane ai-pane">
              <div className="pane-label">
                <Sparkles size={13} style={{ display: "inline", marginRight: 5 }} />
                {selectedInsert ? "AI Replacement" : "Select a replacement"}
              </div>
              {selectedInsert ? (
                <video
                  key={selectedInsert.id}
                  src={fileUrl(jobId, "clip", selectedInsert.clip_path.split("/").pop())}
                  controls muted loop playsInline
                />
              ) : (
                <div className="preview-empty">
                  <Sparkles size={36} />
                  <h2>{activeSlot ? "AI Replacement" : "AI preview"}</h2>
                  <p>{activeSlot ? "Choose a replacement from the right panel." : "Generated replacements will appear here."}</p>
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      {/* Right: options + actions */}
      <aside className="editor-panel chat-panel">
        <div className="panel-heading">
          <div>
            <p>AI Options</p>
            <h2>{activeSlot ? `Clip #${slots.indexOf(activeSlot) + 1} actions` : "Select a clip"}</h2>
          </div>
          <Bot size={22} style={{ color: "#2563A8" }} />
        </div>

        <div className="chat-thread" style={{ flex: "unset", minHeight: "unset" }}>
          {activeSlot ? (
            <>
              <div style={{ fontSize: 13, color: "#6B7280", marginBottom: 4 }}>Issues detected:</div>
              {(activeSlot.issues || []).map((issue) => (
                <div key={issue} className="chat-message assistant" style={{ padding: "8px 12px" }}>
                  <p>{issue}</p>
                </div>
              ))}
              {(!activeSlot.issues || activeSlot.issues.length === 0) && (
                <div className="chat-message assistant" style={{ padding: "8px 12px" }}>
                  <p>No specific issues tagged.</p>
                </div>
              )}
            </>
          ) : (
            <div className="chat-message assistant">
              <Sparkles size={15} />
              <p>Select a clip from the left panel to see AI analysis and replacement options.</p>
            </div>
          )}
        </div>

        {generatedInserts.length > 0 && (
          <>
            <div style={{ fontSize: 13, color: "#6B7280", fontWeight: 600 }}>Replacement options:</div>
            <div className="source-list" style={{ maxHeight: 280 }}>
              {generatedInserts.map((ins) => (
                <article
                  key={ins.id}
                  className={`source-card ${selectedInsertId === ins.id ? "active" : ""}`}
                  onClick={() => setSelectedInsertId(ins.id)}
                >
                  <video
                    src={fileUrl(jobId, "clip", ins.clip_path.split("/").pop())}
                    muted
                    playsInline
                    style={{ width: 74, aspectRatio: "16/9", borderRadius: 10, objectFit: "cover", background: "rgba(255, 255, 255, 0.6)", flexShrink: 0 }}
                    onMouseOver={(e) => e.target.play()}
                    onMouseOut={(e) => { e.target.pause(); e.target.currentTime = 0; }}
                  />
                  <div className="source-meta">
                    <strong>{ins.label}</strong>
                    <small style={{ color: ins.critic_pass ? "#4ade80" : "#f87171" }}>
                      {ins.critic_pass ? "✓ Critic approved" : "✗ Critic flagged"}
                      {ins.critic_notes ? ` — ${ins.critic_notes}` : ""}
                    </small>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}

        {activeSlot && !slotHasDecision && (
          <div className="chat-prompts">
            <button
              onClick={handleKeepOriginal}
              style={{ color: "#6B7280" }}
            >
              <XCircle size={14} style={{ display: "inline", marginRight: 6 }} />
              Keep original
            </button>
            <button onClick={handleCut} style={{ color: "#f87171" }}>
              <Scissors size={14} style={{ display: "inline", marginRight: 6 }} />
              Cut this clip
            </button>
            <button
              onClick={handleApprove}
              disabled={!selectedInsertId}
              style={{ color: selectedInsertId ? "#4ade80" : "#9CA3AF" }}
            >
              <CheckCircle size={14} style={{ display: "inline", marginRight: 6 }} />
              Use replacement
            </button>
          </div>
        )}

        {activeSlot && slotHasDecision && (
          <div style={{ textAlign: "center", padding: "12px", color: "#4ade80", fontSize: 14, fontWeight: 600 }}>
            <CheckCircle size={14} style={{ display: "inline", marginRight: 6 }} />
            Decision recorded
          </div>
        )}
      </aside>
    </section>
  );
}
