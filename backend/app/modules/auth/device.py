"""User-agent summarisation for device history.

Deliberately crude. The goal is a label a person recognises in a session list -
"Chrome on Windows", "Safari on iPhone" - so they can spot the entry that is not
theirs. It is not analytics, and it is never a security control: the user agent is
client-supplied and trivially forged.

A full parser (``ua-parser`` and its regex database) would be more accurate and
is not worth a dependency plus periodic database updates for a label in a
settings page. Nothing branches on this output.
"""

from __future__ import annotations

import re
from typing import Final

#: Order matters - the first match wins. Chrome's UA contains "Safari", Edge's
#: contains both "Chrome" and "Safari", so the most specific brands come first.
_BROWSERS: Final[tuple[tuple[str, str], ...]] = (
    (r"Edg[eA]?/", "Edge"),
    (r"OPR/|Opera", "Opera"),
    (r"Brave/", "Brave"),
    (r"Vivaldi/", "Vivaldi"),
    (r"SamsungBrowser/", "Samsung Internet"),
    (r"YaBrowser/", "Yandex"),
    (r"Firefox/|FxiOS/", "Firefox"),
    (r"CriOS/", "Chrome"),
    (r"Chromium/", "Chromium"),
    (r"Chrome/", "Chrome"),
    (r"Version/.*Safari/", "Safari"),
    (r"Safari/", "Safari"),
    (r"curl/", "curl"),
    (r"HTTPie/", "HTTPie"),
    (r"Postman", "Postman"),
    (r"python-requests|httpx|aiohttp", "Python client"),
    (r"insomnia", "Insomnia"),
)

_PLATFORMS: Final[tuple[tuple[str, str, str], ...]] = (
    # (pattern, label, device type)
    (r"iPhone", "iPhone", "mobile"),
    (r"iPad", "iPad", "tablet"),
    (r"iPod", "iPod", "mobile"),
    (r"Android.*Mobile", "Android", "mobile"),
    (r"Android", "Android", "tablet"),
    (r"Windows NT 10|Windows NT 11", "Windows", "desktop"),
    (r"Windows", "Windows", "desktop"),
    (r"Macintosh|Mac OS X", "macOS", "desktop"),
    (r"CrOS", "ChromeOS", "desktop"),
    (r"Ubuntu", "Ubuntu", "desktop"),
    (r"Linux", "Linux", "desktop"),
)

_BOT = re.compile(r"bot|crawler|spider|slurp|monitor|pingdom|uptime", re.IGNORECASE)


def describe_device(user_agent: str | None) -> tuple[str | None, str | None]:
    """Summarise a user agent as ``(label, device_type)``.

    ``device_type`` is one of ``desktop``, ``mobile``, ``tablet``, ``api``,
    ``bot``, or ``None``, and drives which icon the UI shows.

    >>> describe_device("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    ...                 "AppleWebKit/537.36 (KHTML, like Gecko) "
    ...                 "Chrome/120.0 Safari/537.36")
    ('Chrome on macOS', 'desktop')
    """
    if not user_agent:
        return None, None

    if _BOT.search(user_agent):
        return "Automated client", "bot"

    browser = next((name for pattern, name in _BROWSERS if re.search(pattern, user_agent)), None)
    platform = next(
        ((name, kind) for pattern, name, kind in _PLATFORMS if re.search(pattern, user_agent)),
        None,
    )

    # Non-browser clients report no platform; labelling curl "on Linux" is noise.
    if browser in {"curl", "HTTPie", "Postman", "Python client", "Insomnia"}:
        return browser, "api"

    if browser and platform:
        return f"{browser} on {platform[0]}", platform[1]
    if browser:
        return browser, "desktop"
    if platform:
        return platform[0], platform[1]

    return "Unknown device", None
