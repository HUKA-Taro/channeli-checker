"""Publish Android version policies from stable GitHub releases."""
import json
import logging
import re
from urllib.request import Request, urlopen

from google.cloud.firestore import SERVER_TIMESTAMP

RELEASE_REPOS = {
    "noticeboardv2": "HUKA-Taro/noticeboard_v2",
    "placementv2": "HUKA-Taro/placement_v2",
}
TAG = re.compile(r"v(\d+\.\d+\.\d+)$")
INTERVAL_SECONDS = 300
RPC_OPTIONS = {"timeout": 5, "retry": None}


def release_policy(app, repo):
    request = Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urlopen(request, timeout=10) as response:
        release = json.load(response)
    tag = release.get("tag_name", "")
    match = TAG.fullmatch(tag)
    if not match or release.get("draft") or release.get("prerelease"):
        return None
    version = match[1]
    name_pattern = re.compile(rf"{app}-{re.escape(version)}-([1-9]\d*)\.apk$")
    matches = []
    for asset in release.get("assets", ()):
        name = asset.get("name", "")
        build = name_pattern.fullmatch(name)
        url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
        if (build and asset.get("state") == "uploaded"
                and asset.get("size", 0) > 0
                and re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest") or "")
                and (asset.get("browser_download_url") or "").casefold() == url.casefold()):
            matches.append({
                "minimum_build": int(build[1]),
                "version": version,
                "download_url": url,
            })
    if len(matches) == 1:
        return matches[0]
    return None


def publish_versions(client):
    for app, repo in RELEASE_REPOS.items():
        try:
            policy = release_policy(app, repo)
            if policy is None:
                continue
            ref = client.document(f"app_versions/{app}")
            previous = ref.get(**RPC_OPTIONS)
            old = previous.to_dict() or {}
            if policy["minimum_build"] <= old.get("minimum_build", 0):
                continue
            fields = {**policy, "updated_at": SERVER_TIMESTAMP}
            if previous.exists:
                ref.update(fields,
                           option=client.write_option(last_update_time=previous.update_time),
                           **RPC_OPTIONS)
            else:
                ref.create(fields, **RPC_OPTIONS)
        except Exception as error:
            logging.warning("Could not publish %s version policy: %s",
                            app, type(error).__name__)
