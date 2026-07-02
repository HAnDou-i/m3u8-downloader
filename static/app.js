const form = document.querySelector("#jobForm");
const jobsEl = document.querySelector("#jobs");
const healthEl = document.querySelector("#health");
const refreshBtn = document.querySelector("#refresh");
const clearDoneBtn = document.querySelector("#clearDone");
const probeBtn = document.querySelector("#probeBtn");
const qualityWrap = document.querySelector("#qualityWrap");
const qualitySel = document.querySelector("#quality");
const submitBtn = document.querySelector("#submitBtn");

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const STATUS_MAP = {
  queued: "排队中",
  running: "下载中",
  done: "已完成",
  error: "失败",
  cancelled: "已取消",
  paused: "已暂停",
};

const STATUS_CLASS = {
  queued: "st-queued",
  running: "st-running",
  done: "st-done",
  error: "st-error",
  cancelled: "st-cancelled",
  paused: "st-paused",
};

function statusText(s) { return STATUS_MAP[s] || s; }
function statusClass(s) { return STATUS_CLASS[s] || ""; }

function escapeHtml(v) {
  return String(v || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])
  );
}

function timeAgo(ts) {
  if (!ts) return "";
  const diff = Math.floor((Date.now() / 1000) - ts);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  return Math.floor(diff / 86400) + " 天前";
}

// ---------------------------------------------------------------------------
// Render jobs
// ---------------------------------------------------------------------------

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsEl.innerHTML = `
      <div class="empty">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        <p>暂无下载任务</p>
        <span>在左侧输入 M3U8 链接开始</span>
      </div>`;
    return;
  }

  jobsEl.innerHTML = jobs
    .map((job) => {
      const canDownload = job.status === "done";
            const canCancel = job.status === "running" || job.status === "queued";
      const canRetry = job.status === "error" || job.status === "cancelled" || job.status === "paused";
      const canPause = job.status === "running" || job.status === "queued";
    
      const canDelete =
        job.status === "done" ||
        job.status === "error" ||
        job.status === "cancelled" ||
        job.status === "paused";
      const pct = Math.max(0, Math.min(100, job.percent || 0));
      const logs = (job.logs || [])
        .slice(-6)
        .map((l) => `<li>${escapeHtml(l)}</li>`)
        .join("");

      return `
      <article class="job ${statusClass(job.status)}">
        <div class="job-header">
          <div class="job-info">
            <strong class="job-name">${escapeHtml(job.name)}</strong>
            <div class="job-meta">
              <span class="badge badge-${job.status}">${statusText(job.status)}</span>
              ${job.speed ? `<span class="speed">${escapeHtml(job.speed)}</span>` : ""}
              ${job.size ? `<span class="size">${escapeHtml(job.size)}</span>` : ""}
              <span class="time">${timeAgo(job.created_at)}</span>
            </div>
          </div>
          <span class="percent">${pct.toFixed(1)}%</span>
        </div>
        <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>
        <div class="progress-text">${escapeHtml(job.progress_text || "")}</div>
        <div class="actions">
          ${
            canDownload
              ? `<a href="/downloads/${encodeURIComponent(job.name)}" class="btn btn-sm btn-download">
                   <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                   下载文件
                 </a>`
              : ""
          }
          ${
            canCancel
              ? `<button data-cancel="${job.id}" class="btn btn-sm btn-cancel" type="button">
                   <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                   取消
                 </button>`
              : ""
          }
          ${
            canDelete
              ? `<button data-delete="${job.id}" class="btn btn-sm btn-ghost-danger" type="button">
                   <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                   删除
                 </button>`
              : ""
          }
          ${
            canPause
              ? `<button data-pause="${job.id}" class="btn btn-sm btn-pause" type="button">
                   <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
                   暂停
                 </button>`
              : ""
          }
          ${
            canRetry
              ? `<button data-retry="${job.id}" class="btn btn-sm btn-retry" type="button">
                   <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                   重试
                 </button>`
              : ""
          }
        </div>
        ${logs ? `<ul class="logs">${logs}</ul>` : ""}
      </article>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Refresh loops
// ---------------------------------------------------------------------------

async function refreshJobs() {
  try {
    renderJobs(await api("/api/jobs"));
  } catch (e) {
    jobsEl.innerHTML = `<div class="empty"><p>${escapeHtml(e.message)}</p></div>`;
  }
}

async function refreshHealth() {
  try {
    const d = await api("/api/health");
    healthEl.textContent = `FFmpeg: ${d.ffmpeg}  |  保存目录: ${d.download_dir}`;
  } catch {
    healthEl.textContent = "服务未就绪";
  }
}

// ---------------------------------------------------------------------------
// Probe quality
// ---------------------------------------------------------------------------

let lastVariants = [];

probeBtn.addEventListener("click", async () => {
  const url = form.url.value.trim();
  if (!url) return alert("请先输入 M3U8 链接");
  probeBtn.disabled = true;
  probeBtn.textContent = "探测中...";
  qualityWrap.style.display = "none";
  lastVariants = [];
  try {
    const data = await api("/api/probe", {
      method: "POST",
      body: JSON.stringify({
        url,
        referer: form.referer.value,
        cookie: form.cookie.value,
      }),
    });
    if (data.variants && data.variants.length > 0) {
      lastVariants = data.variants;
      qualitySel.innerHTML =
        `<option value="">原始链接（默认）</option>` +
        data.variants
          .map(
            (v, i) =>
              `<option value="${i}">${escapeHtml(v.label)}</option>`
          )
          .join("");
      qualityWrap.style.display = "";
    } else {
      qualitySel.innerHTML = "";
      qualityWrap.style.display = "none";
      alert("未检测到多画质流，将使用原始链接下载");
    }
  } catch (e) {
    alert("探测失败: " + e.message);
  } finally {
    probeBtn.disabled = false;
    probeBtn.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> 探测画质';
  }
});

// ---------------------------------------------------------------------------
// Submit job
// ---------------------------------------------------------------------------

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitBtn.disabled = true;
  submitBtn.innerHTML =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> 提交中...';
  try {
    let downloadUrl = form.url.value.trim();

    // If a quality variant is selected, use that URL instead
    const qi = qualitySel.value;
    if (qi !== "" && lastVariants[parseInt(qi)]) {
      downloadUrl = lastVariants[parseInt(qi)].url;
    }

    await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        url: downloadUrl,
        name: form.name.value,
        referer: form.referer.value,
        cookie: form.cookie.value,
        quality: qualitySel.options[qualitySel.selectedIndex]?.text || "",
      }),
    });
    form.url.value = "";
    qualityWrap.style.display = "none";
    lastVariants = [];
    await refreshJobs();
  } catch (e) {
    alert(e.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> 开始下载';
  }
});

// ---------------------------------------------------------------------------
// Job actions
// ---------------------------------------------------------------------------

jobsEl.addEventListener("click", async (event) => {
  const cancelBtn = event.target.closest("[data-cancel]");
  if (cancelBtn) {
    cancelBtn.disabled = true;
    await api(`/api/jobs/${cancelBtn.dataset.cancel}/cancel`, {
      method: "POST",
      body: "{}",
    });
    await refreshJobs();
    return;
  }
  const pauseBtn = event.target.closest("[data-pause]");
  if (pauseBtn) {
    pauseBtn.disabled = true;
    await api(`/api/jobs/${pauseBtn.dataset.pause}/pause`, {
      method: "POST",
      body: "{}",
    });
    await refreshJobs();
    return;
  }
  const retryBtn = event.target.closest("[data-retry]");
  if (retryBtn) {
    retryBtn.disabled = true;
    await api(`/api/jobs/${retryBtn.dataset.retry}/retry`, {
      method: "POST",
      body: "{}",
    });
    await refreshJobs();
    return;
  }
  const deleteBtn = event.target.closest("[data-delete]");
  if (deleteBtn) {
    if (!confirm("确定删除此任务？")) return;
    deleteBtn.disabled = true;
    await api(`/api/jobs/${deleteBtn.dataset.delete}/delete`, {
      method: "POST",
      body: "{}",
    });
    await refreshJobs();
    return;
  }
});

// Clear all done
clearDoneBtn.addEventListener("click", async () => {
  try {
    const d = await api("/api/jobs/clear-done", { method: "POST", body: "{}" });
    if (d.removed === 0) alert("没有已完成的任务可清除");
    await refreshJobs();
  } catch (e) {
    alert(e.message);
  }
});

refreshBtn.addEventListener("click", refreshJobs);

// Boot
refreshHealth();
refreshJobs();
setInterval(refreshJobs, 2000);




