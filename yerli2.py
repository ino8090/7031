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

MAX_FAIL_COUNT = 3
BASE_RETRY_WAIT = 5
MAX_RETRY_WAIT = 30


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
        print(f"ℹ️ Yerel state dosyası bulunamadı, 0'dan başlanıyor.")
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
        headers = {
            'User-Agent': STREAM_USER_AGENT,
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
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
    headers = {'User-Agent': STREAM_USER_AGENT}

    try:
        res_logo = requests.get(LOGO_URL, headers=headers, timeout=15)
        if res_logo.status_code == 200 and len(res_logo.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(res_logo.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")

    try:
        res_flag = requests.get(FLAG_URL, headers=headers, timeout=15)
        if res_flag.status_code == 200 and len(res_flag.content) > 0:
            with open('flag.png', 'wb') as f:
                f.write(res_flag.content)
            print("✅ Türk Bayrağı başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Bayrak indirme hatası: {e}")


def check_stream_alive(url, timeout=10):
    """
    İÇERİĞE BAKARAK test eder.
    Content-Type yanıltıcı olabilir (text/vtt ama içerik #EXTM3U olabilir).
    """
    try:
        headers = {
            'User-Agent': STREAM_USER_AGENT,
            'Accept': '*/*',
        }
        r = requests.get(url, headers=headers, timeout=timeout,
                         stream=True, allow_redirects=True)

        if r.status_code >= 400:
            print(f"❌ HTTP {r.status_code}")
            r.close()
            return False

        ct = (r.headers.get('Content-Type') or '').lower()
        print(f"📋 Content-Type: {ct}")

        # İlk 4KB'ı oku — içerik gerçekte ne?
        chunk = r.raw.read(4096, decode_content=True)
        r.close()

        if not chunk:
            print("❌ Boş içerik")
            return False

        # İçeriği metin olarak dene
        text = chunk.decode('utf-8', errors='ignore')

        # ✅ GERÇEK HLS KONTROLÜ (Content-Type'a güvenme!)
        if text.lstrip().startswith('#EXTM3U'):
            if '#EXT-X-STREAM-INF' in text:
                print("✅ Master HLS playlist (video)")
                return True
            if '#EXTINF' in text:
                seg_count = text.count('#EXTINF')
                print(f"✅ Media HLS playlist — ilk 4KB'da {seg_count} segment")
                return True
            print("⚠️ #EXTM3U var ama segment/master yok")
            return True

        # ✅ MPEG-TS sync byte (0x47)
        if chunk[0] == 0x47:
            print("✅ MPEG-TS stream tespit edildi")
            return True

        # ✅ MP4/ISOBMFF
        if any(m in chunk[:64] for m in (b'ftyp', b'moov', b'styp')):
            print("✅ MP4/ISOBMFF tespit edildi")
            return True

        # ❌ Gerçek WebVTT altyazı
        if text.lstrip().startswith('WEBVTT'):
            print("❌ Gerçek WebVTT altyazı — video değil")
            return False

        # ❌ HTML/JSON
        stripped = text.lstrip()
        if (stripped.startswith('<!') or stripped.startswith('<html')
            or stripped.startswith('{"')):
            print(f"❌ HTML/JSON: {stripped[:100]}")
            return False

        # Content-Type'da video/audio var mı?
        if any(k in ct for k in ('video/', 'audio/', 'octet-stream', 'mp2t')):
            print(f"✅ Content-Type video/audio")
            return True

        print(f"⚠️ Bilinmeyen içerik. İlk 100 byte: {text[:100]!r}")
        return False

    except Exception as e:
        print(f"⚠️ Test hatası: {e}")
        return False


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


def build_ffmpeg_input_args(target_stream_url, headers_arg, last_seconds):
    """
    A Yöntemi: HLS M3U8 URL'sini FFmpeg'e doğrudan ver.
    Tüm segment uzantılarına ve protokollere izin ver.
    Çift link (video;audio) desteği korunur.
    """
    if ";" in target_stream_url:
        video_url, audio_url = target_stream_url.split(";", 1)
        video_url = video_url.strip()
        audio_url = audio_url.strip()

        print(f"🎥 Video Bağlantısı : {video_url[:90]}...")
        print(f"🔊 Ses Bağlantısı   : {audio_url[:90]}...")

        input_args = [
            '-headers', headers_arg,
            '-user_agent', STREAM_USER_AGENT,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-allowed_extensions', 'ALL',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '20000000',
            '-ss', str(last_seconds),
            '-i', video_url,
            '-headers', headers_arg,
            '-user_agent', STREAM_USER_AGENT,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-allowed_extensions', 'ALL',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '20000000',
            '-ss', str(last_seconds),
            '-i', audio_url
        ]
        audio_map = ['-map', '1:a:0']
        next_input_index = 2
    else:
        print(f"📡 Kaynak Yayın     : {target_stream_url[:90]}...")
        input_args = [
            '-headers', headers_arg,
            '-user_agent', STREAM_USER_AGENT,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-allowed_extensions', 'ALL',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '20000000',
            '-ss', str(last_seconds),
            '-i', target_stream_url
        ]
        audio_map = ['-map', '0:a?']
        next_input_index = 1

    return input_args, audio_map, next_input_index


def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 Kullanılan Bayrak: {FLAG_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")

    download_assets()

    current_index, last_seconds = get_local_state()
    fail_count = 0

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            print("⚠️ Playlist boş, 10 sn sonra tekrar denenecek...")
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

        # Kaynak canlılık testi (İÇERİĞE bakar)
        test_url = target_stream_url.split(';')[0].strip()
        if not check_stream_alive(test_url):
            print("❌ Kaynak bozuk (video değil). Sıradaki içeriğe geçiliyor.")
            write_step_summary(film_title, current_index, len(playlist),
                               last_seconds, status="🔴 Kaynak bozuk, atlandı")
            current_index += 1
            last_seconds = 0
            update_local_state(current_index, 0)
            fail_count = 0
            time.sleep(2)
            continue

        input_args, audio_map, next_input_index = build_ffmpeg_input_args(
            target_stream_url, headers_arg, last_seconds
        )

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

        if has_logo:
            logo_idx = next_input_index
            overlay_inputs.extend(['-i', 'logo.png'])
            next_input_index += 1
            filter_steps.append(f'[{logo_idx}:v]scale=-2:80[logo]')
            filter_steps.append(f'{last_stream}[logo]overlay=55:55[v_logo]')
            last_stream = '[v_logo]'

        if has_flag:
            flag_idx = next_input_index
            overlay_inputs.extend(['-i', 'flag.png'])
            next_input_index += 1
            filter_steps.append(f'[{flag_idx}:v]scale=60:-2[flag]')
            filter_steps.append(f'{last_stream}[flag]overlay=main_w-overlay_w-60:60[v_flag]')
            last_stream = '[v_flag]'

        filter_str = ";".join(filter_steps)
        if last_stream != '[v]':
            filter_str += f";{last_stream}null[v]"

        command = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'warning'
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
            stdout=subprocess.DEVNULL,
            universal_newlines=True,
            bufsize=1
        )

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds
        error_lines = []
        input_error_detected = False

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if not line:
                continue

            stripped = line.strip()

            if ("Invalid data found" in stripped
                or "Error opening input" in stripped
                or "No such file" in stripped):
                input_error_detected = True

            if any(k in stripped for k in (
                "error", "Error", "ERROR", "failed", "Failed", "FAILED",
                "Connection", "refused", "timeout", "Timed out",
                "Unauthorized", "Forbidden", "403", "404", "401", "500",
                "Server error", "Invalid", "cannot", "Cannot",
                "No such", "not found", "Broken pipe"
            )):
                print(f"🛑 FFMPEG: {stripped}")
                error_lines.append(stripped)
                if len(error_lines) > 50:
                    error_lines.pop(0)

            if "time=" in stripped:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', stripped)
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

        returncode = process.returncode

        if returncode == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor.")
            write_step_summary(film_title, current_index, len(playlist),
                               current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
            current_index += 1
            last_seconds = 0
            update_local_state(current_index, 0)
            fail_count = 0
            time.sleep(2)
            continue

        # --- HATA DURUMU ---
        fail_count += 1
        print(f"⚠️ Yayın koptu (Return Code: {returncode}). Deneme: {fail_count}/{MAX_FAIL_COUNT}")

        if error_lines:
            print("🔎 Son FFmpeg hata satırları:")
            for el in error_lines[-5:]:
                print(f"   » {el}")

        # Kaynak hatası (183 = input açılamadı) → beklemeden sıradakine geç
        source_error = (
            input_error_detected
            or returncode == 183
            or any('Invalid data' in el for el in error_lines)
            or any('Error opening input' in el for el in error_lines)
        )

        if source_error:
            print("❌ Kaynak bozuk. Sıradaki içeriğe geçiliyor.")
            write_step_summary(film_title, current_index, len(playlist),
                               last_seconds, status="🔴 Kaynak bozuk, atlandı")
            current_index += 1
            last_seconds = 0
            update_local_state(current_index, 0)
            fail_count = 0
            time.sleep(2)
            continue

        write_step_summary(film_title, current_index, len(playlist),
                           current_stream_seconds,
                           status=f"🔴 Koptu (RC:{returncode}), deneme {fail_count}/{MAX_FAIL_COUNT}")

        if fail_count >= MAX_FAIL_COUNT:
            print(f"❌ {MAX_FAIL_COUNT} kez üst üste başarısız. Sıradaki içeriğe geçiliyor.")
            current_index += 1
            last_seconds = 0
            update_local_state(current_index, 0)
            fail_count = 0
            time.sleep(3)
        else:
            last_seconds = current_stream_seconds
            update_local_state(current_index, last_seconds)
            wait = min(BASE_RETRY_WAIT * fail_count, MAX_RETRY_WAIT)
            print(f"⚠️ {wait} saniye sonra tekrar bağlanılıyor...")
            time.sleep(wait)


if __name__ == "__main__":
    try:
        start_m3u_stream()
    except KeyboardInterrupt:
        print("\n⏹️ Kullanıcı tarafından durduruldu.")
        sys.exit(0)
