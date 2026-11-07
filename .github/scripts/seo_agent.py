#!/usr/bin/env python3
"""WIT SEO Agent v5
- Audits every HTML page in the repo checkout
- Auto-fixes safe issues in place (titles, descriptions, canonicals, og:title)
- Pulls Ahrefs Site Audit issues + domain metrics
- Writes seo-reports/latest.md and a dated copy
The workflow's "Commit fixes" step pushes the changes to main.
"""
import os, re, json, html, urllib.request, urllib.error, urllib.parse
from datetime import date, datetime
from pathlib import Path

ROOT = Path(os.environ.get("SITE_ROOT", "."))
BASE = "https://workplaceit.net"
DOMAIN = "workplaceit.net"
ANTHROPIC = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AHREFS = os.environ.get("AHREFS_API_KEY", "").strip()
MODEL = "claude-sonnet-4-6"
MAX_FIXED_PAGES = 25          # safety cap per weekly run
TITLE_MAX, DESC_MAX = 60, 160   # flag thresholds
TITLE_TARGET, DESC_TARGET = 58, 155  # rewrite targets
MIN_WORDS = 300
SKIP_DIRS = {".git", ".github", "node_modules", "seo-reports"}

ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')


# ---------- helpers ----------
def attrs(tag):
    return {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(4))
            for m in ATTR_RE.finditer(tag)}


def find_tag(content, name, key, value):
    """First <name ...> tag whose attribute `key` equals `value` (any attribute order)."""
    for m in re.finditer(rf"<{name}\b[^>]*>", content, re.I):
        a = attrs(m.group(0))
        if a.get(key, "").strip().lower() == value:
            return m.group(0), a
    return None, {}


def set_content_attr(content, tag, value):
    esc = html.escape(value, quote=True)
    new_tag = re.sub(r'(content\s*=\s*)("[^"]*"|\'[^\']*\')',
                     lambda m: f'{m.group(1)}"{esc}"', tag, count=1)
    return content.replace(tag, new_tag, 1)


def insert_in_head(content, snippet):
    m = re.search(r"</head>", content, re.I)
    if not m:
        return content
    return content[:m.start()] + f"  {snippet}\n" + content[m.start():]


def page_url(rel):
    p = rel.as_posix()
    if p == "index.html":
        return BASE + "/"
    if p.endswith("/index.html"):
        return f"{BASE}/{p[:-len('index.html')]}"
    return f"{BASE}/{p}"


def body_words(content):
    b = re.search(r"<body\b.*?</body>", content, re.S | re.I)
    s = b.group(0) if b else content
    s = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", s, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).split()


# ---------- Claude ----------
def claude(prompt, max_tokens=800):
    if not ANTHROPIC:
        return None
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": ANTHROPIC, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["content"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        print(f"Claude API {e.code}: {e.read().decode(errors='replace')[:300]}")
    except Exception as e:
        print(f"Claude error: {e}")
    return None


def rewrite(kind, current, title, text, limit):
    prompt = (f"Shorten this web page {kind} to at most {limit} characters. RULES: use only facts, services, "
              f"places, numbers and claims that already appear in the current {kind} or the page text excerpt; "
              f"do NOT add new claims, statistics, organizations, programs or services; keep the main keyword "
              f"and the city name; plain text, no quotes or notes. Return ONLY the new {kind}.\n\n"
              f"Current {kind}: {current or '(none)'}\nPage title: {title}\n"
              f"Page text excerpt: {' '.join(text)[:1500]}")
    for _ in range(2):
        out = claude(prompt, 200)
        if out:
            out = out.strip().strip('"').strip()
            if 20 <= len(out) <= limit:
                return out
    return None


# ---------- page audit + fix ----------
def process(path, allow_fix):
    rel = path.relative_to(ROOT)
    content = path.read_text(encoding="utf-8", errors="replace")
    original = content
    _, robots = find_tag(content, "meta", "name", "robots")
    if "noindex" in robots.get("content", "").lower():
        return [], []

    issues, fixes = [], []
    words = body_words(content)
    t_m = re.search(r"<title[^>]*>(.*?)</title>", content, re.S | re.I)
    title = html.unescape(t_m.group(1).strip()) if t_m else ""
    og_tag, og = find_tag(content, "meta", "property", "og:title")

    # Title
    if not title:
        issues.append("Missing <title>")
    elif len(title) > TITLE_MAX:
        new = rewrite("title tag", title, title, words, TITLE_TARGET) if allow_fix else None
        if new:
            content = content.replace(t_m.group(0), f"<title>{html.escape(new, quote=False)}</title>", 1)
            if og_tag and html.unescape(og.get("content", "")) == title:
                content = set_content_attr(content, og_tag, new)
                og_tag, og = find_tag(content, "meta", "property", "og:title")
            fixes.append(f"Title shortened ({len(title)}→{len(new)}): {new}")
            title = new
        else:
            issues.append(f"Title too long ({len(title)} chars)")

    # Meta description
    d_tag, d = find_tag(content, "meta", "name", "description")
    desc = html.unescape(d.get("content", "").strip())
    if not desc or len(desc) > DESC_MAX:
        new = rewrite("meta description", desc, title, words, DESC_TARGET) if allow_fix else None
        if new and d_tag:
            content = set_content_attr(content, d_tag, new)
            ogd_tag, ogd = find_tag(content, "meta", "property", "og:description")
            if ogd_tag and html.unescape(ogd.get("content", "")) == desc:
                content = set_content_attr(content, ogd_tag, new)
            fixes.append(f"Description shortened ({len(desc)}→{len(new)})")
        elif new:
            content = insert_in_head(content, f'<meta name="description" content="{html.escape(new, quote=True)}">')
            fixes.append("Description added")
        else:
            issues.append("Missing meta description" if not desc else f"Description too long ({len(desc)} chars)")

    # Canonical
    c_tag, _ = find_tag(content, "link", "rel", "canonical")
    if not c_tag:
        if allow_fix:
            content = insert_in_head(content, f'<link rel="canonical" href="{page_url(rel)}">')
            fixes.append(f"Canonical added: {page_url(rel)}")
        else:
            issues.append("Missing canonical")

    # og:title must match title
    if og_tag and title and html.unescape(og.get("content", "")) != title:
        if allow_fix:
            content = set_content_attr(content, og_tag, title)
            fixes.append("og:title synced to title")
        else:
            issues.append("og:title doesn't match title")

    # Manual-only checks
    if not re.search(r"<h1\b", content, re.I):
        issues.append("Missing H1")
    if len(words) < MIN_WORDS:
        issues.append(f"Thin content ({len(words)} words)")
    no_alt = sum(1 for m in re.finditer(r"<img\b[^>]*>", content, re.I) if "alt" not in attrs(m.group(0)))
    if no_alt:
        issues.append(f"{no_alt} image(s) missing alt attribute")

    if content != original:
        path.write_text(content, encoding="utf-8")
    return issues, fixes


# ---------- Ahrefs ----------
def ahrefs(endpoint, **params):
    url = f"https://api.ahrefs.com/v3/{endpoint}?" + urllib.parse.urlencode({**params, "output": "json"})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AHREFS}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"Ahrefs {endpoint} {e.code}: {e.read().decode(errors='replace')[:300]}")
    except Exception as e:
        print(f"Ahrefs {endpoint} error: {e}")
    return None


def first_list(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list):
                return v
    return []


def flat(d):
    if not isinstance(d, dict):
        return str(d)
    return ", ".join(f"{k}: {v}" for k, v in d.items() if not isinstance(v, (dict, list)) and v not in (None, ""))


def ahrefs_report():
    if not AHREFS:
        return "- Not configured (add the AHREFS_API_KEY repo secret)"
    today = date.today().isoformat()
    out = []
    dr = ahrefs("site-explorer/domain-rating", target=DOMAIN, date=today)
    if dr:
        out.append(f"- Domain rating: {flat(dr.get('domain_rating', dr))}")
    m = ahrefs("site-explorer/metrics", target=DOMAIN, date=today, mode="subdomains")
    if m:
        out.append(f"- Organic metrics: {flat(m.get('metrics', m))}")

    projects = first_list(ahrefs("site-audit/projects"))
    proj = next((p for p in projects if DOMAIN in json.dumps(p)), None)
    if not proj:
        out.append(f"- No Ahrefs Site Audit project found for {DOMAIN}")
        return "\n".join(out)
    out.append(f"- Site Audit project: {flat(proj)}")
    issues = first_list(ahrefs("site-audit/issues", project_id=proj.get("project_id")))
    rank = {"Error": 0, "Warning": 1, "Notice": 2}
    live = sorted((i for i in issues if (i.get("crawled") or 0) > 0),
                  key=lambda i: (rank.get(i.get("importance"), 3), -(i.get("crawled") or 0)))
    print(f"Ahrefs issues: {len(live)} affecting pages (of {len(issues)} checks)")
    out.append(f"\n### Ahrefs Site Audit issues affecting pages ({len(live)})")
    out += [f"- **{i.get('importance')}** · {i.get('name')} ({i.get('category')}): "
            f"{i.get('crawled')} URL(s), change {i.get('change', 0):+}" for i in live] or ["- None"]
    return "\n".join(out)


# ---------- main ----------
def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== Workplace IT SEO Agent v5 - {now} ===")
    print(f"Claude key: {'YES' if ANTHROPIC else 'NO'} | Ahrefs key: {'YES' if AHREFS else 'NO'}")

    pages = sorted(p for p in ROOT.rglob("*.html") if not SKIP_DIRS & set(p.relative_to(ROOT).parts))
    print(f"Found {len(pages)} HTML pages")

    manual, fixed = {}, {}
    for p in pages:
        try:
            issues, fixes = process(p, allow_fix=len(fixed) < MAX_FIXED_PAGES)
        except Exception as e:
            print(f"Error {p}: {e}")
            continue
        rel = p.relative_to(ROOT).as_posix()
        if issues:
            manual[rel] = issues
        if fixes:
            fixed[rel] = fixes
    print(f"Fixed: {len(fixed)} pages | Needs review: {len(manual)} pages")

    ahrefs_md = ahrefs_report()

    analysis = claude(
        f"SEO summary for {DOMAIN} ({now}). Pages: {len(pages)}. Auto-fixed: {json.dumps(fixed)[:1500]}. "
        f"Needs manual review: {json.dumps(manual)[:2000]}. Ahrefs data:\n{ahrefs_md[:3000]}\n\n"
        "Give a health score 1-10, the top 5 prioritized actions for the owner, and one tip. Under 200 words.",
        900) or "Claude analysis unavailable this run."

    sec = lambda d: "\n".join(f"- **{k}**: " + "; ".join(v) for k, v in d.items()) or "- None"
    report = (f"# SEO Report {date.today()}\n\n"
              f"Pages: {len(pages)} | Auto-fixed: {len(fixed)} | Needs review: {len(manual)}\n\n"
              f"## Analysis\n{analysis}\n\n## Auto-fixed this run\n{sec(fixed)}\n\n"
              f"## Needs manual review\n{sec(manual)}\n\n## Ahrefs\n{ahrefs_md}\n\n---\n*WIT SEO Agent v5*\n")
    out_dir = ROOT / "seo-reports"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "latest.md").write_text(report, encoding="utf-8")
    (out_dir / f"{date.today()}.md").write_text(report, encoding="utf-8")
    print(report)
    print("=== Done ===")


if __name__ == "__main__":
    main()
