#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests
import threading
from collections import deque

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "maxpremier"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/mpremiuum.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787671958979.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "maxpremier.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

DECODER_THREADS = os.getenv("DECODER_THREADS", "1")
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "60"))
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))

CONCAT_FILE = "concat_playlist.txt"

# Filter modunda aynı anda en fazla kaç içerik FFmpeg'e input olarak verilsin.
# Çift URL'li içeriklerde her içerik 2 input açar, yani 4 içerik = 8 input.
MAX_INPUTS_PER_BATCH = int(os.getenv("MAX_INPUTS_PER_BATCH", "4"))


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


# ===================== FFPROBE (SAĞLAMLAŞTIRILDI) =====================
def get_video_duration_ffprobe(video_url):
    """
    FFprobe ile videonun gerçek toplam süresini çeker.
    H.264 SPS hataları gibi kirli stderr çıktılarını temizler.
    """
    cmd = [
        'ffprobe', '-v', 'error',
        '-allowed_extensions', 'ALL',
        '-analyzeduration', '20000000',
        '-probesize', '20000000',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        '-headers', f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: {STREAM_REFERER}\r\n",
        video_url
    ]
    try:
        output = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, timeout=20
        ).decode('utf-8', errors='ignore')

        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        duration = 0.0

        for ln in reversed(lines):
            if re.fullmatch(r'\d+(\.\d+)?', ln):
                duration = float(ln)
                break
            m = re.search(r'(\d+\.\d+)\s*$', ln)
            if m:
                try:
                    duration = float(m.group(1))
                    break
                except ValueError:
                    continue

        if duration > 0:
            print(f"⏱️ ffprobe toplam süre: {duration:.1f}s ({format_hms(duration)})")
            return duration
    except Exception as e:
        print(f"⚠️ ffprobe süre okunamadı: {e}")
    return 0.0


# ===================== STATE (JSON) =====================
def get_local_state():
    """
    Yerel state dosyasından son durumu okur.
    Döner: (index, seconds, url, title)
    """
    if os.path.exists(STATE_FILE_NAME):
        if os.path.getsize(STATE_FILE_NAME) == 0:
            print(f"⚠️ State dosyası boş ({STATE_FILE_NAME}), 0'dan başlanıyor.")
            return 0, 0, "", ""
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                idx = int(data.get("last_index", 0))
                sec = int(data.get("last_seconds", 0))
                url = data.get("last_url", "")
                title = data.get("last_title", "")
                print(f"✅ State okundu ({STATE_FILE_NAME}) → İndeks: {idx}, Saniye: {sec}, Başlık: {title}")
                return idx, sec, url, title
        except Exception as e:
            print(f"⚠️ State okuma hatası: {e}")
    else:
        print(f"ℹ️ State dosyası yok, 0'dan başlanıyor.")
    return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):
    """Son konumu JSON state dosyasına kaydeder."""
    try:
        data = {
            "last_index": int(index),
            "last_seconds": int(seconds),
            "last_url": url,
            "last_title": title,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(STATE_FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ State yazma hatası: {e}")


# ===================== M3U / LOGO / DOSYA =====================
def get_m3u_playlist(m3u_url):
    try:
        headers = {'User-Agent': STREAM_USER_AGENT, 'Referer': STREAM_REFERER}
        response = requests.get(m3u_url, headers=headers, timeout=15)
        if response.status_code == 200:
            lines = response.text.splitlines()
            playlist = []
            pending_title = None
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF'):
                    match = re.search(r',(.+)$', line)
                    pending_title = match.group(1).strip() if match else None
                elif not line.startswith('#') and line.startswith('http'):
                    title = pending_title or os.path.basename(line.split('?')[0])
                    playlist.append({"url": line, "title": title})
                    pending_title = None
            return playlist
    except Exception as e:
        print(f"⚠️ M3U çekme hatası: {e}")
    return [{"url": m3u_url, "title": os.path.basename(m3u_url)}]


def download_logo():
    try:
        response = requests.get(LOGO_URL, headers={'User-Agent': STREAM_USER_AGENT}, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo indirildi.")
    except Exception as e:
        print(f"⚠️ Logo hatası: {e}")


def write_title_file(title):
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ title.txt hatası: {e}")


def write_remaining_time_file(remaining_seconds):
    try:
        with open('time.txt', 'w', encoding='utf-8') as f:
            f.write(format_hms(remaining_seconds))
    except Exception as e:
        print(f"⚠️ time.txt hatası: {e}")


def print_dashboard(title, index, playlist_len, seconds, status="🟢 Yayında"):
    print("┌" + "─" * 58 + "┐")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ 🔢 Sıra           : {index + 1}/{playlist_len:<32} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(title, index, playlist_len, seconds, status="🟢 Yayında"):
    if not GITHUB_STEP_SUMMARY:
        return
    try:
        content = (
            "## 📺 Canlı Yayın Durumu (Kesintisiz + Çift URL)\n\n"
            "| Alan | Değer |\n|---|---|\n"
            f"| 🎬 Şu an oynayan içerik | {title} |\n"
            f"| 🔢 Playlist sırası | {index + 1} / {playlist_len} |\n"
            f"| ⏱️ Geçen süre | {format_hms(seconds)} |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 🕒 Son güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Step summary hatası: {e}")


# ===================== CONCAT DOSYASI (Demuxer Modu) =====================
def escape_concat_path(path):
    return path.replace("'", "'\\''")


def build_concat_file(items, seek_seconds_first):
    """
    concat demuxer formatında dosya yazar.
    items: [{"url": ..., "title": ...}, ...]  (hepsi TEK url olmalı)
    """
    lines = []
    for i, item in enumerate(items):
        url = item["url"]
        lines.append(f"file '{escape_concat_path(url)}'")
        if i == 0 and seek_seconds_first > 0:
            lines.append(f"inpoint {seek_seconds_first}")

    with open(CONCAT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(items)


def is_dual_url(url):
    return ";" in url


def split_dual_url(url):
    v, a = url.split(";", 1)
    return v.strip(), a.strip()


# ===================== FFMPEG KOMUTU OLUŞTURMA =====================
def build_filter_graph(has_logo, batch_meta, logo_input_index):
    """
    filter_complex string'i üretir.
    
    batch_meta: [{"is_dual": bool}, ...] — sırayla batch'teki içerikler.
    Her item için:
      - tek URL ise → 1 input (hem video hem audio aynı)
      - çift URL ise → 2 input (video, audio)
    
    Dönüş: (filter_str, total_input_count)
    """
    parts = []
    video_indices = []
    audio_indices = []

    cursor = 0
    for n, item in enumerate(batch_meta):
        if item["is_dual"]:
            video_indices.append((n, cursor))
            cursor += 1
            audio_indices.append((n, cursor))
            cursor += 1
        else:
            video_indices.append((n, cursor))
            audio_indices.append((n, cursor))
            cursor += 1

    total_input_count = cursor

    # 1) Video stream'leri normalize et → [vN]
    for n, v_idx in video_indices:
        parts.append(
            f"[{v_idx}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25,"
            f"setsar=1[v{n}]"
        )

    # 2) Audio stream'leri normalize et → [aN]
    for n, a_idx in audio_indices:
        parts.append(
            f"[{a_idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{n}]"
        )

    # 3) Video concat → [vcat]
    vcat_inputs = "".join(f"[v{n}]" for n, _ in video_indices)
    vcount = len(video_indices)
    parts.append(f"{vcat_inputs}concat=n={vcount}:v=1:a=0[vcat]")

    # 4) Audio concat → [acat]
    acat_inputs = "".join(f"[a{n}]" for n, _ in audio_indices)
    acount = len(audio_indices)
    parts.append(f"{acat_inputs}concat=n={acount}:v=0:a=1[acat]")

    # 5) Logo overlay
    if has_logo:
        parts.append(
            f"[{logo_input_index}:v]scale=-2:85,format=rgba,"
            f"colorchannelmixer=aa={LOGO_OPACITY}[logo1]"
        )
        parts.append("[vcat][logo1]overlay=50:50[tmp1]")
        last_video_label = "[tmp1]"
    else:
        last_video_label = "[vcat]"

    # 6) Drawtext (başlık + kalan süre)
    title_drawtext = (
        f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=19:"
        f"x=w-tw-20:y=h-th-20"
    )
    time_drawtext = (
        f"drawtext=textfile='time.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=18:"
        f"x=20:y=h-th-20"
    )
    parts.append(f"{last_video_label}{title_drawtext}[tmp2]")
    parts.append(f"[tmp2]{time_drawtext}[vout]")

    return ";".join(parts), total_input_count


def get_output_args():
    return [
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-pix_fmt', 'yuv420p',
        '-r', '25',
        '-b:v', '2500k',
        '-maxrate', '2500k',
        '-bufsize', '3000k',
        '-g', '50',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-ac', '2',
        '-ar', '44100',
        '-f', 'flv',
        RTMP_SERVER
    ]


# ===================== ANA AKIŞ =====================
def start_m3u_stream():
    print(f"🔧 M3U          : {M3U_URL}")
    print(f"🔧 Logo         : {LOGO_URL}")
    print(f"🔧 State        : {STATE_FILE_NAME}")
    print(f"🔧 RTMP         : {RTMP_SERVER}")
    print(f"🔧 Decoder thrd : {DECODER_THREADS}")
    print(f"🔧 Batch size   : {MAX_INPUTS_PER_BATCH}")
    print(f"🔧 Mod          : KESİNTİSİZ (concat demuxer/filter hibrit)")

    download_logo()

    # ---- State'ten devam ----
    current_index, last_seconds, last_url, last_title = get_local_state()

    playlist = get_m3u_playlist(M3U_URL)
    if not playlist:
        print("❌ Playlist boş, çıkılıyor.")
        return

    if current_index >= len(playlist):
        current_index = 0
        last_seconds = 0
        last_url = ""
        last_title = ""

    # Link değişti mi / aynı film mi kontrolü
    current_item = playlist[current_index]
    if last_url and last_url != current_item["url"]:
        if last_title and current_item["title"] == last_title:
            last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
            print(f"🔄 Link değişti ama film aynı → {last_seconds}s'den devam")
        else:
            print("🆕 İçerik değişti → baştan başlanıyor")
            last_seconds = 0

    write_title_file(current_item["title"])

    # İlk içeriğin toplam süresi
    first_probe = current_item["url"].split(";")[0].strip()
    first_duration = get_video_duration_ffprobe(first_probe)
    write_remaining_time_file(max(0, first_duration - last_seconds) if first_duration > 0 else last_seconds)

    print_dashboard(current_item["title"], current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")
    write_step_summary(current_item["title"], current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

    # ---- Mod seçimi ----
    has_any_dual = any(is_dual_url(playlist[i]["url"]) for i in range(current_index, len(playlist)))

    if not has_any_dual:
        print("🎯 Tüm içerikler tek URL → concat demuxer modu (hafif, hızlı)")
        run_concat_demuxer_mode(playlist, current_index, last_seconds)
    else:
        print("🎯 Çift URL tespit edildi → concat filter modu (batch'li)")
        run_concat_filter_mode(playlist, current_index, last_seconds)


# ===================== MOD 1: CONCAT DEMUXER =====================
def run_concat_demuxer_mode(playlist, current_index, last_seconds):
    items = playlist[current_index:]
    n = build_concat_file(items, last_seconds)
    if n == 0:
        print("❌ concat dosyası boş.")
        return

    print(f"📝 concat dosyası: {n} içerik, ilk seek: {last_seconds}s")

    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
        f"Origin: https://vidmody.com\r\n"
    )

    has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

    title_drawtext = (
        f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=19:"
        f"x=w-tw-20:y=h-th-20"
    )
    time_drawtext = (
        f"drawtext=textfile='time.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=18:"
        f"x=20:y=h-th-20"
    )

    if has_logo:
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25,setsar=1[main];'
            '[1:v]scale=-2:85,format=rgba,'
            f'colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
            '[main][logo1]overlay=50:50[tmp1];'
            f'[tmp1]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[vout]'
        )
        logo_inputs = ['-i', 'logo.png']
    else:
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25,setsar=1[main];'
            f'[main]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[vout]'
        )
        logo_inputs = []

    cmd = [
        'ffmpeg',
        '-re',
        '-f', 'concat',
        '-safe', '0',
        '-headers', headers_arg,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-fflags', '+genpts+discardcorrupt',
        '-err_detect', 'ignore_err',
        '-thread_queue_size', '1024',
        '-max_interleave_delta', '0',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-threads', DECODER_THREADS,
        '-i', CONCAT_FILE,
    ] + logo_inputs + [
        '-filter_complex', filter_str,
        '-map', '[vout]',
        '-map', '0:a:0?',
    ] + get_output_args()

    print("▶ FFmpeg (concat demuxer) başlatıldı...")
    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)

    cumulative = compute_cumulative(playlist, current_index, last_seconds)

    monitor_process(
        process=process,
        playlist=playlist,
        cumulative=cumulative,
        initial_index=current_index,
        initial_seconds=last_seconds,
        mode_label="demuxer",
    )


# ===================== MOD 2: CONCAT FILTER (Çift URL) =====================
def run_concat_filter_mode(playlist, current_index, last_seconds):
    """
    Çift URL'li içerikleri concat filter ile işler. Batch'ler halinde çalışır.
    """
    idx = current_index
    seek = last_seconds

    while idx < len(playlist):
        batch = []
        for i in range(idx, min(idx + MAX_INPUTS_PER_BATCH, len(playlist))):
            batch.append(playlist[i])

        print(f"📦 Batch başlıyor → sıra {idx+1} .. {idx+len(batch)}")
        result = run_filter_batch(batch, idx, seek, playlist)
        if result is None or result < 0:
            print("❌ Batch başarısız oldu, çıkılıyor (üst döngü tekrar deneyecek).")
            return
        idx += len(batch)
        seek = 0  # sonraki batch'ler baştan başlar

    print("🔁 Tüm playlist filter modunda tamamlandı.")


def run_filter_batch(batch, batch_start_index, start_seek, full_playlist):
    """
    Tek bir FFmpeg çağrısıyla batch'i oynatır.
    """
    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
        f"Origin: https://vidmody.com\r\n"
    )

    common_in_opts = [
        '-re',
        '-headers', headers_arg,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-err_detect', 'ignore_err',
        '-fflags', '+genpts+discardcorrupt',
        '-thread_queue_size', '1024',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-reconnect', '1',
        '-reconnect_at_eof', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '10',
        '-rw_timeout', '10000000',
        '-threads', DECODER_THREADS,
    ]

    input_args = []
    batch_meta = []

    for n, item in enumerate(batch):
        url = item["url"]
        seek_args = []
        if n == 0 and start_seek > 0:
            seek_args = ['-ss', str(start_seek)]

        if is_dual_url(url):
            v_url, a_url = split_dual_url(url)
            input_args += common_in_opts + seek_args + ['-i', v_url]
            input_args += common_in_opts + seek_args + ['-i', a_url]
            batch_meta.append({"is_dual": True})
        else:
            input_args += common_in_opts + seek_args + ['-i', url]
            batch_meta.append({"is_dual": False})

    total_stream_inputs = sum(2 if m["is_dual"] else 1 for m in batch_meta)
    logo_input_index = total_stream_inputs

    has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0
    logo_inputs = ['-i', 'logo.png'] if has_logo else []

    filter_str, total_inputs = build_filter_graph(
        has_logo=has_logo,
        batch_meta=batch_meta,
        logo_input_index=logo_input_index,
    )

    print(f"▶ FFmpeg (concat filter) batch — stream inputs: {total_stream_inputs}, "
          f"logo idx: {logo_input_index if has_logo else 'yok'}")

    cmd = ['ffmpeg'] + input_args + logo_inputs + [
        '-filter_complex', filter_str,
        '-map', '[vout]',
        '-map', '[acat]',
    ] + get_output_args()

    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)

    cumulative = compute_cumulative(full_playlist, batch_start_index, start_seek, limit=len(batch))

    final_state = monitor_process(
        process=process,
        playlist=full_playlist,
        cumulative=cumulative,
        initial_index=batch_start_index,
        initial_seconds=start_seek,
        mode_label="filter",
    )
    if final_state is None:
        return -1
    return final_state[1]


# ===================== YARDIMCILAR (DÜZELTİLDİ) =====================
def compute_cumulative(playlist, start_index, start_seek, limit=None):
    """
    Playlist[start_index:] içindeki her içeriğin kümülatif başlangıç saniyesini
    ve süresini hesaplar. Çift URL'de video süresi baz alınır.
    
    ÖNEMLİ: -ss uygulandığında ilk içeriğin efektif süresi start_seek kadar
    azalır ve kümülatif başlangıç 0'dan başlar (FFmpeg output 0'dan başlar).
    """
    result = []
    running = 0.0
    end = len(playlist) if limit is None else min(len(playlist), start_index + limit)

    for i in range(start_index, end):
        item = playlist[i]
        url = item["url"]
        probe_url = url.split(";")[0].strip() if is_dual_url(url) else url
        dur = get_video_duration_ffprobe(probe_url)

        # İlk içeriğe -ss uygulanıyorsa efektif süre kısalır
        if i == start_index and start_seek > 0:
            effective_dur = max(0.0, dur - start_seek)
        else:
            effective_dur = dur

        result.append({
            "index": i,
            "title": item["title"],
            "start": running,
            "duration": effective_dur,
            "original_duration": dur,
        })
        running += effective_dur

    if result:
        print(f"📊 Kümülatif hesap: ilk='{result[0]['title']}' "
              f"start={result[0]['start']:.0f}s, efektif={result[0]['duration']:.0f}s "
              f"(orijinal={result[0]['original_duration']:.0f}s, seek={start_seek}s)")

    return result


def monitor_process(process, playlist, cumulative, initial_index, initial_seconds, mode_label="demuxer"):
    """
    FFmpeg stderr'ini okur, dashboard/state/title/time günceller.
    Dönüş: (final_index, final_seconds) veya None (hata).
    """
    stderr_tail = deque(maxlen=60)
    state_lock = threading.Lock()

    state = {
        "index": initial_index,
        "seconds": initial_seconds,
        "last_progress": time.time(),
    }

    def _reader(proc=process, tail=stderr_tail, st=state, lock=state_lock):
        for line in proc.stderr:
            tail.append(line.rstrip())
            if "time=" not in line:
                continue
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if not m:
                continue
            h, mi, s = m.groups()
            played = int(h) * 3600 + int(mi) * 60 + float(s)

            # FFmpeg time= çıktısı, OUTPUT stream'in başından itibaren geçen
            # süredir. -ss kullanılsa bile output 0'dan başlar.
            absolute = played

            with lock:
                st["last_progress"] = time.time()

                active = None
                for entry in cumulative:
                    if entry["start"] <= absolute < entry["start"] + entry["duration"]:
                        active = entry
                        break
                if active is None and cumulative:
                    active = cumulative[-1]

                if active and active["index"] != st["index"]:
                    st["index"] = active["index"]
                    write_title_file(active["title"])
                    update_local_state(
                        active["index"], 0,
                        playlist[active["index"]]["url"],
                        active["title"]
                    )
                    print(f"🎬 Sıradaki içeriğe geçildi: [{active['index']+1}] {active['title']}")
                    print_dashboard(active["title"], active["index"], len(playlist), 0)
                    write_step_summary(active["title"], active["index"], len(playlist), 0)

                if active:
                    elapsed = absolute - active["start"]
                    remaining = max(0, active["duration"] - elapsed)
                    write_remaining_time_file(remaining)
                    st["seconds"] = elapsed

    def _watchdog(proc=process, st=state, lock=state_lock):
        while proc.poll() is None:
            time.sleep(5)
            with lock:
                last = st["last_progress"]
            if time.time() - last > WATCHDOG_TIMEOUT_SECONDS:
                print(f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS}s ilerleme yok, süreç sonlandırılıyor.")
                try:
                    proc.kill()
                except Exception:
                    pass
                break

    t_read = threading.Thread(target=_reader, daemon=True)
    t_wd = threading.Thread(target=_watchdog, daemon=True)
    t_read.start()
    t_wd.start()

    last_dashboard = time.time()
    try:
        while process.poll() is None:
            time.sleep(5)
            now = time.time()
            if now - last_dashboard >= 30:
                with state_lock:
                    idx = state["index"]
                    sec = int(state["seconds"])
                title = playlist[idx]["title"]
                print_dashboard(title, idx, len(playlist), sec)
                write_step_summary(title, idx, len(playlist), sec)
                update_local_state(idx, sec, playlist[idx]["url"], title)
                last_dashboard = now
    except KeyboardInterrupt:
        print("🛑 Manuel durdurma, FFmpeg kapatılıyor...")
        try:
            process.kill()
        except Exception:
            pass

    rc = process.poll()
    with state_lock:
        final_idx = state["index"]
        final_sec = int(state["seconds"])

    if rc == 0:
        next_idx = final_idx + 1
        update_local_state(next_idx, 0, "", "")
        return (next_idx, 0)

    print(f"⚠️ FFmpeg çıktı (rc={rc}). Kaldığı yerden devam edilecek: index={final_idx}, sec={final_sec}")
    if stderr_tail:
        print("🧾 FFmpeg son log satırları:")
        for tl in stderr_tail:
            print(f"   {tl}")
    safe_idx = min(final_idx, len(playlist) - 1)
    update_local_state(safe_idx, final_sec, playlist[safe_idx]["url"], playlist[safe_idx]["title"])
    return None


# ===================== BAŞLAT =====================
if __name__ == "__main__":
    while True:
        try:
            start_m3u_stream()
        except Exception as e:
            print(f"❌ Ana döngü hatası: {e}")
        print("⏳ 5 saniye sonra yeniden başlatılıyor...")
        time.sleep(5)
