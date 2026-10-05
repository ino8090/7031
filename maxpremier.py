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

# Logo ve yazı opaklık ayarları (0.0 - 1.0 arası)
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# Decoder'ı tek thread'e zorlamak için
DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

# Watchdog: bu kadar saniye boyunca FFmpeg'den ilerleme (time=) gelmezse süreç donmuş kabul edilir.
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))

# Link değişip de aynı film olduğu tespit edildiğinde geriden devam edilecek süre
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))


def format_hms(total_seconds):
    """Saniyeyi SS:DD:SS formatına çevirir."""
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


# ===================== KALICI RTMP YAYINCISI =====================
class RtmpPublisher:
    """
    RTMP sunucusuna SÜREKLİ bağlı kalan tek bir FFmpeg süreci.
    Filmleri kodlayan FFmpeg süreçleri çıktılarını (MPEG-TS) bu sürecin stdin'ine yazar.
    Film değişince sadece kodlayıcı süreç yenilenir; RTMP bağlantısı hiç kapanmaz.
    """

    def __init__(self):
        self.proc = None
        self.started_at = 0.0
        self.tail = deque(maxlen=30)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure_running(self):
        if self.alive():
            return
        self.stop()
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'warning',
            '-fflags', '+genpts',
            '-analyzeduration', '3000000',
            '-probesize', '3000000',
            '-f', 'mpegts', '-i', 'pipe:0',
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            '-flvflags', 'no_duration_filesize',
            '-f', 'flv',
            RTMP_SERVER
        ]
        print(f"📡 Kalıcı RTMP yayıncısı başlatılıyor => {RTMP_SERVER}")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        self.started_at = time.time()
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()

    def _drain(self, proc):
        try:
            for raw in iter(proc.stderr.readline, b''):
                self.tail.append(raw.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass

    def clock(self):
        """Yayıncı başladığından beri geçen gerçek süre (zaman damgası sürekliliği için)."""
        return time.time() - self.started_at

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass
        self.proc = None


publisher = RtmpPublisher()

# Sıradaki filmin süresi, mevcut film oynarken önceden hesaplanır (geçiş boşluğunu kısaltır)
_duration_cache = {}


def probe_url_of(stream_url):
    return stream_url.split(";")[0].strip() if ";" in stream_url else stream_url


def prefetch_duration(stream_url):
    url = probe_url_of(stream_url)
    duration = get_video_duration_ffprobe(url)
    if duration > 0:
        _duration_cache[url] = duration


def get_video_duration_ffprobe(video_url):
    """
    FFprobe ile videonun GERÇEK toplam süresini çeker.
    M3U8 ve HLS akışlarını doğru okuyabilmek için ekstra analiz parametreleri içerir.
    """
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
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=15).decode('utf-8', 'replace').strip()
        # stderr uyarıları (ör. SPS hataları) karışabilir; süre her zaman son satırdadır
        duration = float(output.splitlines()[-1].strip())
        if duration > 0:
            print(f"⏱️ ffprobe ile toplam süre tespit edildi: {duration:.1f} saniye ({format_hms(duration)})")
            return duration
    except Exception as e:
        print(f"⚠️ ffprobe ile süre okunamadı (Canlı yayın veya kısıtlı medya olabilir): {e}")
    return 0.0


def get_local_state():
    """Yerel state dosyasından son durumu okur."""
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
    """Son konumu yerel state dosyasına kaydeder."""
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
    """Şu an oynayan içeriğin adını yazar."""
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ Başlık dosyası yazma hatası: {e}")


def write_remaining_time_file(remaining_seconds):
    """
    Kalan süreyi 'time.txt' dosyasına dinamik olarak yazar.
    FFmpeg drawtext bu dosyayı anlık reload=1 ile okuyacaktır.
    """
    try:
        formatted = format_hms(remaining_seconds)
        # Atomik yazım: drawtext yarım/boş dosya okumasın (takılma/yanıp sönme sebebi)
        with open('time.txt.tmp', 'w', encoding='utf-8') as f:
            f.write(formatted)
        os.replace('time.txt.tmp', 'time.txt')
    except Exception as e:
        print(f"⚠️ Kalan süre dosyası yazma hatası: {e}")


# FFmpeg'den gelen ilerleme bilgisi (sayaç thread'i bunu yumuşatarak kullanır)
_progress = {"pos": 0.0, "wall": 0.0, "started": False}


def countdown_updater(proc, total_duration, initial_value):
    """
    time.txt'yi her saniye düzgün güncelleyen sayaç.
    FFmpeg'in düzensiz gelen time= değerine bağlı kalmaz; son ilerleme noktasından
    gerçek saat ile ilerler, geriye/ileriye sıçramaz.
    """
    shown = initial_value
    last_written = None
    while proc.poll() is None:
        time.sleep(0.2)
        if not _progress["started"]:
            continue
        # Takılma olursa sayaç koşup gitmesin: en fazla 1.5 sn tahmin yürüt
        est = _progress["pos"] + min(time.time() - _progress["wall"], 1.5)
        if total_duration > 0:
            value = max(0, int(total_duration - est + 0.999))
            shown = min(shown, value)      # sadece azalır
        else:
            value = int(est)
            shown = max(shown, value)      # sadece artar
        if shown != last_written:
            write_remaining_time_file(shown)
            last_written = shown


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


def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")
    print(f"🔧 Decoder thread   : {DECODER_THREADS}")

    download_logo()

    current_index, last_seconds, last_url, last_title = get_local_state()

    consecutive_fast_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20
    MAX_RETRY_DELAY_SECONDS = 120

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

        # Videonun Gerçek Toplam Süresini Al (önceden hesaplandıysa cache'ten kullan)
        probe_url = probe_url_of(target_stream_url)
        total_duration_sec = _duration_cache.pop(probe_url, 0.0)
        if total_duration_sec <= 0:
            total_duration_sec = get_video_duration_ffprobe(probe_url)

        # İlk kalan süreyi dosyaya yaz
        initial_remaining = max(0, total_duration_sec - last_seconds) if total_duration_sec > 0 else 0
        write_remaining_time_file(initial_remaining)

        print("=" * 60)
        print("📺 Maxanimasyon Canlı Aktarım Yayını (1080p 25fps - 2500k) Başlatılıyor")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
        print(f"⏱️ Toplam Süre      : {format_hms(total_duration_sec) if total_duration_sec > 0 else 'Bilinmiyor'}")
        print(f"🚀 Hedef RTMP       : {RTMP_SERVER}")

        headers_arg = (
            f"User-Agent: {STREAM_USER_AGENT}\r\n"
            f"Referer: https://vidmody.com/\r\n"
            f"Origin: https://vidmody.com\r\n"
        )

        input_options = [
            '-re',
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

        if ";" in target_stream_url:
            video_url, audio_url = target_stream_url.split(";", 1)
            video_url = video_url.strip()
            audio_url = audio_url.strip()

            input_args = (
                input_options + seek_args + ['-i', video_url] +
                input_options + seek_args + ['-i', audio_url]
            )
            audio_map = ['-map', '1:a:0?']
            logo1_input_index = 2
        else:
            input_args = input_options + seek_args + ['-i', target_stream_url]
            audio_map = ['-map', '0:a:0?']
            logo1_input_index = 1

        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")
        write_step_summary(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo1 = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        # Sağ Alt Köşe: Film Adı
        title_drawtext = (
            f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=19:"
            f"x=w-tw-20:y=h-th-20"
        )

        # Sol Alt Köşe: DİNAMİK KALAN SÜRE
        # Sayaç dosyadan değil, doğrudan videonun kare zamanından (t) hesaplanır.
        # Böylece görüntüyle birebir senkron ve her saniye pürüzsüz ilerler.
        if total_duration_sec > 0:
            base_remaining = max(0.0, total_duration_sec - last_seconds)
            rem_expr = f"ceil(max(0\\,{base_remaining:.3f}-t))"
        else:
            rem_expr = f"floor({float(last_seconds):.3f}+t)"  # süre bilinmiyorsa geçen süre
        time_text = (
            f"%{{eif\\:floor({rem_expr}/3600)\\:d\\:2}}\\:"
            f"%{{eif\\:mod(floor({rem_expr}/60)\\,60)\\:d\\:2}}\\:"
            f"%{{eif\\:mod({rem_expr}\\,60)\\:d\\:2}}"
        )
        time_drawtext = (
            f"drawtext=text='{time_text}':fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=18:"
            f"x=20:y=h-th-20"
        )

        if has_logo1:
            logo_inputs = ['-i', 'logo.png']
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
                'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
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
                'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
                f'[main]{title_drawtext}[tmp2];'
                f'[tmp2]{time_drawtext}[v]'
            )

        # Kalıcı yayıncı ölmüşse (gerçek bir kopma) yeniden başlat
        publisher.ensure_running()

        # Zaman damgaları filmler arasında kesintisiz ilerlesin diye
        # yayıncının başlangıcından beri geçen gerçek süre kadar kaydırılır.
        ts_offset = publisher.clock() + 1.0

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
            '-output_ts_offset', f'{ts_offset:.3f}',
            '-flush_packets', '1',
            '-f', 'mpegts',
            'pipe:1'
        ]

        print("▶ FFmpeg başlatıldı, 1080p 25fps @ 2500k yayın kalıcı yayıncıya iletiliyor...")

        process = subprocess.Popen(
            command,
            stdout=publisher.proc.stdin,   # Çıktı kalıcı yayıncıya akar; bu süreç bitince boru kapanmaz
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        # Sıradaki filmin süresini arka planda önceden hesapla
        if len(playlist) > 0:
            next_item = playlist[(current_index + 1) % len(playlist)]
            threading.Thread(target=prefetch_duration, args=(next_item["url"],), daemon=True).start()

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds
        stderr_tail = deque(maxlen=40)

        last_progress_time = [time.time()]

        def _watchdog(proc=process, progress_ref=last_progress_time):
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                    print(f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS} saniyedir ilerleme yok. Süreç zorla sonlandırılıyor.")
                    try:
                        proc.kill()
                    except Exception as e:
                        print(f"⚠️ Watchdog süreç sonlandırma hatası: {e}")
                    break

        watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        watchdog_thread.start()

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break

            if line:
                stderr_tail.append(line.rstrip())

            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    # FFmpeg'in time= değeri output_ts_offset'i İÇERMEZ, doğrudan oynatılan süredir
                    played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    current_stream_seconds = last_seconds + played_seconds

                    now = time.time()
                    # İlerleme bilgisini sayaç thread'ine ver (time.txt'yi o yazar)
                    _progress["pos"] = current_stream_seconds
                    _progress["wall"] = now
                    _progress["started"] = True
                    last_progress_time[0] = now

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url, film_title)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        if process.returncode == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor (RTMP bağlantısı açık kalıyor).")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
        else:
            if process.returncode == -6:
                print("⚠️ FFmpeg SIGABRT ile çöktü.")
            elif process.returncode == -9:
                print("⚠️ FFmpeg watchdog tarafından donma nedeniyle sonlandırıldı.")
            print(f"⚠️ Yayın koptu (Return Code: {process.returncode}). Aynı saniyeden tekrar denenecek.")
            if stderr_tail:
                print("🧾 FFmpeg son log satırları:")
                for tail_line in stderr_tail:
                    print(f"   {tail_line}")
            if not publisher.alive():
                print("🧾 RTMP yayıncısı kapanmış, son log satırları:")
                for tail_line in publisher.tail:
                    print(f"   {tail_line}")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Bağlantı koptu, tekrar denenecek")

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
        elif process.returncode == 0:
            # Normal film geçişi: bekleme yok, yayıncıya hemen yeni film verilsin
            retry_delay = 0
        else:
            retry_delay = 5

        if retry_delay > 0:
            print(f"⚠️ {retry_delay} saniye sonra tekrar bağlanılıyor...")
            time.sleep(retry_delay)


if __name__ == "__main__":
    try:
        start_m3u_stream()
    except KeyboardInterrupt:
        print("\n🛑 Durduruluyor...")
    finally:
        publisher.stop()
