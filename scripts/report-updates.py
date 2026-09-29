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
    # best effort: split into digit/letter runs so "1.3.7" > "1.0rc2" and
    # "1.2.1" > "1.2rc2" (a number ranks above a pre-release marker).
    out = []
    for part in re.findall(r"\d+|[A-Za-z]+", v):
        if part.isdigit():
            out.append((1, int(part), ""))
        else:
            out.append((0, 0, part.lower()))
    # pad so a release ("1.2.0") outranks its pre-release ("1.2.0beta1")
    return out + [(2, 0, "")] * 8


def looks_like_version(s):
    return bool(re.match(r"^v?\d", s))


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
    # no releases: fall back to tags, preferring version-looking ones (a repo
    # may have non-version tags such as libwebp's "webp-rfc9649").
    d = fetch_json("https://api.github.com/repos/%s/%s/tags?per_page=100" % (owner, repo), h)
    vers = [t["name"] for t in d if looks_like_version(t["name"])]
    if vers:
        return sorted(vers, key=version_key)[-1], "github tag (max version)"
    if d:
        return d[0]["name"], "github tag (first)"
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


def extract_versions(names, pinned_file):
    """Pull the version out of each archive name, using the pinned file as the
    template (so a 'doc' variant or another project is not mistaken for one)."""
    m = re.match(r"^(.*?)([0-9][^/]*?)(\.tar\.(?:gz|xz|bz2)|\.tgz|\.zip)$", pinned_file)
    if not m:
        return set()
    prefix, ext = m.group(1), m.group(3)
    out = set()
    for n in names:
        mm = re.match(r"^%s([0-9][^/]*?)%s$" % (re.escape(prefix), re.escape(ext)), n)
        if mm:
            out.add(mm.group(1))
    return out


def latest_listing(dir_url, pinned_file):
    """A plain directory listing: report the newest few versions (listings are
    alphabetical, so 'the last one' is not the newest)."""
    html = fetch(dir_url)
    names = [n.rstrip("/").split("/")[-1] for n in re.findall(r'href="([^"?#]+)"', html)]
    vers = extract_versions(names, pinned_file)
    if not vers:
        return None, None
    return top_versions(vers), "listing (newest 3)"


def latest_sourceforge(project, subpath, pinned_file):
    """sourceforge's files/ listing blocks bots, but its RSS works. The version
    comes from the file name (the directory layout differs between releases:
    opencore-amr has both files/opencore-amr/<file> and .../0.1.2/<file>)."""
    sub = "/".join(subpath.split("/")[:-2])
    xml = fetch("https://sourceforge.net/projects/%s/rss?path=/%s" % (project, sub))
    names = []
    for link in re.findall(r"<link>([^<]+)</link>", xml):
        parts = link.rstrip("/").split("/")
        if parts and parts[-1] == "download":
            parts.pop()
        if parts:
            names.append(parts[-1])
    vers = extract_versions(names, pinned_file)
    if not vers:
        return None, None
    return top_versions(vers), "sourceforge rss (newest 3)"


def resolve(name, version, url):
    """best effort: the latest upstream name for this dep, or (None, where)."""
    u = url
    # a release-asset download URL: the repo is still the first two segments
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/(?:releases|archive)/", u)
    if m:
        return latest_github(m.group(1), m.group(2))
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
    fname = u.rsplit("/", 1)[-1]
    m = re.match(r"https://downloads\.sourceforge\.net/project/([^/]+)/(.*)$", u)
    if m:
        return latest_sourceforge(m.group(1), m.group(2), fname)
    m = re.match(r"https://download\.sourceforge\.net/([^/]+)/", u)
    if m:
        return latest_sourceforge(m.group(1), "", fname)
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
