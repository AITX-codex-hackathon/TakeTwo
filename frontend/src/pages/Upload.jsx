import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Film,
  Plus,
  Send,
  Sparkles,
  Upload as UploadIcon,
  Zap,
  Clock,
} from "lucide-react";
import { uploadVideo } from "../api";

const ACCEPTED_TYPES = ["video/mp4", "video/quicktime", "video/webm"];

function makeId() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 MB";
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(seconds = 0) {
  const safe = Number.isFinite(seconds) ? seconds : 0;
  const min = Math.floor(safe / 60).toString().padStart(2, "0");
  const sec = Math.floor(safe % 60).toString().padStart(2, "0");
  return `${min}:${sec}`;
}

function getVideoMeta(url) {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.src = url;
    video.onloadedmetadata = () => {
      const duration = Number.isFinite(video.duration) ? video.duration : 12;
      const width = video.videoWidth || 1920;
      const height = video.videoHeight || 1080;
      resolve({ duration, width, height });
    };
    video.onerror = () => resolve({ duration: 12, width: 1920, height: 1080 });
  });
}

function createThumbnail(url) {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.preload = "metadata";
    video.muted = true;
    video.src = url;
    const fallback = window.setTimeout(() => resolve(""), 2500);
    video.onloadeddata = () => {
      video.currentTime = Math.min(0.8, video.duration / 3 || 0);
    };
    video.onseeked = () => {
      window.clearTimeout(fallback);
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 180;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL("image/jpeg", 0.78));
    };
    video.onerror = () => {
      window.clearTimeout(fallback);
      resolve("");
    };
  });
}

export default function Upload({ onUploaded }) {
  const inputRef = useRef(null);
  const centerInputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [clips, setClips] = useState([]);
  const [activeSourceId, setActiveSourceId] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState([
    {
      id: makeId(),
      role: "assistant",
      text: "Hi! I'm your CLIPCURE assistant. Drop a video in the center and hit Analyze — I'll find bad clips and generate cinematic replacements.",
    },
  ]);

  const activeSource = useMemo(
    () => clips.find((c) => c.id === activeSourceId) || clips[0],
    [activeSourceId, clips]
  );

  useEffect(() => {
    if (!activeSourceId && clips.length) setActiveSourceId(clips[0].id);
  }, [activeSourceId, clips]);

  async function addFiles(fileList) {
    const valid = Array.from(fileList || []).filter(
      (f) => ACCEPTED_TYPES.includes(f.type) || /\.(mp4|mov|webm)$/i.test(f.name)
    );
    if (!valid.length) return;

    const staged = [];
    for (const file of valid) {
      const url = URL.createObjectURL(file);
      const [meta, thumbnail] = await Promise.all([getVideoMeta(url), createThumbnail(url)]);
      staged.push({
        id: makeId(),
        name: file.name,
        size: file.size,
        url,
        thumbnail,
        duration: meta.duration,
        dimensions: `${meta.width}x${meta.height}`,
        _file: file,
      });
    }
    setClips((c) => [...staged, ...c]);
  }

  async function handleAnalyze() {
    if (!activeSource || analyzing) return;
    setAnalyzing(true);
    try {
      const resp = await uploadVideo(activeSource._file);
      onUploaded(resp.job_id);
    } catch (e) {
      alert("Upload failed: " + e.message);
      setAnalyzing(false);
    }
  }

  function sendChatMessage(text = chatInput) {
    const clean = text.trim();
    if (!clean) return;
    setChatMessages((m) => [
      ...m,
      { id: makeId(), role: "user", text: clean },
      {
        id: makeId(),
        role: "assistant",
        text: activeSource
          ? `Working with "${activeSource.name}". Hit Analyze to let AI scan it for bad clips and generate cinematic replacements.`
          : "Upload a video first, then I can help.",
      },
    ]);
    setChatInput("");
  }

  return (
    <section className="editor-page">
      {/* Left: source list */}
      <aside className="editor-panel source-panel">
        <div className="panel-heading">
          <div>
            <p>Source Media</p>
            <h2>Your clips</h2>
          </div>
          <button className="icon-button" onClick={() => inputRef.current?.click()} aria-label="Add clip">
            <Plus size={18} />
          </button>
        </div>
        <input ref={inputRef} type="file" accept="video/*" multiple
          onChange={(e) => addFiles(e.target.files)}
          onClick={(e) => { e.currentTarget.value = ""; }}
        />

        <div className="source-list">
          {clips.length === 0 ? (
            <div className="empty-source">
              <Film size={28} />
              <p>No clips yet.<br />Drop a video in the center.</p>
            </div>
          ) : (
            clips.map((clip) => (
              <article
                key={clip.id}
                className={`source-card ${activeSource?.id === clip.id ? "active" : ""}`}
                onClick={() => setActiveSourceId(clip.id)}
              >
                <img src={clip.thumbnail} alt="" />
                <div className="source-meta">
                  <strong>{clip.name}</strong>
                  <span>{formatTime(clip.duration)} · {formatBytes(clip.size)}</span>
                  <small>{clip.dimensions}</small>
                </div>
              </article>
            ))
          )}
        </div>
      </aside>

      {/* Center: upload zone OR file ready + analyze button */}
      <main className="workspace-panel">
        <input ref={centerInputRef} type="file" accept="video/*"
          style={{ display: "none" }}
          onChange={(e) => addFiles(e.target.files)}
          onClick={(e) => { e.currentTarget.value = ""; }}
        />

        {!activeSource ? (
          /* ── No file yet: big obvious drop target ── */
          <div
            className={`upload-center-zone ${dragging ? "is-dragging" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
            onClick={() => centerInputRef.current?.click()}
          >
            <div className="upload-center-icon">
              <UploadIcon size={40} />
            </div>
            <h2 className="upload-center-title">Drop your video here</h2>
            <p className="upload-center-sub">or click to browse — MP4, MOV, WEBM</p>
            <p className="upload-center-hint">
              <Clock size={13} style={{ display: "inline", marginRight: 5, verticalAlign: "middle" }} />
              AI analysis takes ~2–3 minutes per clip
            </p>
          </div>
        ) : (
          /* ── File ready: preview + ANALYZE button ── */
          <div
            className={`analyze-ready-zone ${dragging ? "is-dragging" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
          >
            <div className="analyze-preview">
              <video
                src={activeSource.url}
                poster={activeSource.thumbnail}
                controls
                muted
              />
            </div>

            <div className="analyze-info">
              <div className="analyze-filename">{activeSource.name}</div>
              <div className="analyze-meta">
                {formatTime(activeSource.duration)} · {formatBytes(activeSource.size)} · {activeSource.dimensions}
              </div>
            </div>

            <button
              className="analyze-cta"
              disabled={analyzing}
              onClick={handleAnalyze}
            >
              {analyzing ? (
                <>
                  <span className="analyze-spinner" />
                  Uploading & analyzing…
                </>
              ) : (
                <>
                  <Zap size={22} />
                  Analyze with AI
                </>
              )}
            </button>

            <p className="analyze-time-hint">
              <Clock size={13} style={{ display: "inline", marginRight: 5, verticalAlign: "middle" }} />
              Scanning + generation takes ~2–3 min per bad clip
            </p>
          </div>
        )}
      </main>

      {/* Right: chat */}
      <aside className="editor-panel chat-panel">
        <div className="panel-heading">
          <div>
            <p>AI Assistant</p>
            <h2>Chat with CLIPCURE</h2>
          </div>
          <Bot size={22} />
        </div>

        <div className="chat-thread">
          {chatMessages.map((msg) => (
            <div className={`chat-message ${msg.role}`} key={msg.id}>
              {msg.role === "assistant" && <Sparkles size={15} />}
              <p>{msg.text}</p>
            </div>
          ))}
        </div>

        <div className="chat-prompts">
          {["Improve pacing", "Write captions", "Find hook moments"].map((p) => (
            <button key={p} onClick={() => sendChatMessage(p)}>{p}</button>
          ))}
        </div>

        <form className="chat-composer" onSubmit={(e) => { e.preventDefault(); sendChatMessage(); }}>
          <input
            type="text"
            value={chatInput}
            placeholder="Ask for an edit…"
            onChange={(e) => setChatInput(e.target.value)}
          />
          <button type="submit" aria-label="Send">
            <Send size={17} />
          </button>
        </form>
      </aside>
    </section>
  );
}
