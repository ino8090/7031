#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import sys
import time
import subprocess
import requests

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "maxyerli"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/yerli2.m3u")
LOGO_URL = os.getenv("LOGO_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787745128505.png")

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "state_yerli.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def format_hms(total_seconds):
    total_seconds = int(total_seconds)
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                idx = data.get("last_index", 0)
                sec = data.get("last_seconds", 0)
                print(f"✅ Yerel state okundu ({STATE_FILE_NAME}) => İndeks: {idx}, Saniye: {sec}")
                return idx, sec
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    else:
        print("ℹ️ Yerel state dosyası bulunamadı, 0'dan başlanıyor.")
    return 0, 0


def update_local_state(index, seconds):
    try:
        data = {"last_index": int(index), "last_seconds": int(seconds)}
        with open(STATE_FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 Konum yerel dosyaya kaydedildi => İndeks: {index}, Saniye: {int(seconds)}")
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


def download_logo():
    try:
        headers = {'User-Agent': STREAM_USER_AGENT}
        response = requests.get(LOGO_URL, headers=headers, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


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
    download_logo()
    current_index, last_seconds = get_local_state()
    consecutive_failures = 0

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            time.sleep(10)
            continue

        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0

        current_item = playlist[current_index]
        target_stream_url = current_item["url"]
        film_title = current_item["title"]

        print("=" * 60)
        print("📺 Maxyerli Canlı Aktarım Yayını Başlatılıyor")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")

        # Referer ve User-Agent Tanımı (Engelleri Aşmak İçin)
        headers_arg = f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: https://gelisitirime.top/\r\n"

        reconnect_args = [
            '-tls_verify', '0',               # SSL doğrulama hatasını yok sayar
            '-headers', headers_arg,
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '15000000'         # 15 saniye yanıt alınamazsa zaman aşımına uğratır
        ]

        if ";" in target_stream_url:
            video_url, audio_url = target_stream_url.split(";", 1)
            video_url, audio_url = video_url.strip(), audio_url.strip()

            input_args = reconnect_args + ['-ss', str(last_seconds), '-re', '-i', video_url] + \
                         reconnect_args + ['-ss', str(last_seconds), '-re', '-i', audio_url]
            audio_map = ['-map', '1:a:0']
            logo_input_index = 2
        else:
            input_args = reconnect_args + ['-ss', str(last_seconds), '-re', '-i', target_stream_url]
            audio_map = ['-map', '0:a?']
            logo_input_index = 1

        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        if has_logo:
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
                'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30[main];'
                f'[{logo_input_index}:v]scale=-2:80[logo];'
                '[main][logo]overlay=55:55[v]'
            )
            logo_input = ['-i', 'logo.png']
        else:
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
                'pad=1920:1080:(oh-ih)/2:black,fps=30[v]'
            )
            logo_input = []

        command = ['ffmpeg'] + input_args + logo_input + [
            '-filter_complex', filter_str,
            '-map', '[v]'
        ] + audio_map + [
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-pix_fmt', 'yuv420p',
            '-r', '30',
            '-b:v', '2000k',
            '-maxrate', '2000k',
            '-bufsize', '4000k',
            '-g', '60',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-f', 'flv',
            RTMP_SERVER
        ]

        process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break

            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    current_stream_seconds = last_seconds + played_seconds

                    now = time.time()
                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        if process.returncode == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor.")
            current_index += 1
            last_seconds = 0
            consecutive_failures = 0
            update_local_state(current_index, 0)
        else:
            print(f"⚠️ Yayın koptu (Return Code: {process.returncode}).")
            
            # Eğer hiç oynatılamadan sürekli 0 saniyede kopuyorsa:
            if current_stream_seconds == last_seconds:
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            # 3 kere ard arda açılamayan video olursa atla
            if consecutive_failures >= 3:
                print("❌ Bu video kaynağına erişilemiyor (Kırık link/IP Engeli). Sıradaki videoya geçiliyor...")
                current_index += 1
                last_seconds = 0
                consecutive_failures = 0
            else:
                last_seconds = current_stream_seconds

            update_local_state(current_index, last_seconds)

        time.sleep(5)


if __name__ == "__main__":
    start_m3u_stream()
