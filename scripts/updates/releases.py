"""Read upstream release metadata without downloading or building sources."""

from dataclasses import dataclass
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class Release:
    version: str
    url: str


def request_json(url, *, method="GET", data=None):
    headers = {"Accept": "application/json", "User-Agent": "ffmpeg-update-check"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        headers["Authorization"] = "Bearer " + token
    if urllib.parse.urlsplit(url).hostname == "api.github.com":
        headers["Accept"] = "application/vnd.github+json"
    payload = None if data is None else json.dumps(data).encode()
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ffmpeg-update-check"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def version_key(version):
    # Handles the formats in deps.txt, including libunibreak_8_0, lcms2.19.1,
    # release-3.0.6 and libopenmpt's +release.autotools archive suffix.
    match = re.fullmatch(
        r"[A-Za-z_-]*(\d+(?:[._]\d+)*)(?:\.beta(\d+))?(?:\+release(?:\.autotools)?)?",
        version,
    )
    if not match:
        return None
    numbers = tuple(map(int, re.split(r"[._]", match[1])))
    if len(numbers) > 8:
        return None
    return numbers + (0,) * (8 - len(numbers)) + (int(match[2] is None), int(match[2] or 0))


def select_updates(pinned, releases, tag_prefixes=(), major=None):
    current = version_key(pinned)
    if current is None:
        raise ValueError(f"unsupported pinned version: {pinned}")
    candidates = {}
    prefixes = {"", "v", "release-", re.match(r"[A-Za-z_-]*", pinned)[0], *tag_prefixes}
    for release in releases:
        key = version_key(release.version)
        prefix = re.match(r"[A-Za-z_-]*", release.version)[0]
        if key is None or prefix not in prefixes or key <= current:
            continue
        if major is not None and key[0] != major:
            continue
        # Exclude preview tags, except a beta series already deliberately pinned.
        if key[8] == 0 and not (current[8] == 0 and key[:2] == current[:2]):
            continue
        candidates.setdefault(key, release)
    if not candidates:
        return []
    newest = max(candidates)
    series = [key for key in candidates if key[:2] == current[:2]]
    keys = sorted({newest, max(series) if series else newest})
    return [candidates[key] for key in keys]


def git_refs(url, *patterns):
    run = subprocess.run(["git", "ls-remote", url, *patterns], capture_output=True,
                         text=True, timeout=30, check=True)
    return [line.split("\t", 1) for line in run.stdout.splitlines() if "\t" in line]


def repository(url):
    parsed = urllib.parse.urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname in {"github.com", "bitbucket.org"}:
        return f"https://{parsed.hostname}/{'/'.join(parts[:2])}".removesuffix(".git")
    return url.removesuffix(".git")


def tag_url(repo, tag):
    ref = urllib.parse.quote(tag, safe="")
    host = urllib.parse.urlsplit(repo).hostname
    if host == "github.com":
        return f"{repo}/releases/tag/{ref}"
    if host in {"gitlab.com", "code.videolan.org"}:
        return f"{repo}/-/tags/{ref}"
    if host and host.endswith(".googlesource.com"):
        return f"{repo}/+/refs/tags/{ref}"
    if host == "bitbucket.org":
        return f"{repo}/src/{ref}/"
    return repo


def git_releases(repo):
    return [Release(ref.removeprefix("refs/tags/").removesuffix("^{}"),
                    tag_url(repository(repo), ref.removeprefix("refs/tags/").removesuffix("^{}")))
            for _, ref in git_refs(repo, "refs/tags/*")]


def archive_releases(names, pinned_file, pinned_version, base):
    # Use the actual pin to locate the version. The first digit may belong to
    # the project name, as in libbs2b-3.1.0.tar.gz.
    match = re.fullmatch(r"(.*?)" + re.escape(pinned_version)
                         + r"(.*\.(?:tar\.(?:gz|xz|bz2)|tgz|zip))", pinned_file)
    if not match:
        raise ValueError(f"unsupported archive name: {pinned_file}")
    pattern = re.compile(re.escape(match[1]) + r"([0-9].*?)" + re.escape(match[2]))
    releases = {}
    for href in names:
        path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        if path.endswith("/download"):
            path = path.removesuffix("/download")
        found = pattern.fullmatch(path.rsplit("/", 1)[-1])
        if found:
            # Do not compare compact historical names (lame-398.tar.gz) as
            # version 398 against the current dotted naming convention.
            if "." in pinned_version and "." not in found[1]:
                continue
            if version_key(found[1]) is None:
                continue
            releases[found[1]] = Release(found[1], urllib.parse.urljoin(base, href))
    if not releases:
        raise ValueError("no matching source releases found")
    return list(releases.values())


def find_releases(pin):
    url = pin.url
    repo = repository(url)
    host = urllib.parse.urlsplit(url).hostname or ""
    if re.fullmatch(r"[0-9a-f]{40}", pin.version):
        refs = git_refs(url, "HEAD")
        if len(refs) != 1 or refs[0][1] != "HEAD" or not re.fullmatch(r"[0-9a-f]{40}", refs[0][0]):
            raise ValueError("cannot resolve upstream HEAD")
        sha = refs[0][0]
        if host == "github.com":
            link = f"{repo}/commit/{sha}"
        elif host.endswith(".googlesource.com"):
            link = f"{repo}/+/{sha}"
        else:
            link = f"{repo}/-/commit/{sha}"
        return [Release(sha, link)]
    if host == "github.com":
        owner, name = urllib.parse.urlsplit(repo).path.strip("/").split("/")
        try:
            records = request_json(f"https://api.github.com/repos/{owner}/{name}/releases?per_page=100")
            releases = [Release(row["tag_name"], row["html_url"]) for row in records
                        if not row["draft"] and not row["prerelease"]]
            if releases:
                return releases
            tags = request_json(f"https://api.github.com/repos/{owner}/{name}/tags?per_page=100")
            if tags:
                return [Release(row["name"], tag_url(repo, row["name"])) for row in tags]
        except (OSError, ValueError):
            # A successful git fallback still permits a release check when the API is unavailable.
            return git_releases(repo + ".git")
        raise ValueError("no upstream releases or tags found")
    if host in {"gitlab.com", "code.videolan.org"} or host.endswith(".googlesource.com"):
        return git_releases(url)
    if host == "bitbucket.org":
        owner, name = urllib.parse.urlsplit(repo).path.strip("/").split("/")
        records = request_json(f"https://api.bitbucket.org/2.0/repositories/{owner}/{name}/refs/tags?pagelen=100&sort=-target.date")
        return [Release(row["name"], tag_url(repo, row["name"])) for row in records["values"]]
    filename = url.rsplit("/", 1)[-1]
    if host in {"download.sourceforge.net", "downloads.sourceforge.net"}:
        parts = urllib.parse.urlsplit(url).path.strip("/").split("/")
        project = parts[1] if parts[0] == "project" else parts[0]
        import xml.etree.ElementTree as ET
        xml = ET.fromstring(fetch_text(f"https://sourceforge.net/projects/{project}/rss?path=/"))
        names = [node.text for node in xml.findall("./channel/item/link") if node.text]
        return archive_releases(names, filename, pin.version, url)
    base = url.rsplit("/", 1)[0] + "/"
    html = fetch_text(base)
    return archive_releases(re.findall(r'href=["\']([^"\']+)["\']', html), filename, pin.version, base)


def check_pin(pin, policy=None):
    try:
        releases = find_releases(pin)
        if not releases:
            raise ValueError("no upstream versions found")
        if re.fullmatch(r"[0-9a-f]{40}", pin.version):
            updates = [release for release in releases if release.version != pin.commit]
        else:
            if not any(version_key(release.version) is not None for release in releases):
                raise ValueError("upstream version format is not supported")
            filename = pin.url.rsplit("/", 1)[-1]
            archive_prefix = filename.partition(pin.version)[0] if pin.version in filename else ""
            updates = select_updates(pin.version, releases, tag_prefixes=[archive_prefix],
                                     major=(policy or {}).get("major"))
        return {"name": pin.name, "pinned": pin.version,
                "updates": [{"version": release.version, "url": release.url} for release in updates],
                "error": None}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {"name": pin.name, "pinned": pin.version, "updates": [], "error": str(error)}
