import React from "react";
import { Download, RotateCcw, CheckCircle } from "lucide-react";
import { fileUrl } from "../api";

export default function Done({ jobId, outputPath, onReset }) {
  const filename = outputPath ? outputPath.split("/").pop() : null;

  return (
    <div className="done-card">
      <CheckCircle size={48} style={{ color: "#4ade80", marginBottom: 16 }} />
      <h2>Your video is ready!</h2>
      <p>All approved edits have been applied. Preview below.</p>

      {filename && (
        <video
          src={fileUrl(jobId, "output", filename)}
          controls
          style={{ width: "100%", maxWidth: 800, borderRadius: 12, marginBottom: 24 }}
        />
      )}

      <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
        {filename && (
          <a
            href={fileUrl(jobId, "output", filename)}
            download
            className="btn btn-primary"
            style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            <Download size={16} />
            Download Video
          </a>
        )}
        <button
          className="btn"
          style={{ background: "#1e1e2e", color: "#a0a0b8" }}
          onClick={onReset}
        >
          <RotateCcw size={14} style={{ display: "inline", marginRight: 4 }} />
          Edit Another Video
        </button>
      </div>
    </div>
  );
}
