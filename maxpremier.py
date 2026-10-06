#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MİMARİ (RTMP kalıcı):

  [Okuyucu FFmpeg (her film için ayrı)]  --MPEG-TS pipe-->  [Çıkış FFmpeg (HİÇ KAPANMAZ)]  --RTMP-->  Sunucu

- Okuyucu: filmi açar, logo/yazı ekler, x264+AAC ile kodlar, MPEG-TS olarak stdout'a yazar.
- Çıkış: stdin'den MPEG-TS okur, yeniden kodlamadan (-c copy) RTMP'ye gönderir.
- Film bitince sadece okuyucu kapanır; çıkış süreci ve RTMP bağlantısı açık kalır.
- Sadece RTMP/çıkış süreci gerçekten ölürse (sunucu koparsa vb.) yeniden başlatılır.
"""

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


# ===================== KALICI RTMP ÇIKIŞ SÜRECİ =====================
class RtmpOutput:
    """
    Tek bir FFmpeg süreci: stdin'den MPEG-TS alır, RTMP'ye kopyalar.
    Filmler arası geçişte KAPANMAZ.
    """

    def __init__(self, rtmp_url):
        self.rtmp_url = rtmp_url
        self.proc = None
        self.failed = threading.Event()
        self.stderr_tail = deque(maxlen=30)

    def start(self):
        self.stop(force=True)
        self.failed.clear()
        self.stderr_tail.clear()
        cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'warning',
            '-nostats',
            '-fflags', '+genpts+nobuffer',
            '-analyzeduration', '3000000',
            '-probesize', '3000000',
            '-f', 'mpegts',
            '-i', 'pipe:0',
            '-map', '0:v:0',
            '-map', '0:a:0?',
            '-c', 'copy',
            '-flvflags', 'no_duration_filesize',
            '-f', 'flv',
            self.rtmp_url,
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
        print(f"🔌 Kalıcı RTMP çıkış süreci başlatıldı => {self.rtmp_url}")

    def _drain_stderr(self, proc):
        """stderr dolup süreci kilitlemesin diye sürekli okunur."""
        try:
            for raw in iter(proc.stderr.readline, b''):
                self.stderr_tail.append(raw.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def write(self, data):
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            self.failed.set()
            return False

    def stop(self, force=False):
        p = self.proc
        if p is None:
            return
        self.proc = None
        try:
            if force:
                p.kill()
            else:
                p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass


# ===================== YARDIMCI FONKSİYONLAR =====================
def format_hms(total_seconds):
    """Saniyeyi SS:DD:SS formatına çevirir."""
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


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
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=15).decode('utf-8', 'replace')
        duration = 0.0
        # Uyarı satırları karışsa bile çıktıdaki sayısal satırı bul
        for out_line in reversed(output.strip().splitlines()):
            try:
                duration = float(out_line.strip())
                break
            except ValueError:
                continue
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


def iter_stderr_lines(stream):
    """FFmpeg stderr'ini hem \\r hem \\n ile satırlara böler (time= satırları \\r ile gelir)."""
    fd = stream.fileno()
    buf = b''
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            if buf.strip():
                yield buf.decode('utf-8', 'replace')
            return
        buf += chunk
        parts = re.split(rb'[\r\n]', buf)
        buf = parts.pop()
        for p in parts:
            if p:
                yield p.decode('utf-8', 'replace')


def pump_reader_to_output(reader, output):
    """Okuyucunun stdout'unu (MPEG-TS) kalıcı RTMP çıkışına aktarır."""
    fd = reader.stdout.fileno()
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        if not output.write(chunk):
            # Çıkış süreci/RTMP koptu: okuyucuyu durdur, ana döngü çıkışı yeniden başlatacak.
            try:
                reader.kill()
            except Exception:
                pass
            break


# ===================== ANA DÖNGÜ =====================
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

    output = RtmpOutput(RTMP_SERVER)

    try:
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

            # Videonun Gerçek Toplam Süresini Al
            probe_url = target_stream_url.split(";")[0].strip() if ";" in target_stream_url else target_stream_url
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

            # Sol Alt Köşe: GERÇEK KALAN SÜRE (time.txt dosyasından anlık dinamik okunur)
            time_drawtext = (
                f"drawtext=textfile='time.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
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

            # OKUYUCU: kodlar ve MPEG-TS olarak stdout'a yazar (RTMP'ye DOKUNMAZ)
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
                '-flush_packets', '1',
                '-muxdelay', '0',
                '-muxpreload', '0',
                '-mpegts_flags', '+resend_headers',
                '-f', 'mpegts',
                'pipe:1'
            ]

            # Kalıcı RTMP çıkışı ölmüşse (veya ilk açılışsa) başlat
            if not output.alive():
                output.start()

            print("▶ Okuyucu FFmpeg başlatıldı, 1080p 25fps @ 2500k kalıcı RTMP'ye aktarılıyor...")

            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            pump_thread = threading.Thread(
                target=pump_reader_to_output, args=(process, output), daemon=True
            )
            pump_thread.start()

            last_save_time = time.time()
            last_dashboard_time = time.time()
            current_stream_seconds = last_seconds
            stderr_tail = deque(maxlen=40)

            last_progress_time = [time.time()]

            def _watchdog(proc=process, progress_ref=last_progress_time):
                while proc.poll() is None:
                    time.sleep(5)
                    if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                        print(f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS} saniyedir ilerleme yok. Okuyucu zorla sonlandırılıyor.")
                        try:
                            proc.kill()
                        except Exception as e:
                            print(f"⚠️ Watchdog süreç sonlandırma hatası: {e}")
                        break

            watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
            watchdog_thread.start()

            for line in iter_stderr_lines(process.stderr):
                stderr_tail.append(line.rstrip())

                if "time=" in line:
                    time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                    if time_match:
                        hrs, mins, secs = time_match.groups()
                        played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                        current_stream_seconds = last_seconds + played_seconds

                        # Her kare ilerlemesinde KALAN SÜREYİ 'time.txt' dosyasına yaz
                        if total_duration_sec > 0:
                            remaining_seconds = max(0, total_duration_sec - current_stream_seconds)
                            write_remaining_time_file(remaining_seconds)
                        else:
                            # Eğer ffprobe toplam süreyi çekemediyse geçen süreyi gösterir
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

            process.wait()
            pump_thread.join(timeout=15)

            # Çıkış (RTMP) tarafı bozuldu mu? (süreç öldü, pipe koptu ya da yazma takıldı)
            output_broken = output.failed.is_set() or not output.alive() or pump_thread.is_alive()

            if output_broken:
                print("🔴 Kalıcı RTMP çıkışı koptu. Çıkış süreci yeniden başlatılacak, film aynı saniyeden devam edecek.")
                if output.stderr_tail:
                    print("🧾 Çıkış FFmpeg son log satırları:")
                    for tail_line in output.stderr_tail:
                        print(f"   {tail_line}")
                output.stop(force=True)  # bir sonraki turda temiz başlatılır
                write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 RTMP koptu, yeniden bağlanılıyor")
                # Bu bir film hatası değil, hızlı-hata sayacına eklenmez
                last_seconds = current_stream_seconds
                last_url = target_stream_url
                update_local_state(current_index, last_seconds, last_url, film_title)
                retry_delay = 5

            elif process.returncode == 0:
                print("✅ İçerik bitti, sıradakine geçiliyor (RTMP açık kalıyor).")
                write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
                current_index += 1
                last_seconds = 0
                last_url = ""
                last_title = ""
                update_local_state(current_index, 0, "", "")
                consecutive_fast_failures = 0
                retry_delay = 0  # bekleme yok: geçiş en hızlı şekilde olsun

            else:
                if process.returncode == -6:
                    print("⚠️ FFmpeg SIGABRT ile çöktü.")
                elif process.returncode == -9:
                    print("⚠️ Okuyucu FFmpeg watchdog tarafından donma nedeniyle sonlandırıldı.")
                print(f"⚠️ Okuyucu koptu (Return Code: {process.returncode}). Aynı saniyeden tekrar denenecek. (RTMP açık kalıyor)")
                if stderr_tail:
                    print("🧾 FFmpeg son log satırları:")
                    for tail_line in stderr_tail:
                        print(f"   {tail_line}")
                write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Kaynak koptu, tekrar denenecek")

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

            if retry_delay > 0:
                print(f"⚠️ {retry_delay} saniye sonra tekrar bağlanılıyor...")
                time.sleep(retry_delay)

    except KeyboardInterrupt:
        print("\n🛑 Durduruluyor...")
    finally:
        output.stop()


if __name__ == "__main__":
    start_m3u_stream()
