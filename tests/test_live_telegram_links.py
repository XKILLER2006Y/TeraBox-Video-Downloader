"""
tests/test_live_telegram_links.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Automated verification tool that takes links extracted from live Telegram
chats and runs them through the bot's resolvers to verify end-to-end functionality.
"""
import time
import yt_dlp

from telegram_logic.helpers import extract_all_terabox_url_exp
from diskwalaDL.public_api import extract_all_diskwala_urls, get_diskwala_info
from universalDL import extract_universal_urls, resolve_universal, is_universal_dl_url
from telegram_logic.social_dl import extract_all_social_urls, is_social_url
from flareDL import extract_all_flare_urls, get_flare_info
from flezenDL import extract_all_flezen_urls, get_flezen_info
from teraboxDL import get_video_info_fast

links = [
    'https://filesadda.site/86vclgsx66rq',
    'https://filesadda.site/gzlsmxrthjny',
    'https://filesadda.site/heubthhn86o4',
    'https://flaredvns.com/s/2088649141160128514',
    'https://flaredvns.com/s/2088649150894972929',
    'https://flaredvns.com/s/2093719159241584641',
    'https://flaredvns.com/s/2093719162860998657',
    'https://flaredvns.com/s/2093904060636995586',
    'https://flaremlmq.com/s/2088649151318597633',
    'https://flaremlmq.com/s/2093719160650600450',
    'https://flaremlmq.com/s/2093719160822972418',
    'https://flaremlmq.com/s/2093719163352002561',
    'https://flaremlmq.com/s/2093904060242595841',
    'https://flaremlmq.com/s/2093904061039648769',
    'https://flaremlmq.com/s/2093973615380148225',
    'https://flareobhx.com/s/2088649150643179522',
    'https://flareobhx.com/s/2088649150660362241',
    'https://flareobhx.com/s/2093719160822566913',
    'https://flareobhx.com/s/2093719161816752130',
    'https://flareobhx.com/s/2093719161875742722',
    'https://flareobhx.com/s/2093904063522541570',
    'https://flareobhx.com/s/2093973615417626626',
    'https://flarepqyd.com/s/2088266425771888641',
    'https://flarepqyd.com/s/2088649150668480514',
    'https://flarepqyd.com/s/2093719160168660994',
    'https://flarepqyd.com/s/2093719160193556482',
    'https://flarepqyd.com/s/2093904061010558979',
    'https://flarepqyd.com/s/2093904063694643201',
    'https://flarepqyd.com/s/2093973614839083009',
    'https://flarepqyd.com/s/2093973615434674177',
    'https://flarepqyd.com/s/2093973615439003650',
    'https://terasharefile.com/s/1IzwqcOmsq5O7wfU9GFBz7A',
    'https://terasharefile.com/s/1qO_jGm0H_iB6gLDpkWdLfg',
    'https://terasharefile.com/s/1qQh2v012srojFsWoCySaw',
    'https://www.1024tera.com/wap/share/filelist?surl=nOxcep110zPCVj8heYWwAg',
    'https://www.diskwala.com/app/693e365df999967910d61182',
    'https://www.diskwala.com/app/694c9e4d39857e10c6ea312f',
    'https://www.diskwala.com/app/695125fb39857e10c6033a83',
    'https://www.diskwala.com/app/69534b3b39857e10c60dd629',
    'https://www.diskwala.com/app/698a009539857e10c64df59e',
    'https://www.diskwala.com/app/6997f9be39857e10c69f794a',
    'https://www.diskwala.com/app/69aa5ea339857e10c60dab5c',
    'https://www.diskwala.com/app/69c1578739857e10c69e7c4a',
    'https://www.diskwala.com/app/69c563de563b044b36c3b9f6',
    'https://www.diskwala.com/app/69cf437bdc3191e299b5178c',
    'https://www.diskwala.com/app/69d651d1dc3191e299e6c70f',
    'https://www.diskwala.com/app/69d88b95dc3191e299f661a1',
    'https://www.diskwala.com/app/69e0b4a769eabf87206f6bab',
    'https://www.diskwala.com/app/69e4d44669eabf87208d4e27',
    'https://www.diskwala.com/app/69ef6a6769eabf8720d8b75e',
    'https://www.diskwala.com/app/69f9f48869eabf8720281af4',
    'https://www.diskwala.com/app/69f9f4af69eabf8720281c59',
    'https://www.diskwala.com/app/6a15690d69eabf8720ecbe14',
    'https://www.diskwala.com/app/6a1569b969eabf8720ecc0f7',
    'https://www.diskwala.com/app/6a1569b969eabf8720ecc110',
    'https://www.diskwala.com/app/6a1db19b69eabf87202ab6fa',
    'https://www.diskwala.com/app/6a26386569eabf87206c93ff',
    'https://www.diskwala.com/app/6a414a6769eabf87201149b0',
    'https://www.diskwala.com/app/6a49cd0969eabf872054f3f2',
    'https://www.diskwala.com/app/6a72da6b06ba7ea03d2138ad',
    'https://www.diskwala.com/app/6a79cc2106ba7ea03d68dc64',
    'https://www.diskwala.com/app/6a81ea4406ba7ea03dbb9d7a',
    'https://www.diskwala.com/app/6a8bb56506ba7ea03d1f9ca5',
    'https://www.diskwala.com/app/6a8bb56b06ba7ea03d1f9d71',
    'https://www.instagram.com/p/Dcn9DugGR8U/',
    'https://youtu.be/1vNe8ahCWKY',
    'https://youtu.be/2l8ksiXYlLY',
    'https://youtu.be/cW9js6f70vE',
    'https://youtube.com/shorts/OurAjAtba8g?si=Htj0BMNQJ0hk9XQZ'
]

def main():
    print(f"=== AUTOMATED TEST OF {len(links)} TELEGRAM LINKS ===\n")
    stats = {"OK": 0, "FAIL": 0, "EXPIRED": 0}

    for i, raw_url in enumerate(links, 1):
        u = raw_url.rstrip("*.,)]}'\"")
        t0 = time.time()
        platform = "Unknown"
        status = "PENDING"
        details = ""

        try:
            if extract_all_terabox_url_exp(u):
                platform = "TeraBox"
                info = get_video_info_fast(u)
                status = "OK"
                sz = info.get("size_bytes", 0) // (1024 * 1024)
                details = f"{info.get('file_name', 'video')[:30]} ({sz}MB)"
            elif extract_all_diskwala_urls(u):
                platform = "Diskwala"
                info = get_diskwala_info(u)
                status = "OK"
                sz = info.get("size_bytes", 0) // (1024 * 1024)
                details = f"{info.get('file_name', 'video')[:30]} ({sz}MB)"
            elif extract_all_flare_urls(u):
                platform = "Flare"
                info = get_flare_info(u)
                status = "OK"
                sz = info.get("size", 0) // (1024 * 1024)
                details = f"{info.get('filename', 'video')[:30]} ({sz}MB)"
            elif extract_all_flezen_urls(u):
                platform = "Flezen"
                info = get_flezen_info(u)
                status = "OK"
                sz = info.get("size", 0) // (1024 * 1024)
                details = f"{info.get('filename', 'video')[:30]} ({sz}MB)"
            elif is_universal_dl_url(u) or extract_universal_urls(u):
                platform = "Universal"
                info = resolve_universal(u)
                status = "OK"
                sz = info.get("size", 0) // (1024 * 1024)
                details = f"{info.get('filename', 'file')[:30]} ({sz}MB)"
            elif is_social_url(u) or extract_all_social_urls(u):
                platform = "Social/yt-dlp"
                ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(u, download=False)
                    status = "OK"
                    details = f"{info.get('title', 'video')[:30]}"
            else:
                status = "NO_ROUTER"
                details = "Unmatched platform"
        except Exception as e:
            err_str = str(e)
            if "expired" in err_str.lower() or "deleted" in err_str.lower() or "not found" in err_str.lower() or "removed" in err_str.lower():
                status = "EXPIRED"
            else:
                status = "FAIL"
            details = err_str[:60]

        stats[status] = stats.get(status, 0) + 1
        elapsed = round(time.time() - t0, 2)
        print(f"[{i:02d}/{len(links)}] {status:7s} | {platform:13s} | {elapsed:4.2f}s | {u[:48]} | {details}")

    print("\n" + "=" * 60)
    print(f"RESULTS SUMMARY: {stats.get('OK', 0)} Active/Resolved, {stats.get('EXPIRED', 0)} Uploader-Expired, {stats.get('FAIL', 0)} Errors")
    print("=" * 60)

if __name__ == "__main__":
    main()
