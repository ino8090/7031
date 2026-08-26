#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import re
import json
import requests


# ============================================================
# AYARLAR
# ============================================================

RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"

STREAM_KEY = os.getenv("STREAM_KEY") or "maxnimasyon"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"


# M3U
M3U_URL = os.getenv("M3U_URL") or (
    "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/Maxç.m3u"
)


# LOGO
LOGO_URL = os.getenv("LOGO_URL") or (
    "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1787712844266.png"
)


# ============================================================
# GITHUB GIST
# ============================================================

GIST_ID = "34df90330e4b0daeed9a5b516c1c368d"

GH_TOKEN = os.getenv("GH_TOKEN", "").strip()

STATE_FILE_NAME = os.getenv(
    "STATE_FILE_NAME",
    "state_animasyon.json"
)

GITHUB_STEP_SUMMARY = os.getenv(
    "GITHUB_STEP_SUMMARY"
)


# ============================================================
# USER AGENT
# ============================================================

STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# GITHUB API HEADER
# ============================================================

def github_headers():

    if not GH_TOKEN:
        return {}

    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "MaxAnimasyon-Gist-Bot"
    }


# ============================================================
# SÜRE
# ============================================================

def format_hms(total_seconds):

    total_seconds = int(total_seconds)

    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


# ============================================================
# GIST OKUMA
# ============================================================

def get_gist_state():

    if not GIST_ID:
        print("⚠️ GIST_ID tanımlı değil!")
        return 0, 0

    if not GH_TOKEN:
        print("❌ GH_TOKEN bulunamadı!")
        return 0, 0

    try:

        url = f"https://api.github.com/gists/{GIST_ID}"

        response = requests.get(
            url,
            headers=github_headers(),
            timeout=15
        )

        if response.status_code == 200:

            files = response.json().get("files", {})

            if STATE_FILE_NAME not in files:

                print(
                    f"⚠️ Gist'te "
                    f"'{STATE_FILE_NAME}' bulunamadı."
                )

                print(
                    "ℹ️ Yayın 0'dan başlayacak."
                )

                return 0, 0

            content = files[
                STATE_FILE_NAME
            ].get("content", "")

            try:

                data = json.loads(content)

            except json.JSONDecodeError:

                print(
                    "⚠️ Gist state dosyası geçersiz JSON."
                )

                return 0, 0

            index = int(
                data.get("last_index", 0)
            )

            seconds = int(
                data.get("last_seconds", 0)
            )

            print(
                f"✅ Gist başarıyla okundu "
                f"({STATE_FILE_NAME}) -> "
                f"İndeks: {index}, "
                f"Saniye: {seconds}"
            )

            return index, seconds

        print(
            f"❌ Gist okuma başarısız! "
            f"HTTP: {response.status_code}"
        )

        try:

            print(
                json.dumps(
                    response.json(),
                    ensure_ascii=False,
                    indent=2
                )
            )

        except Exception:

            print(
                response.text[:1000]
            )

    except Exception as error:

        print(
            f"⚠️ Gist okuma hatası: {error}"
        )

    return 0, 0


# ============================================================
# GIST GÜNCELLEME
# ============================================================

def update_gist_state(index, seconds):

    if not GIST_ID:

        print(
            "⚠️ GIST_ID tanımlı değil!"
        )

        return False

    if not GH_TOKEN:

        print(
            "❌ GH_TOKEN bulunamadı!"
        )

        return False

    try:

        url = f"https://api.github.com/gists/{GIST_ID}"

        payload = {
            "files": {
                STATE_FILE_NAME: {
                    "content": json.dumps(
                        {
                            "last_index": int(index),
                            "last_seconds": int(seconds)
                        },
                        ensure_ascii=False
                    )
                }
            }
        }

        response = requests.patch(
            url,
            headers=github_headers(),
            json=payload,
            timeout=15
        )

        if response.status_code == 200:

            print(
                f"💾 Konum Gist'e kaydedildi "
                f"({STATE_FILE_NAME}) -> "
                f"İndeks: {index}, "
                f"Saniye: {int(seconds)}"
            )

            return True

        print(
            f"⚠️ Gist güncelleme hatası "
            f"HTTP: {response.status_code}"
        )

        try:

            error_data = response.json()

            print(
                "🔎 GitHub API mesajı:"
            )

            print(
                json.dumps(
                    error_data,
                    ensure_ascii=False,
                    indent=2
                )
            )

        except Exception:

            print(
                f"🔎 GitHub cevabı: "
                f"{response.text[:1000]}"
            )

        if response.status_code == 401:

            print(
                "❌ 401: Token geçersiz."
            )

        elif response.status_code == 403:

            print(
                "❌ 403: Token'ın Gist yazma "
                "yetkisi yok olabilir."
            )

        return False

    except Exception as error:

        print(
            f"⚠️ Gist güncelleme hatası: {error}"
        )

        return False


# ============================================================
# M3U PLAYLIST
# ============================================================

def get_m3u_playlist(m3u_url):

    if not m3u_url:

        print(
            "❌ M3U URL boş!"
        )

        return []

    try:

        headers = {
            "User-Agent": STREAM_USER_AGENT
        }

        response = requests.get(
            m3u_url,
            headers=headers,
            timeout=20
        )

        if response.status_code != 200:

            print(
                f"⚠️ M3U HTTP hatası: "
                f"{response.status_code}"
            )

            return []

        lines = response.text.splitlines()

        playlist = []

        pending_title = None

        for raw_line in lines:

            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("#EXTINF"):

                match = re.search(
                    r",(.+)$",
                    line
                )

                if match:
                    pending_title = match.group(1).strip()
                else:
                    pending_title = None

            elif (
                not line.startswith("#")
                and line.startswith("http")
            ):

                title = (
                    pending_title
                    or os.path.basename(
                        line.split("?")[0]
                    )
                )

                playlist.append(
                    {
                        "url": line,
                        "title": title
                    }
                )

                pending_title = None

        print(
            f"✅ M3U playlist alındı: "
            f"{len(playlist)} film"
        )

        return playlist

    except Exception as error:

        print(
            f"⚠️ M3U çekme hatası: {error}"
        )

        return []


# ============================================================
# LOGO
# ============================================================

def download_logo():

    if not LOGO_URL:

        print(
            "⚠️ Logo URL boş."
        )

        return False

    try:

        headers = {
            "User-Agent": STREAM_USER_AGENT
        }

        response = requests.get(
            LOGO_URL,
            headers=headers,
            timeout=20
        )

        if (
            response.status_code == 200
            and response.content
        ):

            with open(
                "logo.png",
                "wb"
            ) as file:

                file.write(
                    response.content
                )

            print(
                "✅ Logo başarıyla indirildi."
            )

            return True

        print(
            f"⚠️ Logo indirilemedi. "
            f"HTTP: {response.status_code}"
        )

    except Exception as error:

        print(
            f"⚠️ Logo indirme hatası: {error}"
        )

    return False


# ============================================================
# DASHBOARD
# ============================================================

def print_dashboard(
    title,
    index,
    playlist_len,
    seconds,
    status="🟢 Yayında"
):

    print(
        "┌" + "─" * 58 + "┐"
    )

    print(
        f"│ 🎬 Film           : "
        f"{title[:36]:<36} │"
    )

    print(
        f"│ 🔢 Sıra           : "
        f"{index + 1}/{playlist_len:<32} │"
    )

    print(
        f"│ ⏱️  Geçen Süre     : "
        f"{format_hms(seconds):<36} │"
    )

    print(
        f"│ 📡 Durum          : "
        f"{status:<36} │"
    )

    print(
        "└" + "─" * 58 + "┘"
    )


# ============================================================
# GITHUB ACTIONS SUMMARY
# ============================================================

def write_step_summary(
    title,
    index,
    playlist_len,
    seconds,
    status="🟢 Yayında"
):

    if not GITHUB_STEP_SUMMARY:
        return

    try:

        content = (
            "## 📺 Canlı Yayın Durumu\n\n"
            "| Alan | Değer |\n"
            "|---|---|\n"
            f"| 🎬 Şu an oynayan film | {title} |\n"
            f"| 🔢 Playlist sırası | "
            f"{index + 1} / {playlist_len} |\n"
            f"| ⏱️ Geçen süre | "
            f"{format_hms(seconds)} |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 🕒 Son güncelleme | "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )

        with open(
            GITHUB_STEP_SUMMARY,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(content)

    except Exception as error:

        print(
            f"⚠️ Step summary yazma hatası: "
            f"{error}"
        )


# ============================================================
# ANA YAYIN
# ============================================================

def start_m3u_stream():

    print("=" * 60)
    print("📺 MAX ANİMASYON YAYINI")
    print("=" * 60)

    print(
        f"🔧 M3U       : {M3U_URL}"
    )

    print(
        f"🔧 Logo      : {LOGO_URL}"
    )

    print(
        f"🔧 Gist ID   : {GIST_ID}"
    )

    print(
        f"🔧 State     : {STATE_FILE_NAME}"
    )

    print(
        f"🔧 RTMP      : {RTMP_SERVER}"
    )

    print(
        "🔐 GH_TOKEN  : "
        + (
            "✅ Mevcut"
            if GH_TOKEN
            else "❌ YOK"
        )
    )

    print("=" * 60)


    # ========================================================
    # LOGO
    # ========================================================

    download_logo()


    # ========================================================
    # GIST'TEN SON KONUM
    # ========================================================

    current_index, last_seconds = (
        get_gist_state()
    )

    print(
        f"▶ Başlangıç indeksi: "
        f"{current_index}"
    )

    print(
        f"▶ Başlangıç saniyesi: "
        f"{last_seconds}"
    )


    # ========================================================
    # ANA DÖNGÜ
    # ========================================================

    while True:

        playlist = get_m3u_playlist(
            M3U_URL
        )

        if not playlist:

            print(
                "⚠️ Playlist boş."
            )

            print(
                "⚠️ 10 saniye sonra "
                "tekrar denenecek."
            )

            time.sleep(10)

            continue


        # ====================================================
        # INDEX KONTROL
        # ====================================================

        if current_index >= len(playlist):

            current_index = 0

            last_seconds = 0

            update_gist_state(
                current_index,
                last_seconds
            )


        # ====================================================
        # MEVCUT FİLM
        # ====================================================

        current_item = playlist[
            current_index
        ]

        target_stream_url = (
            current_item["url"]
        )

        film_title = (
            current_item["title"]
        )


        print("=" * 60)

        print(
            f"🎬 Oynatılan Film : "
            f"{film_title}"
        )

        print(
            f"🔢 Sıra           : "
            f"{current_index + 1}/"
            f"{len(playlist)}"
        )

        print(
            f"📡 Kaynak         : "
            f"{target_stream_url}"
        )

        print(
            f"⏱️ Başlangıç      : "
            f"{last_seconds} saniye"
        )

        print(
            f"🚀 RTMP           : "
            f"{RTMP_SERVER}"
        )

        print("=" * 60)


        print_dashboard(
            film_title,
            current_index,
            len(playlist),
            last_seconds,
            status="🟡 Başlatılıyor"
        )


        write_step_summary(
            film_title,
            current_index,
            len(playlist),
            last_seconds,
            status="🟡 Başlatılıyor"
        )


        # ====================================================
        # LOGO
        # ====================================================

        has_logo = (
            os.path.exists("logo.png")
            and os.path.getsize("logo.png") > 0
        )


        # ====================================================
        # FFMPEG FILTER
        # ====================================================

        if has_logo:

            filter_str = (
                "[0:v]"
                "scale=1920:1080:"
                "force_original_aspect_ratio=decrease,"
                "pad=1920:1080:"
                "(ow-iw)/2:(oh-ih)/2:black,"
                "fps=30[main];"
                "[1:v]scale=-2:100[logo];"
                "[main][logo]"
                "overlay=55:55[v]"
            )

            logo_input = [
                "-i",
                "logo.png"
            ]

        else:

            filter_str = (
                "[0:v]"
                "scale=1920:1080:"
                "force_original_aspect_ratio=decrease,"
                "pad=1920:1080:"
                "(ow-iw)/2:(oh-ih)/2:black,"
                "fps=30[v]"
            )

            logo_input = []


        # ====================================================
        # HTTP HEADER
        # ====================================================

        headers_arg = (
            f"User-Agent: "
            f"{STREAM_USER_AGENT}\r\n"
        )


        # ====================================================
        # FFMPEG COMMAND
        # ====================================================

        command = [

            "ffmpeg",

            "-headers",
            headers_arg,

            "-ss",
            str(last_seconds),

            "-re",

            "-i",
            target_stream_url

        ] + logo_input + [

            "-filter_complex",
            filter_str,

            "-map",
            "[v]",

            "-map",
            "0:a?",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-pix_fmt",
            "yuv420p",

            "-r",
            "30",

            "-b:v",
            "2000k",

            "-maxrate",
            "2000k",

            "-bufsize",
            "4000k",

            "-g",
            "60",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-ar",
            "44100",

            "-f",
            "flv",

            RTMP_SERVER
        ]


        print(
            "▶ FFmpeg başlatıldı..."
        )


        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )


        last_save_time = time.time()

        last_dashboard_time = time.time()

        current_stream_seconds = (
            last_seconds
        )


        # ====================================================
        # FFMPEG TAKİP
        # ====================================================

        while True:

            line = (
                process.stderr.readline()
            )

            if (
                not line
                and process.poll() is not None
            ):
                break


            if "time=" not in line:
                continue


            time_match = re.search(
                r"time=(\d+):(\d+):(\d+(?:\.\d+)?)",
                line
            )


            if not time_match:
                continue


            hrs, mins, secs = (
                time_match.groups()
            )


            played_seconds = (

                int(hrs) * 3600

                + int(mins) * 60

                + float(secs)
            )


            current_stream_seconds = (
                last_seconds
                + played_seconds
            )


            now = time.time()


            # =================================================
            # HER 15 SANİYE GIST
            # =================================================

            if (
                now - last_save_time
                >= 15
            ):

                update_gist_state(
                    current_index,
                    current_stream_seconds
                )

                last_save_time = now


            # =================================================
            # HER 30 SANİYE DASHBOARD
            # =================================================

            if (
                now - last_dashboard_time
                >= 30
            ):

                print_dashboard(
                    film_title,
                    current_index,
                    len(playlist),
                    current_stream_seconds
                )

                write_step_summary(
                    film_title,
                    current_index,
                    len(playlist),
                    current_stream_seconds
                )

                last_dashboard_time = now


        # ====================================================
        # FFMPEG SONUCU
        # ====================================================

        return_code = (
            process.returncode
        )


        print(
            f"⚠️ FFmpeg kapandı. "
            f"Return code: {return_code}"
        )


        # ====================================================
        # FİLM TAMAMLANDI
        # ====================================================

        if return_code == 0:

            print(
                "✅ Film tamamlandı."
            )

            current_index += 1

            last_seconds = 0


            update_gist_state(
                current_index,
                0
            )


            write_step_summary(
                film_title,
                current_index,
                len(playlist),
                0,
                status=(
                    "✅ Bitti, "
                    "sıradaki filme geçiliyor"
                )
            )


        # ====================================================
        # YAYIN KOPTU
        # ====================================================

        else:

            print(
                "🔴 FFmpeg/yayın bağlantısı koptu."
            )


            last_seconds = int(
                current_stream_seconds
            )


            update_gist_state(
                current_index,
                last_seconds
            )


            write_step_summary(
                film_title,
                current_index,
                len(playlist),
                last_seconds,
                status=(
                    "🔴 Bağlantı koptu, "
                    "tekrar denenecek"
                )
            )


        print(
            "⚠️ 5 saniye sonra "
            "tekrar bağlanılıyor..."
        )

        time.sleep(5)


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    try:

        start_m3u_stream()

    except KeyboardInterrupt:

        print(
            "\n🛑 Yayın durduruldu."
        )

    except Exception as error:

        print(
            f"\n❌ Kritik hata: {error}"
        )

        raise
