#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "maxyerli"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/yerli2.m3u")
LOGO_URL = os.getenv("LOGO_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787745128505.png")
FLAG_URL = os.getenv("FLAG_URL", "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/file_00000000eae88246b13a221f896ea385.png")

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "state_maxyerli.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def format_hms(total_seconds):
    """Saniyeyi SS:DD:SS formatına çevirir."""
    total_seconds = int(total_seconds)
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def escape_drawtext(text):
    """FFmpeg drawtext filtresi için metindeki özel karakterleri temizler."""
    text = text.replace('\\', '\\\\')
    text = text.replace("'", "'\\\\''")
    text = text.replace(':', '\\:')
    text = text.replace('%', '\\%')
    return text


def get_local_state():
    """Yerel state_maxyerli.json dosyasından son durumu okur."""
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
        print(f"ℹ️ Yerel state dosyası bulunamadı, 0'dan başlanıyor.")
    return 0, 0


def update_local_state(index, seconds):
    """Son konumu yerel state_maxyerli.json dosyasına kaydeder."""
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


def download_assets():
    """Logo ve Türk Bayrağı görsellerini indirir."""
    headers = {'User-Agent': STREAM_USER_AGENT}
    
    # Logo indirme
    try:
        res_logo = requests.get(LOGO_URL, headers=headers, timeout=15)
        if res_logo.status_code == 200 and len(res_logo.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(res_logo.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")

    # Bayrak indirme
    try:
        res_flag = requests.get(FLAG_URL, headers=headers, timeout=15)
        if res_flag.status_code == 200 and len(res_flag.content) > 0:
            with open('flag.png', 'wb') as f:
                f.write(res_flag.content)
            print("✅ Türk Bayrağı başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Bayrak indirme hatası: {e}")


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
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 Kullanılan Bayrak: {FLAG_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")

    download_assets()

    current_index, last_seconds = get_local_state()

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
        print("📺 Maxyerli Canlı Aktarım Yayını (1080p 30fps - 2000k) Başlatılıyor")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
        print(f"🚀 Hedef RTMP       : {RTMP_SERVER}")

        headers_arg = f"User-Agent: {STREAM_USER_AGENT}\r\n"

        # --- ÇİFT LİNK VEYA TEK LİNK KONTROLÜ ---
        if ";" in target_stream_url:
            video_url, audio_url = target_stream_url.split(";", 1)
            video_url = video_url.strip()
            audio_url = audio_url.strip()

            print(f"🎥 Video Bağlantısı : {video_url}")
            print(f"🔊 Ses Bağlantısı   : {audio_url}")

            input_args = [
                '-headers', headers_arg,
                '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
                '-ss', str(last_seconds),
                '-re',
                '-i', video_url,
                '-headers', headers_arg,
                '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
                '-ss', str(last_seconds),
                '-re',
                '-i', audio_url
            ]
            audio_map = ['-map', '1:a:0']
            next_input_index = 2
        else:
            print(f"📡 Kaynak Yayın     : {target_stream_url}")
            input_args = [
                '-headers', headers_arg,
                '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
                '-ss', str(last_seconds),
                '-re',
                '-i', target_stream_url
            ]
            audio_map = ['-map', '0:a?']
            next_input_index = 1

        print("=" * 60)

        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")
        write_step_summary(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0
        has_flag = os.path.exists('flag.png') and os.path.getsize('flag.png') > 0

        overlay_inputs = []
        filter_steps = [
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30[main]'
        ]
        last_stream = '[main]'

        # Logo İşleme (Sol Üst)
        if has_logo:
            logo_idx = next_input_index
            overlay_inputs.extend(['-i', 'logo.png'])
            next_input_index += 1
            filter_steps.append(f'[{logo_idx}:v]scale=-2:80[logo]')
            filter_steps.append(f'{last_stream}[logo]overlay=55:55[v_logo]')
            last_stream = '[v_logo]'

        # Bayrak İşleme (Sağ Üst)
        if has_flag:
            flag_idx = next_input_index
            overlay_inputs.extend(['-i', 'flag.png'])
            next_input_index += 1
            filter_steps.append(f'[{flag_idx}:v]scale=60:-2[flag]')
            # Sağ üst köşe overlay formülü: main_w - overlay_w - 60
            filter_steps.append(f'{last_stream}[flag]overlay=main_w-overlay_w-60:60[v_flag]')
            last_stream = '[v_flag]'

        # Sağ Alt Köşe: Şeffaf Arka Planlı Film Adı (drawtext)
        safe_title = escape_drawtext(film_title)
        drawtext_filter = (
            f"{last_stream}drawtext="
            f"text='{safe_title}':"
            "fontsize=18:"
            "fontcolor=white:"
            "box=0:"
            "shadowcolor=black@0.9:"
            "shadowx=2:"
            "shadowy=2:"
            "x=w-tw-40:"
            "y=h-th-40[v_title]"
        )
        filter_steps.append(drawtext_filter)
        last_stream = '[v_title]'

        # Son filtre çıktısını [v] adıyla tanımlama
        filter_str = ";".join(filter_steps)
        if last_stream != '[v]':
            filter_str += f";{last_stream}null[v]"

        command = [
            'ffmpeg'
        ] + input_args + overlay_inputs + [
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

        print("▶ FFmpeg başlatıldı, 1080p 30fps @ 2000k yayın iletiliyor...")

        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

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
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
            current_index += 1
            last_seconds = 0
            update_local_state(current_index, 0)
        else:
            print(f"⚠️ Yayın koptu (Return Code: {process.returncode}). Aynı saniyeden tekrar denenecek.")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Bağlantı koptu, tekrar denenecek")
            last_seconds = current_stream_seconds
            update_local_state(current_index, last_seconds)

        print("⚠️ 5 saniye sonra tekrar bağlanılıyor...")
        time.sleep(5)


if __name__ == "__main__":
    start_m3u_stream()
