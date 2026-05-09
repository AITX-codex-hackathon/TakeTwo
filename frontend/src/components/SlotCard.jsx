import React, { useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  XCircle,
  Scissors,
  Play,
} from "lucide-react";
import { fileUrl } from "../api";

export default function SlotCard({ index, slot, inserts, jobId, onDecision }) {
  const [selectedId, setSelectedId] = useState(null);

  const startSec = (slot.start_frame / slot.fps).toFixed(1);
  const endSec = (slot.end_frame / slot.fps).toFixed(1);
  const qualityPct = Math.round(slot.quality_score * 100);

  const anchorFilename = slot.anchor_frame_path.split("/").pop();
  const originalClipFilename = `${slot.id}_original.mp4`;

  const hasDecision = inserts.some(
    (i) => i.status === "approved" || i.status === "cut"
  );

  const selectedInsert = inserts.find((i) => i.id === selectedId);

  function handleApprove() {
    if (!selectedId) return;
    onDecision(selectedId, "approved");
  }

  function handleCut() {
    const cutInsert = inserts.find((i) => i.label?.includes("cutting"));
    if (cutInsert) {
      onDecision(cutInsert.id, "cut");
    } else if (inserts.length > 0) {
      onDecision(inserts[0].id, "cut");
    }
  }

  function handleReject() {
    inserts.forEach((i) => {
      if (i.status !== "applied") onDecision(i.id, "rejected");
    });
  }

  return (
    <div
      className="slot-card"
      style={{
        opacity: hasDecision ? 0.6 : 1,
        borderColor: hasDecision ? "#4ade8033" : undefined,
      }}
    >
      <div className="slot-header">
        <h3>
          Clip #{index} — {startSec}s to {endSec}s
          <span
            style={{ fontWeight: 400, color: "#6b6b80", fontSize: 13, marginLeft: 8 }}
          >
            Quality: {qualityPct}%
          </span>
        </h3>
        <div className="issue-badges">
          {(slot.issues || []).map((issue) => (
            <span key={issue} className="issue-badge">
              {issue}
            </span>
          ))}
        </div>
      </div>

      <div className="slot-body">
        {/* Side-by-side: original vs selected replacement */}
        <div className="comparison">
          <div className="comparison-panel original">
            <div className="panel-label bad">
              <AlertTriangle size={14} />
              Original (Bad Clip)
            </div>
            <video
              src={fileUrl(jobId, "clip", originalClipFilename)}
              controls
              muted
              loop
              playsInline
            />
          </div>

          <div className="comparison-panel replacement">
            <div className="panel-label good">
              <Sparkles size={14} />
              {selectedInsert ? "AI Replacement" : "Select a replacement below"}
            </div>
            {selectedInsert ? (
              <video
                src={fileUrl(
                  jobId,
                  "clip",
                  selectedInsert.clip_path.split("/").pop()
                )}
                controls
                muted
                loop
                playsInline
              />
            ) : (
              <img
                src={fileUrl(jobId, "anchor", anchorFilename)}
                alt="anchor"
                style={{ filter: "brightness(0.3)" }}
              />
            )}
          </div>
        </div>

        {/* Replacement options */}
        <div className="insert-options">
          {inserts
            .filter((i) => i.clip_path)
            .map((ins) => (
              <div
                key={ins.id}
                className={`insert-option ${selectedId === ins.id ? "selected" : ""}`}
                onClick={() => setSelectedId(ins.id)}
                style={{ cursor: "pointer" }}
              >
                <video
                  src={fileUrl(jobId, "clip", ins.clip_path.split("/").pop())}
                  muted
                  playsInline
                  onMouseOver={(e) => e.target.play()}
                  onMouseOut={(e) => {
                    e.target.pause();
                    e.target.currentTime = 0;
                  }}
                />
                <div className="insert-meta">
                  <div className="label">{ins.label}</div>
                  <div
                    className={`critic ${ins.critic_pass ? "critic-pass" : "critic-fail"}`}
                  >
                    {ins.critic_pass ? (
                      <>
                        <CheckCircle
                          size={12}
                          style={{ display: "inline", marginRight: 4 }}
                        />
                        Critic approved
                      </>
                    ) : (
                      <>
                        <XCircle
                          size={12}
                          style={{ display: "inline", marginRight: 4 }}
                        />
                        Critic flagged
                      </>
                    )}
                    {ins.critic_notes && ` — ${ins.critic_notes}`}
                  </div>
                </div>
              </div>
            ))}
        </div>

        {/* Actions */}
        {!hasDecision && (
          <div className="slot-actions">
            <button className="btn btn-reject" onClick={handleReject}>
              <XCircle size={14} style={{ display: "inline", marginRight: 4 }} />
              Keep Original
            </button>
            <button className="btn btn-cut" onClick={handleCut}>
              <Scissors size={14} style={{ display: "inline", marginRight: 4 }} />
              Cut Clip
            </button>
            <button
              className="btn btn-approve"
              disabled={!selectedId}
              onClick={handleApprove}
            >
              <CheckCircle size={14} style={{ display: "inline", marginRight: 4 }} />
              Use Replacement
            </button>
          </div>
        )}

        {hasDecision && (
          <div style={{ textAlign: "right", padding: "8px 0", color: "#4ade80", fontSize: 14 }}>
            <CheckCircle size={14} style={{ display: "inline", marginRight: 4 }} />
            Decision recorded
          </div>
        )}
      </div>
    </div>
  );
}
