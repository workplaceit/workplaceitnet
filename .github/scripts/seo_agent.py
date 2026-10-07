import os, re, json, base64, urllib.request, urllib.error
from datetime import datetime

TOKEN = os.environ["GITHUB_TOKEN"]
ANTHROPIC = os.environ.get("ANTHROPIC_API_KEY", "")
REPO = os.environ.get("REPO", "workplaceit/workplaceitnet")
BASE = "https://workplaceit.net"
ISSUES = {}

def gh_get(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"token {TOKEN}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def gh_create_or_update(path, content, msg):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    sha = None
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"token {TOKEN}"})
        with urllib.request.urlopen(req) as r:
            sha = json.load(r)["sha"]
    except: pass
    body = {"message": msg, "content": encoded}
    if sha: body["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"token {TOKEN}", "Content-Type": "application/json"},
        method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def decode(data):
    raw = data["content"].replace("\n", "").replace("\r", "")
    return base64.b64decode(raw).decode("utf-8", errors="replace")

def list_html(path=""):
    items = []
    try:
        url = f"https://api.github.com/repos/{REPO}/contents/{path}"
        req = urllib.request.Request(url, headers={"Authorization": f"token {TOKEN}"})
        with urllib.request.urlopen(req) as r:
            entries = json.load(r)
        for e in entries:
            if e["type"] == "file" and e["name"].endswith(".html"):
                items.append(e["path"])
            elif e["type"] == "dir" and e["name"] not in [".git", ".github", "node_modules", "seo-reports"]:
                items.extend(list_html(e["path"]))
    except Exception as ex:
        print(f"list error {path}: {ex}")
    return items

def ask_claude(prompt):
    if not ANTHROPIC:
        return "Skipped: ANTHROPIC_API_KEY not set."
    payload = json.dumps({
        "model": "claude-sonnet-4-6", "max_tokens": 800,
        "system": "SEO expert for Workplace IT, a Denver MSP. Be concise and actionable.",
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, headers={
        "x-api-key": ANTHROPIC,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    })
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)["content"][0]["text"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"Claude API error {e.code}: {err_body[:300]}")
        return f"Claude analysis unavailable (API error {e.code}). Check ANTHROPIC_API_KEY secret."

def audit_page(fname, content):
    issues = []
    t_m = re.search(r"<title>(.*?)</title>", content, re.DOTALL)
    d_m = re.search(r'<meta\s+name="description"\s+content="(.*?)"', content)
    can_m = re.search(r'<link\s+rel="canonical"\s+href="(.*?)"', content)
    ogt_m = re.search(r'property="og:title"\s+content="(.*?)"', content)
    rob_m = re.search(r'<meta\s+name="robots"\s+content="(.*?)"', content)
    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.DOTALL)
    t = t_m.group(1).strip() if t_m else ""
    d = d_m.group(1).strip() if d_m else ""
    robots = rob_m.group(1) if rob_m else ""
    h1 = re.sub(r"<[^>]+>", "", h1_m.group(1)).strip() if h1_m else ""
    og_t = ogt_m.group(1) if ogt_m else ""
    can = can_m.group(1) if can_m else ""
    if "noindex" in robots:
        return []
    if t and len(t) > 65: issues.append(f"Title too long ({len(t)} chars)")
    if d and len(d) > 162: issues.append(f"Desc too long ({len(d)} chars)")
    if og_t and t and og_t != t: issues.append("og:title mismatch")
    if not can: issues.append("Missing canonical")
    if not t: issues.append("MISSING TITLE")
    if not d: issues.append("MISSING DESC")
    if not h1: issues.append("MISSING H1")
    wc = len(re.sub(r"<[^>]+>", " ", content).split())
    if wc < 300: issues.append(f"LOW WORD COUNT ({wc})")
    return issues

def check_sitemap(all_html):
    try:
        data = gh_get("sitemap.xml")
        sitemap = decode(data)
        return [f for f in all_html if f"{BASE}/{f}" not in sitemap]
    except Exception as e:
        print(f"Sitemap check error: {e}")
        return []

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Workplace IT SEO Agent - {now}")
    print(f"ANTHROPIC_API_KEY set: {'Yes' if ANTHROPIC else 'NO - missing!'}")

    all_html = list_html()
    print(f"Found {len(all_html)} pages")

    for fname in all_html:
        try:
            data = gh_get(fname)
            content = decode(data)
            issues = audit_page(fname, content)
            if issues:
                ISSUES[fname] = issues
        except Exception as e:
            print(f"Error {fname}: {e}")

    sitemap_missing = check_sitemap(all_html)
    auto_fixable = {k: [i for i in v if any(x in i for x in ["too long", "mismatch", "canonical"])]
                    for k, v in ISSUES.items()
                    if any(x in i for x in ["too long", "mismatch", "canonical"] for i in v)}
    manual_needed = {k: [i for i in v if any(x in i for x in ["MISSING", "LOW"])]
                     for k, v in ISSUES.items()
                     if any(x in i for x in ["MISSING", "LOW"] for i in v)}

    print(f"Pages with issues: {len(ISSUES)} | Need manual: {len(manual_needed)}")

    analysis = ask_claude(
        f"Weekly SEO audit for workplaceit.net (Denver MSP).\n"
        f"Date:{now} Pages:{len(all_html)} Issues:{len(ISSUES)} Manual:{len(manual_needed)}\n"
        f"Top manual issues:{json.dumps(dict(list(manual_needed.items())[:5]),indent=2)}\n"
        f"Missing from sitemap:{sitemap_missing[:3]}\n"
        "Give: 1) Health score 1-10 2) Top 3 fixes this week 3) One growth tip. Under 150 words."
    )
    print(f"\nAnalysis:\n{analysis}")

    report = (
        f"# SEO Report - {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"**Pages:** {len(all_html)} | **Issues:** {len(ISSUES)} | **Manual:** {len(manual_needed)}\n\n"
        f"## Claude's Analysis\n{analysis}\n\n"
        f"## Needs Manual Fixes\n"
        + ("\n".join(f"- **{k}**: {v}" for k, v in manual_needed.items()) or "- None")
        + f"\n\n## Auto-Fixable (title/desc/canonical)\n"
        + ("\n".join(f"- **{k}**: {v}" for k, v in auto_fixable.items()) or "- None")
        + f"\n\n## Missing from Sitemap\n"
        + ("\n".join(f"- {f}" for f in sitemap_missing) or "- None")
        + "\n\n---\n*WIT SEO Agent - runs every Sunday 2am MT*\n"
    )

    try:
        gh_create_or_update("seo-reports/latest.md", report,
                            f"SEO Report {datetime.now().strftime('%Y-%m-%d')}")
        print("Report saved!")
    except Exception as e:
        print(f"Report save error: {e}")

    print("Done!")

if __name__ == "__main__":
    main()
