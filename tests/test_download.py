import sys
import os

# Add the parent directory to sys.path so we can import 'terabox'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
import json
from terabox.public_api import prepare_terabox_link, STORAGE_DIR
from terabox.core_pipeline import discover_all_hls_chunks, download_all_chunks, concatenate_chunks_ffmpeg, build_streaming_url
from terabox.internal_helpers import _headers, _safe_filename, BYTES_PER_MB

def check_quality(session, prepared, quality):
    """Probe the streaming URL once to see if it returns M3U8 or an error."""
    url = build_streaming_url(
        prepared["shareid"], prepared["uk"], prepared["sign"], 
        prepared["timestamp"], prepared["fs_id"], quality
    )
    try:
        r = session.get(url, headers=_headers(session, prepared["surl"]), timeout=30)
        text = r.text.strip()
        if text.startswith("#EXTM3U"):
            return True
        elif text.startswith("{"):
            try:
                data = json.loads(text)
                print(f"    [!] Quality {quality} failed with errno={data.get('errno')} ({data.get('errmsg', '')})")
            except Exception:
                print(f"    [!] Quality {quality} returned JSON format, but couldn't parse error")
        else:
            print(f"    [!] Quality {quality} returned unexpected format: {text[:50]}...")
    except Exception as e:
        print(f"    [!] Request failed for {quality}: {e}")
    return False

def main():
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://1024terabox.com/s/1mKoEsoPWtrnXZ_rNXbHZoA"
    surl = test_url.split("/")[-1].lstrip("1")
    
    print(f"Testing download for SURL: {surl}")
    prepared = prepare_terabox_link(surl)
    
    session = prepared["session"]
    
    qualities = ["M3U8_AUTO_1080", "M3U8_AUTO_720", "M3U8_AUTO_480", "M3U8_AUTO_360", "M3U8_720P", "M3U8_480P", "M3U8_360P"]
    working_quality = None
    
    for q in qualities:
        print(f"Checking quality: {q}...")
        if check_quality(session, prepared, q):
            print(f"--> {q} works!\n")
            working_quality = q
            break
            
    if not working_quality:
        print("No working quality found!")
        sys.exit(1)
        
    print(f"Proceeding to download chunks with quality {working_quality}...")
    chunks = discover_all_hls_chunks(
        session, prepared["shareid"], prepared["uk"], 
        prepared["sign"], prepared["timestamp"], prepared["fs_id"], 
        working_quality, surl=prepared["surl"]
    )
    
    print(f"\nDiscovered {len(chunks)} chunks.")
    
    filename = prepared["filename"]
    safe = _safe_filename(filename)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    mp4_path = os.path.join(STORAGE_DIR, safe if safe.lower().endswith(".mp4") else safe + ".mp4")
    tmp_dir = os.path.join(STORAGE_DIR, safe.rsplit(".", 1)[0] + "_segments")
    
    download_all_chunks(session, chunks, tmp_dir, surl=prepared["surl"])
    concatenate_chunks_ffmpeg(tmp_dir, chunks, mp4_path)
    
    final_size = os.path.getsize(mp4_path) / BYTES_PER_MB
    print(f"\nDownload complete: {mp4_path} ({final_size:.1f} MB)")
    
if __name__ == "__main__":
    main()
