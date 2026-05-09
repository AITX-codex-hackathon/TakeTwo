import React from "react";
import { Download, RotateCcw, CheckCircle, Sparkles } from "lucide-react";
import { fileUrl } from "../api";

export default function Done({ jobId, outputPath, onReset }) {
  const filename = outputPath ? outputPath.split("/").pop() : null;

  return (
    <section className="editor-page">
      <div
        className="workspace-panel"
        style={{
          gridColumn: "1 / -1",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 24,
          padding: 48,
          textAlign: "center",
        }}
      >
        <CheckCircle size={56} style={{ color: "#4ade80" }} />
        <div>
          <h2 style={{ fontSize: 28, fontWeight: 700, color: "#1F2937", marginBottom: 8 }}>
            Your video is ready!
          </h2>
          <p style={{ color: "#6B7280", fontSize: 16 }}>
            All approved edits have been applied.
          </p>
        </div>

        {filename && (
          <video
            src={fileUrl(jobId, "output", filename)}
            controls
            style={{ width: "100%", maxWidth: 800, borderRadius: 16, background: "#1F2937" }}
          />
        )}

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", justifyContent: "center" }}>
          {filename && (
            <a
              href={fileUrl(jobId, "output", filename)}
              download
              className="btn btn-primary"
              style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 8 }}
            >
              <Download size={16} />
              Download Video
            </a>
          )}
          <button
            className="btn"
            style={{ background: "rgba(255,255,255,0.6)", color: "#1F2937", border: "1px solid rgba(255,255,255,0.8)", padding: "12px 24px", borderRadius: 10, fontWeight: 600 }}
            onClick={onReset}
          >
            <RotateCcw size={14} style={{ display: "inline", marginRight: 6 }} />
            Edit Another Video
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#9CA3AF", fontSize: 13 }}>
          <Sparkles size={14} />
          Powered by CLIPCURE AI
        </div>
      </div>
    </section>
  );
}
