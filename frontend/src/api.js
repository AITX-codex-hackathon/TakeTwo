const BASE = "";

export async function uploadVideo(file) {
  const form = new FormData();
  form.append("video", file);
  const res = await fetch(`${BASE}/jobs`, { method: "POST", body: form });
  return res.json();
}

export async function getJob(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}`);
  if (!res.ok) {
    let message = `${res.status}`;
    try {
      const text = await res.text();
      if (text) message = `${res.status}: ${text.slice(0, 240)}`;
    } catch {
      // Keep the status-only message when the response body is unavailable.
    }
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export async function retryJob(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}/retry`, { method: "POST" });
  return res.json();
}

export async function updateInsert(jobId, insertId, status) {
  const res = await fetch(`${BASE}/jobs/${jobId}/inserts/${insertId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return res.json();
}

export async function applyEdits(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}/apply`, { method: "POST" });
  return res.json();
}

export function fileUrl(jobId, kind, filename) {
  return `${BASE}/jobs/${jobId}/file/${kind}/${filename}`;
}
