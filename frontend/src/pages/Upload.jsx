import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Film,
  Maximize2,
  Plus,
  Send,
  Sparkles,
  Upload as UploadIcon,
} from "lucide-react";

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

export default function Upload() {
  const inputRef = useRef(null);
  const previewRef = useRef(null);
  const originalPaneRef = useRef(null);
  const aiPaneRef = useRef(null);
  const [draggingUpload, setDraggingUpload] = useState(false);
  const [clips, setClips] = useState([]);
  const [activeSourceId, setActiveSourceId] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState([
    {
      id: makeId(),
      role: "assistant",
      text: "Hi, I am your CLIPCURE assistant. Ask me to improve pacing, captions, audio, hooks, or social cutdowns.",
    },
  ]);

  const activeSource = useMemo(
    () => clips.find((clip) => clip.id === activeSourceId) || clips[0],
    [activeSourceId, clips]
  );

  useEffect(() => {
    if (!activeSourceId && clips.length) setActiveSourceId(clips[0].id);
  }, [activeSourceId, clips]);

  async function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    const valid = incoming.filter(
      (file) => ACCEPTED_TYPES.includes(file.type) || /\.(mp4|mov|webm)$/i.test(file.name)
    );

    if (!valid.length) {
      return;
    }

    setUploadProgress(5);

    const staged = [];
    for (const file of valid) {
      const url = URL.createObjectURL(file);
      const [meta, thumbnail] = await Promise.all([getVideoMeta(url), createThumbnail(url)]);
      staged.push({
        id: makeId(),
        name: file.name,
        size: file.size,
        type: file.type || "video",
        url,
        thumbnail,
        duration: meta.duration,
        dimensions: `${meta.width}x${meta.height}`,
      });
      setUploadProgress(Math.min(92, 20 + staged.length * 22));
    }

    setClips((current) => [...staged, ...current]);
    setUploadProgress(100);

    window.setTimeout(() => setUploadProgress(0), 900);
  }

  function handleDropUpload(event) {
    event.preventDefault();
    setDraggingUpload(false);
    addFiles(event.dataTransfer.files);
  }

  function sendChatMessage(text = chatInput) {
    const clean = text.trim();
    if (!clean) return;
    const userMessage = { id: makeId(), role: "user", text: clean };
    const assistantMessage = {
      id: makeId(),
      role: "assistant",
      text: activeSource
        ? `I can work with "${activeSource.name}". I would start by tightening dead air, balancing voice levels, and preparing a cleaner AI preview.`
        : "Upload or select a source clip first, then I can suggest edits against the actual footage.",
    };
    setChatMessages((current) => [...current, userMessage, assistantMessage]);
    setChatInput("");
  }

  function openFullscreen(targetRef) {
    const element = targetRef.current;
    if (!element) return;
    if (element.requestFullscreen) {
      element.requestFullscreen();
    } else if (element.webkitRequestFullscreen) {
      element.webkitRequestFullscreen();
    }
  }

  return (
    <section className="editor-page">
      <aside className="editor-panel source-panel">
        <div className="panel-heading">
          <div>
            <p>Source Clips</p>
            <h2>Project media</h2>
          </div>
          <button className="icon-button" onClick={() => inputRef.current?.click()} aria-label="Add clip">
            <Plus size={18} />
          </button>
        </div>

        <div
          className={`dropzone ${draggingUpload ? "is-dragging" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDraggingUpload(true);
          }}
          onDragLeave={() => setDraggingUpload(false)}
          onDrop={handleDropUpload}
          onClick={() => inputRef.current?.click()}
        >
          <UploadIcon size={24} />
          <strong>Upload footage</strong>
          <span>MP4, MOV, WEBM</span>
          {uploadProgress > 0 && (
            <div className="upload-progress" aria-label="Upload progress">
              <span style={{ width: `${uploadProgress}%` }} />
            </div>
          )}
        </div>

        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          multiple
          onChange={(event) => addFiles(event.target.files)}
          onClick={(event) => {
            event.currentTarget.value = "";
          }}
        />

        <div className="source-list">
          {clips.length === 0 && (
            <div className="empty-source">
              <Film size={28} />
              <p>No clips yet.</p>
            </div>
          )}
          {clips.map((clip) => (
            <article
              key={clip.id}
              className={`source-card ${activeSource?.id === clip.id ? "active" : ""}`}
              draggable
              onDragStart={(event) => event.dataTransfer.setData("clip-id", clip.id)}
              onClick={() => setActiveSourceId(clip.id)}
            >
              <img src={clip.thumbnail} alt="" />
              <div className="source-meta">
                <strong>{clip.name}</strong>
                <span>{formatTime(clip.duration)} / {formatBytes(clip.size)}</span>
                <small>{clip.dimensions}</small>
              </div>
              <button
                className="mini-button"
                onClick={(event) => {
                  event.stopPropagation();
                  setActiveSourceId(clip.id);
                }}
              >
                Add
              </button>
            </article>
          ))}
        </div>
      </aside>

      <main className="workspace-panel">
        <div className="workspace-header">
          <div>
            <p>Review workspace</p>
            <h2>Original vs AI preview</h2>
          </div>
          <span>{activeSource ? activeSource.name : "No clip selected"}</span>
        </div>

        <div className="preview-stage">
          <div className="compare-pane original-pane" ref={originalPaneRef}>
            <div className="pane-label">Original upload</div>
            <button
              className="pane-fullscreen"
              type="button"
              aria-label="Fullscreen original upload"
              onClick={() => openFullscreen(originalPaneRef)}
            >
              <Maximize2 size={17} />
            </button>
            {activeSource ? (
              <video
                ref={previewRef}
                src={activeSource.url}
                poster={activeSource.thumbnail}
                controls
              />
            ) : (
              <div className="preview-empty">
                <Film size={42} />
                <h2>Uploaded files</h2>
                <p>Select a source clip to preview the original footage.</p>
              </div>
            )}
          </div>

          <div className="compare-pane ai-pane" ref={aiPaneRef}>
            <div className="pane-label">AI preview</div>
            <button
              className="pane-fullscreen"
              type="button"
              aria-label="Fullscreen AI preview"
              onClick={() => openFullscreen(aiPaneRef)}
            >
              <Maximize2 size={17} />
            </button>
            {activeSource ? (
              <video src={activeSource.url} poster={activeSource.thumbnail} controls muted />
            ) : (
              <div className="preview-empty">
                <Sparkles size={42} />
                <h2>AI preview</h2>
                <p>Generated edits will appear here after a clip is selected.</p>
              </div>
            )}
          </div>
        </div>

      </main>

      <aside className="editor-panel chat-panel">
        <div className="panel-heading">
          <div>
            <p>AI Assistant</p>
            <h2>Chat with CLIPCURE</h2>
          </div>
          <Bot size={22} />
        </div>

        <div className="chat-thread">
          {chatMessages.map((message) => (
            <div className={`chat-message ${message.role}`} key={message.id}>
              {message.role === "assistant" && <Sparkles size={15} />}
              <p>{message.text}</p>
            </div>
          ))}
        </div>

        <div className="chat-prompts">
          {["Improve pacing", "Write captions", "Find hook moments"].map((prompt) => (
            <button key={prompt} onClick={() => sendChatMessage(prompt)}>
              {prompt}
            </button>
          ))}
        </div>

        <form
          className="chat-composer"
          onSubmit={(event) => {
            event.preventDefault();
            sendChatMessage();
          }}
        >
          <input
            type="text"
            value={chatInput}
            placeholder="Ask for an edit..."
            onChange={(event) => setChatInput(event.target.value)}
          />
          <button type="submit" aria-label="Send message">
            <Send size={17} />
          </button>
        </form>
      </aside>
    </section>
  );
}
