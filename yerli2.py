#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests
from urllib.parse import urlparse, urljoin

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "maxyerli"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/yerli2.m3u")
LOGO_URL = os.getenv("LOGO_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787745128505.png")
FLAG_URL = os.getenv("FLAG_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/file_00000000eae88246b13a221f896ea385.png")

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "state_yerli.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

STREAM_EXTENSIONS = ('.m3u8', '.mp4', '.mpd', '.ts', '.mkv', '.webm')


def resolve_stream_url(url, timeout=15):
    headers = {
        'User-Agent': STREAM_USER_AGENT,
        'Referer': 'https://ha.vixolity.com/',
        'Origin': 'https://ha.vixolity.com',
        'Accept': '*/*',
    }
    if any(url.lower().split('?')[0].endswith(ext) for ext in STREAM_EXTENSIONS):
        return url
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        final_url = r.url
        if any(final_url.lower().split('?')[0].endswith(ext) for ext in STREAM_EXTENSIONS):
            print(f"✅ Redirect ile stream bulundu: {final_url}")
            return final_url
        html = r.text
        base = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
        patterns = [
            r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
            r'["\'](https?://[^"\']+\.mpd[^"\']*)["\']',
            r'file\s*:\s*["\']([^"\']+)["\']',
            r'source\s*:\s*["\']([^"\']+)["\']',
            r'["\'](/[^"\']+\.m3u8[^"\']*)["\']',
            r'["\'](/[^"\']+\.mp4[^"\']*)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                found = m.group(1)
                if found.startswith('/'):
                    found = urljoin(base, found)
                print(f"✅ HTML içinden stream bulundu: {found}")
                return found
        print(f"⚠️ Stream URL'i bulunamadı, orijinal deneniyor: {url}")
        return url
    except Exception as e:
        print(f"⚠️ resolve_stream_url hatası: {e}")
        return url


def download_assets():
    headers = {'User-Agent': STREAM_USER_AGENT}
    try:
        res_logo = requests.get(LOGO_URL, headers=headers, timeout=15)
        if res_logo.status_code == 200 and len(res_logo.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(res_logo.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def format_hms(total_seconds):
    total_seconds = int(total_seconds)
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def get_stream_duration(url):
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-user_agent', STREAM_USER_AGENT,
            '-headers', 'Referer: https://ha.vixolity.com/\r\n',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        duration = float(result.stdout.strip())
        if duration > 0:
            return duration
    except Exception as e:
        print(f"⚠️ Süre tespit hatası: {e}")
    return None


def escape_drawtext(text):
    if not text:
        return ""
    text = text.replace('\\', '\\\\')
    text = text.replace(':', '\\:')
    text = text.replace("'", "\\'")
    text = text.replace('%', '\\%')
    return text


def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                idx = data.get("last_index", 0)
                print(f"✅ Yerel state okundu => İndeks: {idx}")
                return idx
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    return 0


def update_local_state(index):
    try:
        data = {"last_index": int(index)}
        with open(STATE_FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 Konum kaydedildi => İndeks: {index}")
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")


def get_m3u_playlist(m3u_url):
    try:
        headers = {'User-Agent': STREAM_USER_AGENT}
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
            "## 📺 Canlı Yayın Durumu (Maxyerli)\n\n"
            "| Alan | Değer |\n"
            "|---|---|\n"
            f"| 🎬 Şu an oynayan içerik | {title} |\n"
            f"| 🔢 Playlist sırası | {index + 1} / {playlist_len} |\n"
            f"| ⏱️ Geçen süre | {format_hms(seconds)} (sa:dk:sn) |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 🕒 Son güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Step summary yazma hatası: {e}")


def start_m3u_stream():
    print(f"🔧 M3U    : {M3U_URL}")
    print(f"🔧 Logo   : {LOGO_URL}")
    print(f"🔧 State  : {STATE_FILE_NAME}")
    print(f"🔧 RTMP   : {RTMP_SERVER}")

    download_assets()
    current_index = get_local_state()

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            time.sleep(10)
            continue

        if current_index >= len(playlist):
            current_index = 0

        current_item = playlist[current_index]
        raw_url = current_item["url"]
        film_title = current_item["title"]

        print(f"🔍 Kaynak çözümleniyor: {raw_url}")
        target_stream_url = resolve_stream_url(raw_url)
        print(f"🎯 Kullanılacak stream: {target_stream_url}")

        print("=" * 60)
        print(f"🎬 {film_title}")
        print(f"🚀 RTMP: {RTMP_SERVER}")
        print("=" * 60)

        print_dashboard(film_title, current_index, len(playlist), 0, status="🟡 Başlatılıyor")
        write_step_summary(film_title, current_index, len(playlist), 0, status="🟡 Başlatılıyor")

        headers_arg = (
            f"User-Agent: {STREAM_USER_AGENT}\r\n"
            "Referer: https://ha.vixolity.com/\r\n"
            "Origin: https://ha.vixolity.com\r\n"
        )

        # ============================================================
        # HİÇBİR MÜDAHALE YOK — kaynak ne verirse o
        # ============================================================
        input_args = [
            '-thread_queue_size', '2048',
            '-headers', headers_arg,
            '-user_agent', STREAM_USER_AGENT,
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
            '-reconnect_at_eof', '1',
            '-http_persistent', '1',
            '-multiple_requests', '1',
            '-analyzeduration', '10000000',
            '-probesize', '10000000',
            '-re',
            '-i', target_stream_url,
        ]

        probe_url = target_stream_url
        film_duration = get_stream_duration(probe_url)
        if film_duration is not None:
            print(f"⏳ Süre: {format_hms(film_duration)}")
        else:
            print("⚠️ Süre tespit edilemedi.")

        has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        overlay_inputs = []
        filter_steps = [
            # Sadece ölçekle + pad. fps zorlaması yok. PTS reset yok.
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black[main]'
        ]

        last_stream = '[main]'
        next_input_index = 1

        if has_logo:
            logo_idx = next_input_index
            overlay_inputs.extend(['-i', 'logo.png'])
            next_input_index += 1
            filter_steps.append(f'[{logo_idx}:v]scale=-2:80[logo]')
            filter_steps.append(f'{last_stream}[logo]overlay=55:55[v_logo]')
            last_stream = '[v_logo]'

        escaped_title = escape_drawtext(film_title)

        drawtext_title = (
            f"drawtext=text='{escaped_title}':fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':fontcolor=white:fontsize=18:"
            f"borderw=2:bordercolor=black:x=w-tw-30:y=h-th-30"
        )

        if film_duration is not None:
            remaining_expr = f"({film_duration:.3f}-t)"
            hh_expr = f"trunc({remaining_expr}/3600)"
            mm_expr = f"trunc(mod({remaining_expr},3600)/60)"
            ss_expr = f"trunc(mod({remaining_expr},60))"
            remaining_time_text = (
                f"%{{eif\\:{hh_expr}\\:d\\:2}}\\:%{{eif\\:{mm_expr}\\:d\\:2}}\\:%{{eif\\:{ss_expr}\\:d\\:2}}"
            )
            drawtext_remaining = (
                f"drawtext=text='\\ {remaining_time_text}':fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':fontcolor=white:fontsize=19:"
                f"borderw=2:bordercolor=black:x=30:y=h-th-30"
            )
        else:
            drawtext_remaining = (
                f"drawtext=text='\\: Bilinmiyor':fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':fontcolor=white:fontsize=19:"
                f"borderw=2:bordercolor=black:x=30:y=h-th-30"
            )

        filter_steps.append(f'{last_stream}{drawtext_title},{drawtext_remaining}[v]')
        last_stream = '[v]'

        filter_str = ";".join(filter_steps)
        if last_stream != '[v]':
            filter_str += f";{last_stream}null[v]"

        # ============================================================
        # ÇIKIŞ — ses ve görüntü KAYNAKTAN GELDİĞİ GİBİ geçer
        # Ses için hiçbir filter yok, -c:a aac ile sadece encode edilir
        # ============================================================
        command = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'warning',
        ] + input_args + overlay_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]',
            '-map', '0:a:0',                # ← kaynaktaki ilk ses, olduğu gibi
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-pix_fmt', 'yuv420p',
            '-b:v', '2000k',
            '-maxrate', '2000k',
            '-bufsize', '4000k',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-ac', '2',
            '-f', 'flv',
            RTMP_SERVER
        ]

        print("▶ FFmpeg başlatıldı (kaynak ne verirse o)...")
        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )

        last_dashboard_time = time.time()
        current_stream_seconds = 0
        last_lines = []

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if line:
                last_lines.append(line.rstrip())
                if len(last_lines) > 15:
                    last_lines.pop(0)

            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    current_stream_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)

                    now = time.time()
                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        if process.returncode == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor.")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti")
            current_index += 1
            update_local_state(current_index)
        else:
            print(f"⚠️ Yayın koptu (Return Code: {process.returncode})")
            print("---- FFmpeg son satırlar ----")
            for l in last_lines:
                print(l)
            print("-----------------------------")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status=f"🔴 Koptu ({process.returncode})")
            current_index += 1
            update_local_state(current_index)

        print("⚠️ 5 saniye sonra tekrar denenecek...")
        time.sleep(5)


if __name__ == "__main__":
    start_m3u_stream()
