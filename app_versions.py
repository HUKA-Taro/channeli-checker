"""Publish Android version policies from stable GitHub releases."""
import json
import logging
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from google.cloud.firestore import SERVER_TIMESTAMP

RELEASE_REPOS = {
    "noticeboardv2": "HUKA-Taro/noticeboard_v2",
    "placementv2": "HUKA-Taro/placement_v2",
}
TAG = re.compile(r"v(\d+\.\d+\.\d+)$")
INTERVAL_SECONDS = 300
RPC_OPTIONS = {"timeout": 5, "retry": None}


def tagged_build_number(app, repo, tag, version):
    paths = ("pubspec.yaml", "noticeboard/pubspec.yaml") if app == "noticeboardv2" else ("pubspec.yaml",)
    for path in paths:
        url = f"https://raw.githubusercontent.com/{repo}/{tag}/{path}"
        try:
            with urlopen(url, timeout=10) as response:
                pubspec = response.read().decode("utf-8")
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
        match = re.search(r"^version:[ \t]*(\d+\.\d+\.\d+)\+([1-9]\d*)[ \t]*(?:#[^\r\n]*)?\r?$",
                          pubspec, re.MULTILINE)
        if match and match[1] == version:
            return int(match[2])
        return None
    return None


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
    downloads = []
    for asset in release.get("assets", ()):
        name = asset.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*\.apk", name):
            continue
        url = f"https://github.com/{repo}/releases/download/{tag}/{quote(name)}"
        if (asset.get("state") == "uploaded"
                and asset.get("size", 0) > 0
                and re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest") or "")
                and asset.get("browser_download_url") == url):
            downloads.append(url)
    if len(downloads) != 1:
        return None
    build = tagged_build_number(app, repo, tag, version)
    if build is None:
        return None
    return {"minimum_build": build, "version": version, "download_url": downloads[0]}



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
