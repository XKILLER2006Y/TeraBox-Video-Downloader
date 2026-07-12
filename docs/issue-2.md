I ran a series of new tests to figure out why the bot was failing for 1080p on this new link while your browser streamed it perfectly.

The Secret is in the Cookies
I created a test script that iterated through all 8 cookies provided in your .env file and attempted to request 1080p streaming for the exact same video. Here are the results:

[COOKIE 1] FAILED - errno=130 ()
[COOKIE 2] FAILED - errno=130 ()
[COOKIE 3] FAILED - errno=130 ()
[COOKIE 4] FAILED - errno=130 ()
[COOKIE 5] FAILED - errno=130 ()
[COOKIE 6] FAILED - errno=130 ()
[COOKIE 7] SUCCESS - 1080p unlocked!
[COOKIE 8] SUCCESS - 1080p unlocked!
Why it happens:
Your browser works because you're likely logged into an account that is either a Premium Account or an account that hasn't hit TeraBox's dynamic rate-limiting thresholds.

In your code, core_pipeline.py has a load_session() function that randomly selects one of these 8 cookies to prevent shadow bans:

python
cookie_str = random.choice(CookiesList)
Because 6 out of your 8 cookies are currently blocked from accessing 1080p/720p streams (likely due to free-tier restrictions or rate limits), the bot has a 75% chance of picking a "bad" cookie and failing with errno=130, and a 25% chance of picking Cookie 7 or 8 and succeeding at 1080p.

How the Fallback Code I Wrote Fixes This:
My previous modification to public_api.py solves this elegantly:

If the bot randomly picks Cookie 1-6, it tests 1080p -> fails, tests 720p -> fails, tests 480p -> succeeds. It then downloads the video in 480p.
If the bot randomly picks Cookie 7-8, it tests 1080p -> succeeds immediately and downloads in 1080p.
If you absolutely want the bot to always fetch 1080p when possible, we could update the fallback logic to attempt all available cookies for 1080p before giving up and dropping to 720p/480p. Let me know if you'd like me to implement a "cookie-hopping" strategy!