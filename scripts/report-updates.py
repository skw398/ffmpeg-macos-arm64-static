#!/usr/bin/env python3
"""report-updates.py — report the latest upstream release/tag of every pinned
dependency, so a human can see what moved.

Report only: it never fails. It does NOT decide whether to upgrade -- version
schemes differ too much to compare reliably (v1.2.3 / libunibreak_8_0 /
lcms2.19.1 / 0.99.beta20 / release-3.0.6 / a commit), so it reports the latest
it can find for every line and leaves the judgement to a human.

Output:  name | pinned | latest | where
Env:     GITHUB_TOKEN / GH_TOKEN (optional; raises the GitHub API rate limit)

Usage:   python3 scripts/report-updates.py [name ...]
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(ROOT, "deps.txt")
UA = "Mozilla/5.0 (compatible; ffmpeg-macos-arm64-static-update-reporter)"
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
ARCHIVE = re.compile(r"\.(tar\.(gz|xz|bz2)|tgz|zip)$")
RETRY = (403, 429, 500, 502, 503, 504)


def fetch(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in RETRY:
                raise
        except urllib.error.URLError as e:
            last = e
        time.sleep(2)
    raise last


def fetch_json(url, headers=None):
    return json.loads(fetch(url, headers))


def version_key(v):
    # best effort: digits compare as numbers, the rest lexically
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[._+-]", v)]


# --- per-host resolvers ------------------------------------------------------

def latest_github(owner, repo):
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = "Bearer " + TOKEN
    try:
        d = fetch_json("https://api.github.com/repos/%s/%s/releases/latest" % (owner, repo), h)
        t = d.get("tag_name") or d.get("name")
        if t:
            return t, "github release"
    except urllib.error.HTTPError as e:
        if e.code != 404:            # 404 = no releases -> try tags
            raise
    d = fetch_json("https://api.github.com/repos/%s/%s/tags?per_page=1" % (owner, repo), h)
    if d:
        return d[0]["name"], "github tag"
    return None, None


def latest_gitlab(base, path):
    proj = urllib.parse.quote(path, safe="")
    d = fetch_json("%s/api/v4/projects/%s/repository/tags?per_page=1&order_by=updated&sort=desc" % (base, proj))
    if d:
        return d[0]["name"], "gitlab tag"
    return None, None


def latest_googlesource(url):
    t = fetch(url.rstrip("/") + "/+refs?format=JSON")
    t = t.split("\n", 1)[-1]         # strip the )]}' guard line
    d = json.loads(t)
    tags = sorted((k.split("/")[-1] for k in d if k.startswith("refs/tags/")), key=version_key)
    if tags:
        return tags[-1], "googlesource tag (max version)"
    return None, None


def latest_bitbucket(owner, repo):
    d = fetch_json("https://api.bitbucket.org/2.0/repositories/%s/%s/refs/tags?pagelen=1&sort=-target.date" % (owner, repo))
    vals = d.get("values") or []
    if vals:
        return vals[0]["name"], "bitbucket tag"
    return None, None


def top_versions(versions, n=3):
    return " / ".join(sorted(set(versions), key=version_key)[-n:][::-1])


def latest_listing(dir_url, pinned_file):
    """A plain directory listing: match the pinned file's shape and report the
    newest few versions (listings are alphabetical, so 'the last one' is not
    the newest; 'doc' and other variants are filtered out by requiring a digit)."""
    html = fetch(dir_url)
    names = [n.rstrip("/").split("/")[-1] for n in re.findall(r'href="([^"?#]+)"', html)]
    m = re.match(r"^(.*?)([0-9][^/]*?)(\.tar\.(?:gz|xz|bz2)|\.tgz|\.zip)$", pinned_file)
    if not m:
        return None, None
    prefix, ext = m.group(1), m.group(3)
    cands = set()
    for n in names:
        mm = re.match(r"^%s([0-9][^/]*?)%s$" % (re.escape(prefix), re.escape(ext)), n)
        if mm:
            cands.add(mm.group(1))
    if not cands:
        return None, None
    return top_versions(cands), "listing (newest 3)"


def latest_sourceforge(project, subpath):
    """sourceforge's files/ listing blocks bots, but its RSS works and is
    newest-first; the version is the directory above the file."""
    # subpath is <sub>/<version>/<file>; list the directory that holds the versions
    sub = "/".join(subpath.split("/")[:-2])
    xml = fetch("https://sourceforge.net/projects/%s/rss?path=/%s" % (project, sub))
    vers = []
    for link in re.findall(r"<link>([^<]+)</link>", xml):
        parts = link.rstrip("/").split("/")
        if parts and parts[-1] == "download":
            parts.pop()
        if not parts or not ARCHIVE.search(parts[-1]):
            continue
        vers.append(parts[-2] if len(parts) >= 2 else parts[-1])
    if not vers:
        return None, None
    return top_versions(vers), "sourceforge rss (newest 3)"


def resolve(name, version, url):
    """best effort: the latest upstream name for this dep, or (None, where)."""
    u = url
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", u)
    if m:
        return latest_github(m.group(1), m.group(2))
    m = re.match(r"https://(gitlab\.com|code\.videolan\.org)/(.+?)(?:\.git)?$", u)
    if m:
        return latest_gitlab("https://" + m.group(1), m.group(2))
    if re.match(r"https://[a-z0-9-]+\.googlesource\.com/", u):
        return latest_googlesource(u)
    m = re.match(r"https://bitbucket\.org/([^/]+)/([^/]+)/", u)
    if m:
        return latest_bitbucket(m.group(1), m.group(2))
    m = re.match(r"https://downloads\.sourceforge\.net/project/([^/]+)/(.*)$", u)
    if m:
        return latest_sourceforge(m.group(1), m.group(2))
    m = re.match(r"https://download\.sourceforge\.net/([^/]+)/", u)
    if m:
        return latest_sourceforge(m.group(1), "")
    if u.startswith("https://"):
        return latest_listing(u.rsplit("/", 1)[0] + "/", u.rsplit("/", 1)[-1])
    return None, None


def main():
    only = set(sys.argv[1:])
    rows = []
    with open(DEPS) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            f = [x.strip() for x in line.split("|")]
            if len(f) < 3:
                continue
            if only and f[0] not in only:
                continue
            rows.append((f[0], f[1], f[2]))

    print("== upstream versions (report only; compare with the pin by hand) ==")
    print("%-16s %-24s %-30s %s" % ("name", "pinned", "latest", "where"))
    ok = unknown = 0
    for name, version, url in rows:
        try:
            latest, where = resolve(name, version, url)
        except Exception as e:                        # never abort the report
            latest, where = None, "error: %s" % e
        if latest:
            ok += 1
            print("%-16s %-24s %-30s %s" % (name, version, latest, where))
        else:
            unknown += 1
            print("%-16s %-24s %-30s %s" % (name, version, "?", where or "unresolved"))
    print()
    print("== %d resolved, %d unresolved (report only) ==" % (ok, unknown))


if __name__ == "__main__":
    sys.exit(main())
