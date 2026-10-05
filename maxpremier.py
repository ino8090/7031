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
import atexit
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

WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))

FIFO_PATH = os.getenv("FIFO_PATH", "/tmp/maxpremier_stream_fifo")


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def get_video_duration_ffprobe(video_url):
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-allowed_extensions', 'ALL',
        '-analyzeduration', '20000000',
        '-probesize', '20000000',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        '-headers', f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: {STREAM_REFERER}\r\n",
        video_url
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=15).decode('utf-8').strip()
        # Sadece son satırdaki sayıyı al (ffmpeg log satırları karışabilir)
        last_line = output.splitlines()[-1].strip()
        duration = float(last_line)
        if duration > 0:
            print(f"⏱️ ffprobe ile toplam süre tespit edildi: {duration:.1f} saniye ({format_hms(duration)})")
            return duration
    except Exception as e:
        print(f"⚠️ ffprobe ile süre okunamadı (Canlı yayın veya kısıtlı medya olabilir): {e}")
    return 0.0


def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        if os.path.getsize(STATE_FILE_NAME) == 0:
            print(f"⚠️ Yerel state dosyası boş ({STATE_FILE_NAME}), 0'dan başlanıyor.")
            return 0, 0, "", ""
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                idx = data.get("last_index", 0)
                sec = data.get("last_seconds", 0)
                url = data.get("last_url", "")
                title = data.get("last_title", "")
                print(f"✅ Yerel state okundu ({STATE_FILE_NAME}) => İndeks: {idx}, Saniye: {sec}")
                return idx, sec, url, title
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    else:
        print(f"ℹ️ Yerel state dosyası bulunamadı, 0'dan başlanıyor.")
    return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):
    try:
        data = {
            "last_index": int(index),
            "last_seconds": int(seconds),
            "last_url": url,
            "last_title": title,
        }
        with open(STATE_FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 Konum yerel dosyaya kaydedildi => İndeks: {index}, Saniye: {int(seconds)}")
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")


def get_m3u_playlist(m3u_url):
    try:
        headers = {
            'User-Agent': STREAM_USER_AGENT,
            'Referer': STREAM_REFERER
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


def write_remaining_time_file(remaining_seconds):
    try:
        formatted = format_hms(remaining_seconds)
        with open('time.txt', 'w', encoding='utf-8') as f:
            f.write(formatted)
    except Exception as e:
        print(f"⚠️ Kalan süre dosyası yazma hatası: {e}")


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
            "## 📺 Canlı Yayın Durumu (Maxanimasyon)\n\n"
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


# ===================== FIFO YÖNETİMİ =====================

def cleanup_fifo():
    try:
        if os.path.exists(FIFO_PATH):
            os.remove(FIFO_PATH)
    except Exception:
        pass


def create_fifo():
    cleanup_fifo()
    try:
        os.mkfifo(FIFO_PATH)
        print(f"✅ FIFO oluşturuldu: {FIFO_PATH}")
    except Exception as e:
        print(f"❌ FIFO oluşturma hatası: {e}")
        sys.exit(1)


atexit.register(cleanup_fifo)


# ===================== ANA FFMPEG (RTMP'YE BASAN) =====================
# FIFO'dan MPEG-TS okur, stream'i COPY ederek RTMP'ye basar.
# Video geçişlerinde BU SÜREÇ ASLA KAPANMAZ.

def start_main_ffmpeg():
    cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'warning',
        '-fflags', '+genpts+igndts+discardcorrupt',
        '-thread_queue_size', '4096',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-f', 'mpegts',
        '-i', FIFO_PATH,
        '-c', 'copy',
        '-flvflags', 'no_duration_filesize',
        '-f', 'flv',
        RTMP_SERVER
    ]
    print(f"🎥 Ana FFmpeg başlatılıyor (RTMP'ye sürekli basacak): {RTMP_SERVER}")
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1
    )
    return proc


# ===================== DECODER FFMPEG (FIFO'YA YAZAN) =====================
# Kaynak videoyu decode eder, logo+title+time overlay uygular,
# H264+AAC olarak MPEG-TS container içinde FIFO'ya yazar.

def build_decoder_command(target_stream_url, last_seconds, has_logo1, audio_only_url=None):
    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: https://vidmody.com/\r\n"
        f"Origin: https://vidmody.com\r\n"
    )

    input_options = [
        '-headers', headers_arg,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-err_detect', 'ignore_err',
        '-fflags', '+genpts+discardcorrupt',
        '-thread_queue_size', '1024',
        '-max_interleave_delta', '0',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-reconnect', '1',
        '-reconnect_at_eof', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '10',
        '-rw_timeout', '10000000',
        '-threads', DECODER_THREADS,
    ]

    seek_args = ['-ss', str(last_seconds)] if last_seconds > 0 else []

    if audio_only_url:
        input_args = (
            input_options + seek_args + ['-i', target_stream_url] +
            input_options + seek_args + ['-i', audio_only_url]
        )
        audio_map = ['-map', '1:a:0?']
        logo1_input_index = 2
    else:
        input_args = input_options + seek_args + ['-i', target_stream_url]
        audio_map = ['-map', '0:a:0?']
        logo1_input_index = 1

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

    if has_logo1:
        logo_inputs = ['-i', 'logo.png']
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25,format=yuv420p[main];'
            f'[{logo1_input_index}:v]scale=-2:85,format=rgba,'
            f'colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
            '[main][logo1]overlay=50:50[tmp1];'
            f'[tmp1]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[v]'
        )
    else:
        logo_inputs = []
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25,format=yuv420p[main];'
            f'[main]{title_drawtext}[tmp2];'
            f'[tmp2]{time_drawtext}[v]'
        )

    # MPEG-TS olarak FIFO'ya yaz. Bu container hem video hem sesi taşır.
    # -re ile gerçek zamanlı pacing yapılır (yayın hızı korunur).
    command = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'info',
        '-re',
    ] + input_args + logo_inputs + [
        '-filter_complex', filter_str,
        '-map', '[v]'
    ] + audio_map + [
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-tune', 'zerolatency',
        '-pix_fmt', 'yuv420p',
        '-r', '25',
        '-g', '50',
        '-b:v', '2500k',
        '-maxrate', '2500k',
        '-bufsize', '3000k',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-ac', '2',
        '-ar', '44100',
        '-muxdelay', '0',
        '-muxpreload', '0',
        '-f', 'mpegts',
        FIFO_PATH
    ]
    return command


# ===================== ANA AKIŞ =====================

def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")
    print(f"🔧 Decoder thread   : {DECODER_THREADS}")
    print(f"🔧 FIFO yolu        : {FIFO_PATH}")

    download_logo()
    create_fifo()

    main_proc = start_main_ffmpeg()

    def _main_logger(proc):
        try:
            for line in iter(proc.stderr.readline, ''):
                if not line:
                    break
                stripped = line.strip()
                if stripped and any(k in stripped for k in ('error', 'Error', 'failed', 'Failed', 'Broken', 'Connection', 'refused')):
                    print(f"[ANA-FFMPEG] {stripped}")
        except Exception:
            pass

    threading.Thread(target=_main_logger, args=(main_proc,), daemon=True).start()

    current_index, last_seconds, last_url, last_title = get_local_state()

    consecutive_fast_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20
    MAX_RETRY_DELAY_SECONDS = 120

    while True:
        # Ana FFmpeg öldüyse yeniden başlat
        if main_proc.poll() is not None:
            print(f"⚠️ Ana FFmpeg beklenmedik şekilde kapandı (rc={main_proc.returncode}). Yeniden başlatılıyor...")
            create_fifo()
            main_proc = start_main_ffmpeg()
            threading.Thread(target=_main_logger, args=(main_proc,), daemon=True).start()

        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            time.sleep(10)
            continue

        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""

        current_item = playlist[current_index]
        target_stream_url = current_item["url"]
        film_title = current_item["title"]

        if last_seconds > 0 and last_url and target_stream_url != last_url:
            if last_title and film_title == last_title:
                old_seconds = last_seconds
                last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
                print(f"🔄 Bu sıradaki ({current_index + 1}) içeriğin linki değişmiş, "
                      f"ancak film aynı ('{film_title}'). {old_seconds}s yerine "
                      f"{last_seconds}s'den devam edilecek.")
            else:
                print(f"🆕 Bu sıradaki ({current_index + 1}) içerik gerçekten değişmiş, baştan başlatılacak.")
                last_seconds = 0

        last_url = target_stream_url
        last_title = film_title

        write_title_file(film_title)

        probe_url = target_stream_url.split(";")[0].strip() if ";" in target_stream_url else target_stream_url
        total_duration_sec = get_video_duration_ffprobe(probe_url)

        initial_remaining = max(0, total_duration_sec - last_seconds) if total_duration_sec > 0 else 0
        write_remaining_time_file(initial_remaining)

        print("=" * 60)
        print("📺 Maxanimasyon Canlı Aktarım Yayını (1080p 25fps - 2500k)")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
        print(f"⏱️ Toplam Süre      : {format_hms(total_duration_sec) if total_duration_sec > 0 else 'Bilinmiyor'}")
        print(f"🚀 Hedef RTMP       : {RTMP_SERVER}")

        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")
        write_step_summary(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo1 = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        if ";" in target_stream_url:
            video_url, audio_url = target_stream_url.split(";", 1)
            video_url = video_url.strip()
            audio_url = audio_url.strip()
            decoder_cmd = build_decoder_command(video_url, last_seconds, has_logo1, audio_url)
        else:
            decoder_cmd = build_decoder_command(target_stream_url, last_seconds, has_logo1, None)

        decoder_proc = subprocess.Popen(
            decoder_cmd,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds
        stderr_tail = deque(maxlen=40)

        last_progress_time = [time.time()]

        def _watchdog(dec_proc=decoder_proc, main_proc_ref=main_proc, progress_ref=last_progress_time):
            while dec_proc.poll() is None:
                time.sleep(5)
                if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                    print(f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS} saniyedir ilerleme yok. Decoder sonlandırılıyor.")
                    try:
                        dec_proc.kill()
                    except Exception as e:
                        print(f"⚠️ Watchdog süreç sonlandırma hatası: {e}")
                    break
                if main_proc_ref.poll() is not None:
                    print("⚠️ Ana FFmpeg öldü, decoder de sonlandırılıyor.")
                    try:
                        dec_proc.kill()
                    except Exception:
                        pass
                    break

        watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        watchdog_thread.start()

        while True:
            line = decoder_proc.stderr.readline()
            if not line and decoder_proc.poll() is not None:
                break

            if line:
                stderr_tail.append(line.rstrip())

            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    current_stream_seconds = last_seconds + played_seconds

                    if total_duration_sec > 0:
                        remaining_seconds = max(0, total_duration_sec - current_stream_seconds)
                        write_remaining_time_file(remaining_seconds)
                    else:
                        write_remaining_time_file(current_stream_seconds)

                    now = time.time()
                    last_progress_time[0] = now

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url, film_title)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        decoder_rc = decoder_proc.returncode

        if decoder_rc == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor. (RTMP KOPMADI - Ana FFmpeg hâlâ yayında)")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
            # FIFO'da buffer kalmış olabilir; kısa bekleme
            time.sleep(1)
            continue
        else:
            if decoder_rc == -6:
                print("⚠️ Decoder SIGABRT ile çöktü.")
            elif decoder_rc == -9:
                print("⚠️ Decoder watchdog tarafından donma nedeniyle sonlandırıldı.")
            print(f"⚠️ Decoder koptu (Return Code: {decoder_rc}). Aynı saniyeden tekrar denenecek.")
            if stderr_tail:
                print("🧾 Decoder son log satırları:")
                for tail_line in stderr_tail:
                    print(f"   {tail_line}")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Decoder koptu, tekrar denenecek")

            duration_this_attempt = current_stream_seconds - last_seconds
            if duration_this_attempt < FAST_FAIL_THRESHOLD_SECONDS:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0

            if consecutive_fast_failures >= 3:
                print(f"❌ {film_title} akışı sürekli hataya düştü. Sonraki içeriğe geçiliyor...")
                current_index += 1
                last_seconds = 0
                last_url = ""
                last_title = ""
                consecutive_fast_failures = 0
                update_local_state(current_index, 0, "", "")
            else:
                last_seconds = current_stream_seconds
                last_url = target_stream_url
                update_local_state(current_index, last_seconds, last_url, film_title)

        if consecutive_fast_failures > 0:
            retry_delay = min(5 * (2 ** consecutive_fast_failures), MAX_RETRY_DELAY_SECONDS)
        else:
            retry_delay = 5

        print(f"⚠️ {retry_delay} saniye sonra decoder tekrar başlatılıyor...")
        time.sleep(retry_delay)


if __name__ == "__main__":
    start_m3u_stream()
