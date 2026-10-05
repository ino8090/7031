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

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "maxpremier"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/mpremiuum.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787671958979.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "maxpremier.json")
CONCAT_FILE_NAME = "playlist_concat.txt"

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


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
    return []


def generate_concat_file(playlist):
    try:
        with open(CONCAT_FILE_NAME, "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            for item in playlist:
                clean_url = item["url"].replace("'", "'\\''")
                f.write(f"file '{clean_url}'\n")
        print(f"📝 Concat oynatma listesi oluşturuldu: {len(playlist)} video eklendi.")
    except Exception as e:
        print(f"⚠️ Concat dosyası yazma hatası: {e}")


def download_logo():
    headers = {'User-Agent': STREAM_USER_AGENT}
    try:
        response = requests.get(LOGO_URL, headers=headers, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def write_title_file(title):
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ Başlık dosyası yazma hatası: {e}")


def write_remaining_time_file(seconds):
    try:
        formatted = format_hms(seconds)
        with open('time.txt', 'w', encoding='utf-8') as f:
            f.write(formatted)
    except Exception as e:
        print(f"⚠️ Süre dosyası yazma hatası: {e}")


def start_seamless_stream():
    print(f"🔧 M3U URL       : {M3U_URL}")
    print(f"🔧 RTMP Hedefi   : {RTMP_SERVER}")
    print(f"🚀 OBS Modu (Kopmasız Concat Akışı) Başlatılıyor...")

    download_logo()
    playlist = get_m3u_playlist(M3U_URL)

    if not playlist:
        print("❌ M3U listesi boş veya alınamadı! Çıkılıyor.")
        return

    generate_concat_file(playlist)

    write_title_file(playlist[0]["title"])
    write_remaining_time_file(0)

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

    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
    )

    if has_logo:
        logo_inputs = ['-i', 'logo.png']
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
            f'[1:v]scale=-2:85,format=rgba,'
            f'colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
            '[main][logo1]overlay=50:50[tmp1];'
            f'[tmp1]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[v]'
        )
    else:
        logo_inputs = []
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
            f'[main]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[v]'
        )

    # Düzeltilmiş ve Temizlenmiş FFmpeg Komutu
    command = [
        'ffmpeg',
        '-re',
        '-headers', headers_arg,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto,concat',
        '-f', 'concat',
        '-safe', '0',
        '-stream_loop', '-1',
        '-i', CONCAT_FILE_NAME,
    ] + logo_inputs + [
        '-filter_complex', filter_str,
        '-map', '[v]',
        '-map', '0:a:0?',
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

    print("▶ FFmpeg tek oturum canlı aktarımı başlatıldı...")

    process = subprocess.Popen(
        command,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        universal_newlines=True
    )

    last_progress_time = [time.time()]

    def _watchdog(proc=process, progress_ref=last_progress_time):
        while proc.poll() is None:
            time.sleep(5)
            if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                print(f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS} saniyedir ilerleme yok. Kapatılıyor.")
                try:
                    proc.kill()
                except Exception:
                    pass
                break

    threading.Thread(target=_watchdog, daemon=True).start()

    while True:
        line = process.stderr.readline()
        if not line and process.poll() is not None:
            print(f"⚠️ FFmpeg durdu. Çıkış Kodu: {process.returncode}")
            break

        if "time=" in line:
            time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if time_match:
                hrs, mins, secs = time_match.groups()
                played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                write_remaining_time_file(played_seconds)
                last_progress_time[0] = time.time()
        elif "Error" in line or "error" in line or "Failed" in line:
            print(f"🔴 FFmpeg Hatası: {line.strip()}")


if __name__ == "__main__":
    while True:
        try:
            start_seamless_stream()
        except Exception as e:
            print(f"⚠️ Yayın hatası: {e}")
        print("🔄 Yayın düştü, 5 saniye sonra bağlantı sıfırlanıp tekrar başlatılıyor...")
        time.sleep(5)
