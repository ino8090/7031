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

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def format_hms(total_seconds):
    total_seconds = max(0, int(float(total_seconds)))
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
            pending_duration = -1
            
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF'):
                    # Süre ve Başlığı Çek
                    dur_match = re.search(r'#EXTINF:(-?\d+)', line)
                    if dur_match:
                        pending_duration = int(dur_match.group(1))
                    
                    title_match = re.search(r',(.+)$', line)
                    pending_title = title_match.group(1).strip() if title_match else None
                elif not line.startswith('#') and line.startswith('http'):
                    title = pending_title or os.path.basename(line.split('?')[0])
                    playlist.append({
                        "url": line, 
                        "title": title,
                        "duration": pending_duration
                    })
                    pending_title = None
                    pending_duration = -1
            return playlist
    except Exception as e:
        print(f"⚠️ M3U çekme hatası: {e}")
    return []


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
    print(f"🚀 OBS Modu (Kesintisiz Pipe Akışı) Başlatılıyor...\n")

    download_logo()
    playlist = get_m3u_playlist(M3U_URL)
    total_videos = len(playlist)

    if total_videos == 0:
        print("❌ M3U listesi boş veya alınamadı! Çıkılıyor.")
        return

    print(f"📝 Toplam {total_videos} video listelendi.\n")

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

    # 1. Ana RTMP Yayıncısı (Sunucuya tek bir canlı bağlantı açar)
    output_cmd = [
        'ffmpeg',
        '-re',
        '-f', 'mpegts',
        '-i', 'pipe:0',
    ]

    if has_logo:
        output_cmd += ['-i', 'logo.png']
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
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
            f'[main]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[v]'
        )

    output_cmd += [
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

    rtmp_proc = subprocess.Popen(output_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    # 2. Videoları Sırayla Oynat ve Log Ekranına Süreyi/Sırayı Bas
    current_index = 0
    while rtmp_proc.poll() is None:
        if current_index >= total_videos:
            current_index = 0  # Liste bitince tekrar 1. videoya dön

        item = playlist[current_index]
        video_url = item["url"]
        title = item["title"]
        m3u_dur = item.get("duration", -1)

        write_title_file(title)

        input_cmd = [
            'ffmpeg',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
            '-headers', headers_arg,
            '-i', video_url,
            '-c:v', 'mpeg2video',
            '-b:v', '3000k',
            '-c:a', 'mp2',
            '-b:a', '128k',
            '-f', 'mpegts',
            'pipe:1'
        ]

        dec_proc = subprocess.Popen(input_cmd, stdout=rtmp_proc.stdin, stderr=subprocess.PIPE, universal_newlines=True)

        total_duration_secs = m3u_dur if m3u_dur > 0 else None

        print(f"\n▶ [{current_index + 1}/{total_videos}] Film: {title}")

        # FFmpeg stderr takibi ve canlı log basma
        while dec_proc.poll() is None:
            line = dec_proc.stderr.readline()
            
            # Eğer M3U'da süre yoksa FFmpeg başlığından çek
            if not total_duration_secs and "Duration:" in line:
                dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', line)
                if dur_match:
                    hrs, mins, secs = dur_match.groups()
                    total_duration_secs = int(hrs) * 3600 + int(mins) * 60 + float(secs)

            # Anlık oynatılan süre loglama
            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    curr_secs = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    
                    write_remaining_time_file(curr_secs)

                    played_str = format_hms(curr_secs)
                    if total_duration_secs:
                        total_str = format_hms(total_duration_secs)
                        progress = min(100.0, (curr_secs / total_duration_secs) * 100)
                        log_msg = f"\r⏳ Sıra: [{current_index + 1}/{total_videos}] | Film: {title[:30]}... | Süre: {played_str} / {total_str} (%{progress:.1f})"
                    else:
                        log_msg = f"\r⏳ Sıra: [{current_index + 1}/{total_videos}] | Film: {title[:30]}... | Oynatılan: {played_str}"
                    
                    sys.stdout.write(log_msg)
                    sys.stdout.flush()

        print(f"\n✅ [{current_index + 1}/{total_videos}] {title} bitti. Sonraki videoya geçiliyor...")
        current_index += 1

if __name__ == "__main__":
    while True:
        try:
            start_seamless_stream()
        except Exception as e:
            print(f"\n⚠️ Yayın hatası: {e}")
        print("\n🔄 Yayın düştü, 5 saniye sonra bağlantı sıfırlanıp tekrar başlatılıyor...")
        time.sleep(5)
