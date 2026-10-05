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
import socket

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "maxpremier"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

LOCAL_RELAY_PORT = 18888
LOCAL_STREAM_URL = f"http://127.0.0.1:{LOCAL_RELAY_PORT}/live.flv"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/mpremiuum.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787671958979.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "state_maxpremier.json")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

DECODER_THREADS = os.getenv("DECODER_THREADS", "1")
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))


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
        duration = float(output)
        if duration > 0:
            print(f"⏱ ffprobe ile toplam süre tespit edildi: {duration:.1f} saniye ({format_hms(duration)})")
            return duration
    except Exception as e:
        print(f"⚠️ ffprobe ile süre okunamadı: {e}")
    return 0.0


def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        if os.path.getsize(STATE_FILE_NAME) == 0:
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
        print(f"💾 Konum kaydedildi => İndeks: {index}, Saniye: {int(seconds)}")
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")


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


# ===================== DÜZELTİLMİŞ LOCAL RELAY SERVER =====================
class StreamRelayServer:
    def __init__(self, host='127.0.0.1', port=LOCAL_RELAY_PORT):
        self.host = host
        self.port = port
        self.subscribers = []
        self.header_buffer = bytearray()
        self.lock = threading.Lock()
        self.has_stream = False

    def start(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.host, self.port))
        server_socket.listen(10)

        def listen_loop():
            while True:
                client, _ = server_socket.accept()
                threading.Thread(target=self.handle_client, args=(client,), daemon=True).start()

        threading.Thread(target=listen_loop, daemon=True).start()
        print(f"📡 OBS Yerel Akış Sunucusu ({self.host}:{self.port}) Başlatıldı.")

    def handle_client(self, client_socket):
        try:
            request = client_socket.recv(1024).decode('utf-8', errors='ignore')
            if "POST" in request or "PUT" in request:
                # Oynatıcı FFmpeg Buraya Veri Gönderiyor
                client_socket.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
                first_chunk = True
                while True:
                    data = client_socket.recv(8192)
                    if not data:
                        break
                    
                    with self.lock:
                        self.has_stream = True
                        if first_chunk or len(self.header_buffer) < 65536:
                            self.header_buffer.extend(data)
                            first_chunk = False

                        for sub in list(self.subscribers):
                            try:
                                sub.sendall(data)
                            except:
                                if sub in self.subscribers:
                                    self.subscribers.remove(sub)
                
                with self.lock:
                    self.has_stream = False
                    self.header_buffer.clear()

            else:
                # OBS Motoru Buradan Veri Çekiyor
                client_socket.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: video/x-flv\r\n\r\n")
                with self.lock:
                    if len(self.header_buffer) > 0:
                        client_socket.sendall(bytes(self.header_buffer))
                    self.subscribers.append(client_socket)
                
                # Bağlantıyı açık tut
                while self.has_stream:
                    time.sleep(1)
        except:
            pass
        finally:
            with self.lock:
                if client_socket in self.subscribers:
                    self.subscribers.remove(client_socket)
            try:
                client_socket.close()
            except:
                pass


relay_server = StreamRelayServer()

def start_obs_master_stream():
    def obs_runner():
        while True:
            # Akış yerel sunucuya geline kadar bekle
            while not relay_server.has_stream:
                time.sleep(1)

            print("🎛️ OBS Ana Motoru BozzTV'ye Bağlanıyor...")
            
            obs_command = [
                'ffmpeg',
                '-y',
                '-re',
                '-i', LOCAL_STREAM_URL,
                '-c:v', 'copy',
                '-c:a', 'copy',
                '-flvflags', 'no_duration_filesize',
                '-f', 'flv',
                RTMP_SERVER
            ]
            
            proc = subprocess.Popen(obs_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait()
            print("⚠️ OBS Ana Motoru düştü, yeniden bağlanmak için bekleniyor...")
            time.sleep(2)

    threading.Thread(target=obs_runner, daemon=True).start()


# ===================== ANA OYNATICI DÖNGÜSÜ =====================
def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")

    download_logo()
    relay_server.start()
    start_obs_master_stream()

    current_index, last_seconds, last_url, last_title = get_local_state()
    consecutive_fast_failures = 0

    while True:
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
                last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
                print(f"🔄 Link değişti ama film aynı ('{film_title}'). {last_seconds}s'den devam ediliyor.")
            else:
                last_seconds = 0

        last_url = target_stream_url
        last_title = film_title
        write_title_file(film_title)

        probe_url = target_stream_url.split(";")[0].strip() if ";" in target_stream_url else target_stream_url
        total_duration_sec = get_video_duration_ffprobe(probe_url)

        initial_remaining = max(0, total_duration_sec - last_seconds) if total_duration_sec > 0 else 0
        write_remaining_time_file(initial_remaining)

        print("=" * 60)
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")

        headers_arg = f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: https://vidmody.com/\r\n"

        input_options = [
            '-re',
            '-headers', headers_arg,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-err_detect', 'ignore_err',
            '-fflags', '+genpts+discardcorrupt',
            '-thread_queue_size', '1024',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '10000000',
            '-threads', DECODER_THREADS,
        ]

        seek_args = ['-ss', str(last_seconds)] if last_seconds > 0 else []

        if ";" in target_stream_url:
            video_url, audio_url = target_stream_url.split(";", 1)
            input_args = input_options + seek_args + ['-i', video_url.strip()] + input_options + seek_args + ['-i', audio_url.strip()]
            audio_map = ['-map', '1:a:0?']
            logo1_input_index = 2
        else:
            input_args = input_options + seek_args + ['-i', target_stream_url]
            audio_map = ['-map', '0:a:0?']
            logo1_input_index = 1

        has_logo1 = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        title_drawtext = f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':fontcolor=white@{TEXT_OPACITY}:fontsize=19:x=w-tw-20:y=h-th-20"
        time_drawtext = f"drawtext=textfile='time.txt':reload=1:fontfile='{BOLD_FONT_PATH}':fontcolor=white@{TEXT_OPACITY}:fontsize=18:x=20:y=h-th-20"

        if has_logo1:
            logo_inputs = ['-i', 'logo.png']
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
                'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
                f'[{logo1_input_index}:v]scale=-2:85,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
                f'[main][logo1]overlay=50:50[tmp1];[tmp1]{title_drawtext}[tmp2];[tmp2]{time_drawtext}[v]'
            )
        else:
            logo_inputs = []
            filter_str = f'[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];[main]{title_drawtext}[tmp2];[tmp2]{time_drawtext}[v]'

        command = [
            'ffmpeg'
        ] + input_args + logo_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]'
        ] + audio_map + [
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
            f"http://127.0.0.1:{LOCAL_RELAY_PORT}/live.flv"
        ]

        process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)

        last_save_time = time.time()
        current_stream_seconds = last_seconds
        last_progress_time = [time.time()]

        def _watchdog(proc=process, progress_ref=last_progress_time):
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                    print("🚨 Watchdog: Akış dondu, yeniden başlatılıyor.")
                    try:
                        proc.kill()
                    except:
                        pass
                    break

        threading.Thread(target=_watchdog, daemon=True).start()

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

                    if total_duration_sec > 0:
                        write_remaining_time_file(max(0, total_duration_sec - current_stream_seconds))
                    else:
                        write_remaining_time_file(current_stream_seconds)

                    now = time.time()
                    last_progress_time[0] = now

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url, film_title)
                        last_save_time = now

        if process.returncode == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor.")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
        else:
            print(f"⚠️ Oynatıcı koptu (Code: {process.returncode}). Aynı saniyeden tekrar denenecek.")
            duration_this_attempt = current_stream_seconds - last_seconds
            if duration_this_attempt < 20:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0

            if consecutive_fast_failures >= 3:
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

        time.sleep(3)


if __name__ == "__main__":
    start_m3u_stream()
