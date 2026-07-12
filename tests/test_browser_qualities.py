import sys
import os
import requests
import json
import random
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from terabox.public_api import prepare_terabox_link
from terabox.core_pipeline import build_streaming_url
from terabox.internal_helpers import _headers, _logid, BASE_URL

def probe_qualities(session, prepared):
    shareid = prepared["shareid"]
    uk = prepared["uk"]
    sign = prepared["sign"]
    timestamp = prepared["timestamp"]
    fs_id = prepared["fs_id"]
    surl = prepared["surl"]
    
    print("\n--- Probing different client types and app_ids for 1080p ---")
    
    # Variations to test
    client_types = ["0", "1", "2", "3", "4"]
    app_ids = ["250528", "250530", "4384814", "4823612"]
    channels = ["dubox", "android", "ios", "pc"]
    
    headers = _headers(session, surl)
    
    for ctype in client_types:
        for app in app_ids:
            for ch in channels:
                params = {
                    "uk": str(uk), "shareid": str(shareid), "type": "M3U8_AUTO_1080",
                    "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
                    "jsToken": "", "esl": "1", "isplayer": "1", "ehps": "1",
                    "clienttype": ctype, "app_id": app, "web": "1" if ctype=="0" else "0",
                    "channel": ch, "dp-logid": _logid(),
                }
                
                url = f"{BASE_URL}/share/streaming"
                
                try:
                    r = session.get(url, params=params, headers=headers, timeout=10)
                    text = r.text.strip()
                    if text.startswith("#EXTM3U"):
                        print(f"[!] SUCCESS 1080p unlocked! clienttype={ctype}, app_id={app}, channel={ch}")
                        return
                    else:
                        try:
                            data = json.loads(text)
                            errno = data.get("errno")
                            # We expect 130 or -6 etc
                            if errno != 130:
                                print(f"[*] clienttype={ctype}, app={app}, channel={ch} -> errno={errno}")
                        except:
                            print(f"[*] clienttype={ctype}, app={app}, channel={ch} -> Unknown response")
                except Exception as e:
                    pass
                time.sleep(0.5)

    print("\n[!] None of the variations unlocked 1080p. The API might strictly require a Premium account cookie for 1080p, or the video might not actually be 1080p on the server.")

def main():
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://1024terabox.com/s/1AzYlzzrWxZSurHlbyFMpLg"
    surl = test_url.split("/")[-1].lstrip("1")
    
    print(f"Preparing link: {surl}")
    prepared = prepare_terabox_link(surl)
    probe_qualities(prepared["session"], prepared)

if __name__ == "__main__":
    main()
