import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_from_directory

APP_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", APP_DIR / "downloads")).resolve()
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

app = Flask(__name__)
jobs = {}
jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_name(value: str) -> str:
    value = (value or "video").strip()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "video"


def user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125 Safari/537.36"
    )


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url


def build_headers(referer: str = "", cookie: str = "") -> str:
    lines = [f"User-Agent: {user_agent()}"]
    if referer:
        lines.append(f"Referer: {referer}")
        lines.append(f"Origin: {origin_of(referer)}")
    if cookie:
        lines.append(f"Cookie: {cookie}")
    return "\r\n".join(lines) + "\r\n"


def parse_time(value: str) -> float:
    try:
        h, m, s = value.split(":")
        return float(h) * 3600 + float(m) * 60 + float(s)
    except Exception:
        return 0.0


def format_speed(bytes_per_sec: float) -> str:
    """Format download speed with appropriate unit."""
    if bytes_per_sec <= 0:
        return ""
    if bytes_per_sec >= 1048576:
        return f"{bytes_per_sec / 1048576:.1f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def format_size(bytes_val: float) -> str:
    """Format file size with appropriate unit."""
    if bytes_val <= 0:
        return "0 B"
    if bytes_val >= 1073741824:
        return f"{bytes_val / 1073741824:.2f} GB"
    if bytes_val >= 1048576:
        return f"{bytes_val / 1048576:.1f} MB"
    if bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val:.0f} B"


def format_seconds(seconds: float) -> str:
    seconds = int(max(0, seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Quality probing
# ---------------------------------------------------------------------------

def probe_streams(url: str, referer: str = "", cookie: str = "") -> list:
    """Return a list of {index, resolution, bandwidth, url} for each video stream in a master playlist."""
    hdrs = build_headers(referer, cookie)
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-headers", hdrs, url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        items = []
        for i, s in enumerate(streams):
            w = s.get("width", 0)
            h_val = s.get("height", 0)
            bw = int(s.get("bit_rate", 0) or 0)
            label = f"{w}x{h_val}" if w and h_val else s.get("codec_name", "unknown")
            items.append({
                "index": i,
                "resolution": label,
                "bandwidth": bw,
                "width": w,
                "height": h_val,
            })
        return items
    except Exception:
        return []


def probe_master_playlist(url: str, referer: str = "", cookie: str = "") -> list:
    """Parse a master M3U8 to find variant streams with resolution info."""
    import urllib.request
    hdrs = {"User-Agent": user_agent()}
    if referer:
        hdrs["Referer"] = referer
    if cookie:
        hdrs["Cookie"] = cookie
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    variants = []
    lines = text.strip().splitlines()
    base_url = url.rsplit("/", 1)[0] + "/"
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = {}
            for m in re.finditer(r'(\w[\w-]*)=("[^"]*"|[^,]+)', line):
                key = m.group(1)
                val = m.group(2).strip('"')
                attrs[key] = val
            bw = int(attrs.get("BANDWIDTH", 0))
            res = attrs.get("RESOLUTION", "")
            i += 1
            if i < len(lines):
                next_line = lines[i].strip()
                if next_line and not next_line.startswith("#"):
                    stream_url = next_line if next_line.startswith("http") else base_url + next_line
                    variants.append({
                        "bandwidth": bw,
                        "resolution": res,
                        "url": stream_url,
                    })
        i += 1

    variants.sort(key=lambda v: v["bandwidth"], reverse=True)
    for idx, v in enumerate(variants):
        v["index"] = idx
        if not v["resolution"]:
            if v["bandwidth"] >= 5_000_000:
                v["resolution"] = "1080p"
            elif v["bandwidth"] >= 2_000_000:
                v["resolution"] = "720p"
            elif v["bandwidth"] >= 800_000:
                v["resolution"] = "480p"
            else:
                v["resolution"] = "360p"
        v["label"] = f"{v['resolution']} ({v['bandwidth'] // 1000} kbps)"
    return variants


def probe_duration(url: str, referer: str = "", cookie: str = "") -> float:
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-headers", build_headers(referer, cookie), url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return float(json.loads(result.stdout).get("format", {}).get("duration") or 0)
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------

def set_job(job_id: str, **updates):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(updates)


def append_log(job_id: str, text: str):
    with jobs_lock:
        if job_id in jobs:
            logs = jobs[job_id].setdefault("logs", [])
            logs.append(f"{time.strftime('%H:%M:%S')}  {text}")
            del logs[:-200]


def is_image_m3u8(url: str, referer: str = "", cookie: str = "") -> tuple:
    """Check if m3u8 contains image segments instead of video ts segments.
    Returns (is_image, segment_urls, total_duration)."""
    import urllib.request
    hdrs = {"User-Agent": user_agent()}
    if referer:
        hdrs["Referer"] = referer
    if cookie:
        hdrs["Cookie"] = cookie
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return False, [], 0

    base_url = url.rsplit("/", 1)[0] + "/"
    lines = text.strip().splitlines()
    segments = []
    total_dur = 0.0
    current_dur = 0.0
    has_ts = False
    has_image = False

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                current_dur = float(line.split(":")[1].rstrip(","))
            except Exception:
                current_dur = 0.0
            total_dur += current_dur
        elif line and not line.startswith("#"):
            seg_url = line if line.startswith("http") else base_url + line
            segments.append({"url": seg_url, "duration": current_dur})
            lower = line.lower()
            if lower.endswith((".jpeg", ".jpg", ".png", ".webp", ".bmp")):
                has_image = True
            elif lower.endswith(".ts") or "ts" in lower:
                has_ts = True
            current_dur = 0.0

    return has_image and not has_ts, segments, total_dur


def run_image_m3u8(job_id: str, segments: list, total_duration: float, output: Path, referer: str, cookie: str):
    """Download image segments and concat into a video."""
    import tempfile
    import urllib.request
    import glob as globmod

    # Resume: reuse existing tmpdir if available
    total = len(segments)
    existing_tmpdir = None
    existing_downloaded = 0
    with jobs_lock:
        job_data = jobs.get(job_id, {})
        existing_tmpdir = job_data.get("_tmpdir")
        existing_downloaded = job_data.get("_downloaded", 0)

    if existing_tmpdir and Path(existing_tmpdir).exists():
        tmpdir = Path(existing_tmpdir)
        append_log(job_id, f"继续下载，已完成 {existing_downloaded}/{total} 段")
    else:
        tmpdir = Path(tempfile.mkdtemp(prefix="m3u8_img_"))
        existing_downloaded = 0
        append_log(job_id, f"检测到图片序列，共 {total} 段")

    # Clear resume data
    set_job(job_id, _tmpdir=None, _downloaded=0)

    hdrs = {"User-Agent": user_agent()}
    if referer:
        hdrs["Referer"] = referer
    if cookie:
        hdrs["Cookie"] = cookie

    # Download all images with speed tracking
    downloaded = []
    total_bytes = 0
    speed_bytes = 0
    speed_time = time.time()

    for i, seg in enumerate(segments):
        img_path = tmpdir / f"frame_{i:05d}.jpeg"
        # Skip already downloaded frames (resume support)
        if i < existing_downloaded and img_path.exists():
            file_size = img_path.stat().st_size
            total_bytes += file_size
            downloaded.append(img_path)
            pct = round((i + 1) / total * 100, 1)
            set_job(job_id, percent=pct, progress_text=f"跳过已下载 {i+1}/{total}")
            continue

        try:
            req = urllib.request.Request(seg["url"], headers=hdrs)
            with urllib.request.urlopen(req, timeout=10) as resp:
                chunks = []
                while True:
                    # Check cancel/pause between chunks
                    should_stop = False
                    is_paused = False
                    with jobs_lock:
                        job_state = jobs.get(job_id, {})
                        if job_state.get("cancel") or job_state.get("paused"):
                            should_stop = True
                            is_paused = job_state.get("paused")
                    if should_stop:
                        resp.close()
                        if is_paused:
                            # Save progress for resume
                            set_job(job_id, status="paused", progress_text="已暂停",
                                    _tmpdir=str(tmpdir), _downloaded=len(downloaded))
                            append_log(job_id, f"任务已暂停（已下载 {len(downloaded)}/{total}）")
                        else:
                            shutil.rmtree(tmpdir, ignore_errors=True)
                            set_job(job_id, status="cancelled", percent=0, progress_text="已取消")
                            append_log(job_id, "正在取消任务...")
                        return
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    chunks.append(chunk)
                data = b"".join(chunks)
                img_path.write_bytes(data)
                file_size = len(data)
            total_bytes += file_size
            downloaded.append(img_path)
        except Exception as e:
            should_stop = False
            with jobs_lock:
                if jobs.get(job_id, {}).get("cancel") or jobs.get(job_id, {}).get("paused"):
                    should_stop = True
            if should_stop:
                with jobs_lock:
                    if jobs.get(job_id, {}).get("paused"):
                        set_job(job_id, status="paused", progress_text="已暂停",
                                _tmpdir=str(tmpdir), _downloaded=len(downloaded))
                    else:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                return
            append_log(job_id, f"下载第 {i+1} 段失败: {str(e)[:60]}")
            continue

        # Calculate speed every iteration
        now = time.time()
        dt = now - speed_time
        speed_bytes += file_size
        speed = ""
        if dt >= 0.5:
            bps = speed_bytes / dt
            speed = format_speed(bps)
            speed_bytes = 0
            speed_time = now

        pct = round((i + 1) / total * 100, 1)
        size_str = format_size(total_bytes)
        set_job(job_id, percent=pct,
                progress_text=f"下载图片 {i+1}/{total} ({size_str})",
                speed=speed if speed else jobs.get(job_id, {}).get("speed", ""),
                size=size_str)

    if not downloaded:
        append_log(job_id, "没有成功下载任何图片")
        shutil.rmtree(tmpdir, ignore_errors=True)
        set_job(job_id, status="error", progress_text="下载失败：无有效图片")
        return

    append_log(job_id, f"已下载 {len(downloaded)} 张图片，开始合成视频...")

    # Create concat list for ffmpeg
    frame_duration = segments[0]["duration"] if segments else 4.0
    concat_file = tmpdir / "concat.txt"
    with open(concat_file, "w") as f:
        for img in downloaded:
            f.write(f"file '{img}'\n")
            f.write(f"duration {frame_duration}\n")
        # Last image needs to be listed again for ffmpeg concat demuxer
        f.write(f"file '{downloaded[-1]}'\n")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
        )
        set_job(job_id, status="running", progress_text="合成视频中...")
        process.wait()

        if process.returncode == 0 and output.exists() and output.stat().st_size > 1024:
            size_mb = output.stat().st_size / 1048576
            set_job(job_id, status="done", percent=100, progress_text="下载完成",
                    size=f"{size_mb:.1f} MB", finished_at=time.time())
            append_log(job_id, f"合成完成：{output.name} ({size_mb:.1f} MB)")
        else:
            set_job(job_id, status="error", progress_text="视频合成失败")
            append_log(job_id, "FFmpeg 合成失败")
    except Exception as e:
        set_job(job_id, status="error", progress_text=str(e)[:120])
        append_log(job_id, f"合成错误：{str(e)[:160]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_download(job_id: str):
    with jobs_lock:
        job = dict(jobs[job_id])

    url = job["url"]
    referer = job.get("referer", "")
    cookie = job.get("cookie", "")
    output = Path(job["path"])
    output.parent.mkdir(parents=True, exist_ok=True)

    # Check if this is an image-based m3u8
    is_image, segments, total_dur = is_image_m3u8(url, referer, cookie)
    if is_image and segments:
        run_image_m3u8(job_id, segments, total_dur, output, referer, cookie)
        return

    append_log(job_id, "正在探测视频信息...")
    duration = probe_duration(url, referer, cookie)
    set_job(job_id, duration=duration)
    if duration:
        append_log(job_id, f"视频时长 {format_seconds(duration)}")

    cmd = [
        FFMPEG, "-y",
        "-headers", build_headers(referer, cookie),
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        str(output),
    ]

    append_log(job_id, "开始下载")
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
        )
        set_job(job_id, pid=process.pid, status="running")
        time_re = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
        speed_re = re.compile(r"speed=\s*([\d.]+)x")

        for line in process.stderr:
            with jobs_lock:
                job_state = jobs.get(job_id, {})
                cancelled = job_state.get("cancel")
                paused = job_state.get("paused")
            if cancelled:
                process.terminate()
                append_log(job_id, "正在取消任务...")
                break
            if paused:
                process.terminate()
                set_job(job_id, status="paused", progress_text="已暂停")
                append_log(job_id, "任务已暂停")
                return

            match = time_re.search(line)
            if not match:
                continue

            current = parse_time(match.group(1))
            percent = min(100.0, current / duration * 100) if duration else 0.0
            # Calculate real download speed from file size growth
            speed = ""
            if output.exists():
                now = time.time()
                cur_size = output.stat().st_size
                with jobs_lock:
                    prev_size = jobs.get(job_id, {}).get("_prev_size", 0)
                    prev_time = jobs.get(job_id, {}).get("_prev_time", 0)
                if prev_time > 0 and now > prev_time:
                    dt = now - prev_time
                    bps = (cur_size - prev_size) / dt
                    speed = format_speed(bps)
                with jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["_prev_size"] = cur_size
                        jobs[job_id]["_prev_time"] = now
            set_job(
                job_id,
                current=current,
                percent=round(percent, 1),
                speed=speed,
                progress_text=(
                    f"{format_seconds(current)} / {format_seconds(duration)}"
                    if duration
                    else format_seconds(current)
                ),
            )

        process.wait()
        with jobs_lock:
            cancelled = jobs.get(job_id, {}).get("cancel")

        if cancelled or jobs.get(job_id, {}).get("cancel"):
            if output.exists():
                output.unlink()
            set_job(job_id, status="cancelled", percent=0, progress_text="已取消")
            append_log(job_id, "任务已取消")
            return

        if process.returncode == 0 and output.exists() and output.stat().st_size > 1024:
            size_mb = output.stat().st_size / 1048576
            set_job(
                job_id,
                status="done",
                percent=100,
                progress_text="下载完成",
                size=f"{size_mb:.1f} MB",
                finished_at=time.time(),
            )
            append_log(job_id, f"下载完成：{output.name} ({size_mb:.1f} MB)")
            return

        set_job(job_id, status="error", progress_text="FFmpeg 下载失败")
        append_log(job_id, "FFmpeg 下载失败，请检查链接或 Referer")
    except FileNotFoundError:
        set_job(job_id, status="error", progress_text="未找到 ffmpeg")
        append_log(job_id, "未找到 ffmpeg，请确认容器或系统已安装")
    except Exception as exc:
        set_job(job_id, status="error", progress_text=str(exc)[:120])
        append_log(job_id, f"错误：{str(exc)[:160]}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "ffmpeg": FFMPEG, "download_dir": str(DOWNLOAD_DIR)})


@app.post("/api/probe")
def probe():
    """Probe a M3U8 URL for available quality variants."""
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    referer = (data.get("referer") or "").strip()
    cookie = (data.get("cookie") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return jsonify({"error": "请输入有效的 M3U8 链接"}), 400

    variants = probe_master_playlist(url, referer, cookie)
    duration = probe_duration(url, referer, cookie)
    return jsonify({"variants": variants, "duration": duration})


@app.post("/api/jobs")
def create_job():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return jsonify({"error": "请输入有效的 M3U8 链接"}), 400

    name = safe_name(data.get("name"))
    if not name.lower().endswith((".mp4", ".mkv", ".ts")):
        name += ".mp4"

    job_id = uuid.uuid4().hex
    path = DOWNLOAD_DIR / name

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "url": url,
            "referer": (data.get("referer") or "").strip(),
            "cookie": (data.get("cookie") or "").strip(),
            "quality": (data.get("quality") or "").strip(),
            "name": name,
            "path": str(path),
            "status": "queued",
            "percent": 0,
            "progress_text": "排队中",
            "speed": "",
            "size": "",
            "logs": [],
            "cancel": False,
            "paused": False,
            "pid": None,
            "created_at": time.time(),
            "finished_at": None,
        }

    thread = threading.Thread(target=run_download, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"id": job_id})


@app.get("/api/jobs")
def list_jobs():
    with jobs_lock:
        ordered = sorted(jobs.values(), key=lambda item: item["created_at"], reverse=True)
        return jsonify(ordered)


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(job)



@app.post("/api/jobs/<job_id>/retry")
def retry_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        if job["status"] not in ("error", "cancelled", "paused"):
            return jsonify({"error": "只能重试失败、已取消或已暂停的任务"}), 400
        # Reset job state
        job["status"] = "queued"
        job["percent"] = 0
        job["progress_text"] = "排队中"
        job["speed"] = ""
        job["size"] = ""
        job["cancel"] = False
        job["paused"] = False
        job["logs"] = []
        job["finished_at"] = None
    thread = threading.Thread(target=run_download, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/pause")
def pause_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        if job["status"] not in ("running", "queued"):
            return jsonify({"error": "只能暂停进行中的任务"}), 400
        job["paused"] = True
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/resume")
def resume_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        if job["status"] != "paused":
            return jsonify({"error": "只能继续已暂停的任务"}), 400
        job["status"] = "queued"
        job["progress_text"] = "排队中"
        job["cancel"] = False
        job["paused"] = False
    thread = threading.Thread(target=run_download, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"ok": True})
@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    with jobs_lock:
        if job_id not in jobs:
            return jsonify({"error": "任务不存在"}), 404
        jobs[job_id]["cancel"] = True
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/delete")
def delete_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        if job["status"] in ("running", "queued"):
            return jsonify({"error": "进行中的任务无法删除"}), 400
        # Delete file if it exists
        try:
            p = Path(job["path"])
            if p.exists():
                p.unlink()
        except Exception:
            pass
        del jobs[job_id]
    return jsonify({"ok": True})


@app.post("/api/jobs/clear-done")
def clear_done():
    with jobs_lock:
        to_remove = [jid for jid, j in jobs.items() if j["status"] in ("done", "error", "cancelled")]
        for jid in to_remove:
            del jobs[jid]
    return jsonify({"ok": True, "removed": len(to_remove)})


@app.get("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "7860"))
    print(f"[M3U8 Downloader] Starting on http://0.0.0.0:{port}")
    print(f"[M3U8 Downloader] Download dir: {DOWNLOAD_DIR}")
    print(f"[M3U8 Downloader] FFmpeg: {FFMPEG}")
    app.run(host="0.0.0.0", port=port, threaded=True)












