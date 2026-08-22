import sys
import os
import requests
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from terabox.public_api import prepare_terabox_link
from terabox.core_pipeline import build_streaming_url
from terabox.internal_helpers import _headers, CookiesList

def main():
    test_url = "https://1024terabox.com/s/1AzYlzzrWxZSurHlbyFMpLg"
    surl = test_url.split("/")[-1].lstrip("1")
    
    print(f"Checking 1080p streaming for SURL {surl} across {len(CookiesList)} cookies...\n")
    
    prepared = prepare_terabox_link(surl)
    
    for i, cookie_str in enumerate(CookiesList):
        session = requests.Session()
        for c in cookie_str.split(";"):
            if "=" in c:
                k, v = c.strip().split("=", 1)
                session.cookies.set(k.strip(), v.strip(), domain=".1024tera.com", path="/")
                
        url = build_streaming_url(
            prepared["shareid"], prepared["uk"], prepared["sign"], 
            prepared["timestamp"], prepared["fs_id"], "M3U8_AUTO_1080"
        )
        
        r = session.get(url, headers=_headers(session, surl), timeout=10)
        text = r.text.strip()
        
        if text.startswith("#EXTM3U"):
            print(f"[COOKIE {i+1}] SUCCESS - 1080p unlocked!")
        else:
            try:
                data = json.loads(text)
                print(f"[COOKIE {i+1}] FAILED - errno={data.get('errno')} ({data.get('errmsg', '')})")
            except Exception:
                print(f"[COOKIE {i+1}] FAILED - Unknown response")

if __name__ == "__main__":
    main()
