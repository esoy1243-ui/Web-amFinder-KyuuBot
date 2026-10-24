#!/usr/bin/env python3
"""Web UI lokal untuk mencari preset Alight Motion dari URL TikTok."""

from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from amfinder_pro import API_BASE, build_session, fmt_count, get_security_token, parse_stream

app = Flask(__name__)


def is_tiktok_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("http", "https") and (host == "tiktok.com" or host.endswith(".tiktok.com"))
    except ValueError:
        return False


def display_author(value):
    if isinstance(value, dict):
        return value.get("nickname") or value.get("unique_id") or "Tidak diketahui"
    return value or "Tidak diketahui"


def is_supported_preset_link(value):
    """Accept only well-formed links from the preset hosting services."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        allowed_domains = ("alightcreative.com", "drive.google.com", "mediafire.com")
        return (
            parsed.scheme in ("http", "https")
            and any(host == domain or host.endswith("." + domain) for domain in allowed_domains)
            and bool(parsed.path)
        )
    except ValueError:
        return False


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/search")
def search():
    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()
    only_alightcreative = str(data.get("only_alightcreative", "false")).lower() in ("true", "1", "on", "yes")

    if not url:
        return jsonify(success=False, error="Masukkan link TikTok terlebih dahulu."), 400
    if not is_tiktok_url(url):
        return jsonify(success=False, error="Link tidak valid. Masukkan URL dari tiktok.com."), 400

    def generate():
        import json

        def event(kind, **values):
            return json.dumps({"type": kind, **values}, ensure_ascii=False) + "\n"

        api_response = None
        try:
            yield event("log", message="Menghubungi API amfinder...")
            session = build_session()
            yield event("log", message="Menembus Cloudflare...")
            token = get_security_token(session)
            yield event("log", message="Mengambil data video...")
            api_response = session.get(
                f"{API_BASE}?url={requests.utils.quote(url, safe='')}&t={token}",
                stream=True,
                timeout=(10, 30),
            )
            api_response.raise_for_status()
            links, video, saw_data = parse_stream(api_response)

            if not saw_data:
                yield event("error", error="API tidak mengembalikan data. Coba lagi sebentar.")
                return

            normalized = []
            for item in links:
                link = (item.get("url") or item.get("link") or item.get("href") or "").strip()
                title = item.get("title") or "Preset Alight Motion"
                if str(title).strip().casefold() == "page not found":
                    continue
                if not is_supported_preset_link(link):
                    continue
                if only_alightcreative and "alightcreative.com" not in link.lower():
                    continue
                normalized.append({
                    "title": title,
                    "url": link,
                    "source": item.get("source") or "AM Finder",
                    "type": item.get("type") or "AM",
                })

            stats = video.get("stats") if isinstance(video.get("stats"), dict) else {}
            result = {
                "title": video.get("title") or video.get("description") or "Video TikTok",
                "author": display_author(video.get("author")),
                "views": fmt_count(stats.get("views", "N/A")),
                "likes": fmt_count(stats.get("likes", "N/A")),
                "preset_links": normalized,
            }
            yield event("log", message="Selesai!")
            yield event("result", success=True, result=result)
        except requests.exceptions.Timeout:
            yield event("error", error="Permintaan ke API timeout. Coba lagi sebentar.")
        except requests.exceptions.RequestException as exc:
            yield event("error", error=f"Gagal terhubung ke API: {exc}")
        except (KeyError, ValueError, TypeError):
            app.logger.exception("Respons API tidak bisa diproses")
            yield event("error", error="Respons API tidak bisa diproses. Coba lagi nanti.")
        except Exception as exc:
            app.logger.exception("Pencarian preset gagal")
            yield event("error", error=f"Terjadi kesalahan saat mencari preset: {exc}")
        finally:
            if api_response is not None:
                api_response.close()

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
