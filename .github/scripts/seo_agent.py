import os, re, json, base64, urllib.request, urllib.error
from datetime import datetime

TOKEN = os.environ["GITHUB_TOKEN"]
ANTHROPIC = os.environ["ANTHROPIC_API_KEY"]
REPO = os.environ.get("REPO", "workplaceit/workplaceitnet")
BASE = "https://workplaceit.net"
FIXES = []
ISSUES = {}

def gh_get(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"token {TOKEN}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def gh_put(path, content, sha, msg):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    # Encode content as bytes first, then base64
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content
    encoded = base64.b64encode(content_bytes).decode("ascii")
    body = json.dumps({"message": msg, "content": encoded, "sha": sha})
    req = urllib.request.Request(url, data=body.encode("utf-8"),
        headers={"Authorization": f"token {TOKEN}", "Content-Type": "application/json"},
        method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def gh_create(path, content, msg):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content
    encoded = base64.b64encode(content_bytes).decode("ascii")
    body = json.dumps({"message": msg, "content": encoded})
    req = urllib.request.Request(url, data=body.encode("utf-8"),
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
    payload = json.dumps({
        "model": "claude-sonnet-4-6", "max_tokens": 800,
        "system": "SEO expert for Workplace IT, a Denver MSP. Be concise.",
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, headers={
        "x-api-key": ANTHROPIC, "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    })
    with urllib.request.urlopen(req) as r:
        return json.load(r)["content"][0]["text"]

def audit(fname, content):
    changed = False
    page_issues = []
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
        return content, [], False
    if t and len(t) > 65:
        nt = t[:60].rsplit(" ", 1)[0]
        content = content.replace(f"<title>{t}</title>", f"<title>{nt}</title>", 1)
        page_issues.append(f"Title trimmed {len(t)} to {len(nt)}")
        changed = True
    if d and len(d) > 162:
        nd = d[:158].rsplit(" ", 1)[0] + "..."
        content = content.replace(f'<meta name="description" content="{d}"',
                                  f'<meta name="description" content="{nd}"', 1)
        page_issues.append(f"Desc trimmed {len(d)} to {len(nd)}")
        changed = True
    if og_t and t and og_t != t:
        content = content.replace(f'property="og:title" content="{og_t}"',
                                  f'property="og:title" content="{t}"', 1)
        page_issues.append("og:title synced")
        changed = True
    if not can:
        page_url = f"{BASE}/{fname}"
        content = content.replace("</head>",
            f'  <link rel="canonical" href="{page_url}" />\n</head>', 1)
        page_issues.append("Canonical added")
        changed = True
    if not t: page_issues.append("MISSING TITLE")
    if not d: page_issues.append("MISSING DESC")
    if not h1: page_issues.append("MISSING H1")
    wc = len(re.sub(r"<[^>]+>", " ", content).split())
    if wc < 300: page_issues.append(f"LOW WORD COUNT {wc}")
    return content, page_issues, changed

def fix_sitemap(all_html):
    try:
        data = gh_get("sitemap.xml")
        sitemap = decode(data)
        missing = []
        for fname in all_html:
            try:
                pd = gh_get(fname)
                pc = decode(pd)
                if "noindex" in pc: continue
            except: continue
            url = f"{BASE}/{fname}"
            if url not in sitemap:
                missing.append(url)
        if missing:
            entries = "".join(
                f"  <url><loc>{u}</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>\n"
                for u in missing)
            new_sitemap = sitemap.replace("</urlset>", entries + "</urlset>")
            gh_put("sitemap.xml", new_sitemap, data["sha"],
                   f"SEO Agent: +{len(missing)} pages to sitemap")
            FIXES.append(f"Sitemap +{len(missing)}")
            print(f"Sitemap: +{len(missing)} pages")
        else:
            print("Sitemap: complete")
    except Exception as e:
        print(f"Sitemap error: {e}")

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Workplace IT SEO Agent - {now}")
    all_html = list_html()
    print(f"Found {len(all_html)} pages")
    for fname in all_html:
        try:
            data = gh_get(fname)
            content = decode(data)
            new_content, page_issues, changed = audit(fname, content)
            if page_issues:
                ISSUES[fname] = page_issues
            if changed:
                auto = [i for i in page_issues if "MISSING" not in i and "LOW" not in i]
                try:
                    gh_put(fname, new_content, data["sha"], f"SEO Agent: fix {fname}")
                    FIXES.extend([f"{fname}: {i}" for i in auto])
                    print(f"Fixed: {fname} - {auto}")
                except Exception as e:
                    print(f"Could not push fix for {fname}: {e}")
            elif any("MISSING" in i or "LOW" in i for i in page_issues):
                flagged = [i for i in page_issues if "MISSING" in i or "LOW" in i]
                print(f"Needs attention: {fname} - {flagged}")
        except Exception as e:
            print(f"Error processing {fname}: {e}")
    fix_sitemap(all_html)
    manual = {k: [i for i in v if "MISSING" in i or "LOW" in i]
              for k, v in ISSUES.items()
              if any("MISSING" in i or "LOW" in i for i in v)}
    try:
        analysis = ask_claude(
            f"Weekly SEO audit workplaceit.net Denver MSP. "
            f"Date:{now} Pages:{len(all_html)} Auto-fixes:{len(FIXES)} Manual:{len(manual)}\n"
            f"Issues:{json.dumps(dict(list(manual.items())[:5]),indent=2)}\n"
            f"Fixes:{json.dumps(FIXES[:5],indent=2)}\n"
            "Give: 1) Health score 1-10 2) Top 3 manual fixes 3) One growth tip. Under 150 words."
        )
    except Exception as e:
        analysis = f"Claude analysis unavailable: {e}"
    report = (
        f"# SEO Report {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"Pages:{len(all_html)} | Auto-fixes:{len(FIXES)} | Need attention:{len(manual)}\n\n"
        f"## Analysis\n{analysis}\n\n"
        f"## Auto-Fixes\n" +
        ("\n".join(f"- {f}" for f in FIXES) if FIXES else "- None needed this week") +
        f"\n\n## Needs Manual Attention\n" +
        ("\n".join(f"- **{k}**: {v}" for k, v in manual.items()) or "- None")
    )
    try:
        ex = gh_get("seo-reports/latest.md")
        gh_put("seo-reports/latest.md", report, ex["sha"],
               f"SEO Report {datetime.now().strftime('%Y-%m-%d')}")
    except urllib.error.HTTPError:
        gh_create("seo-reports/latest.md", report, "SEO Report first run")
    print(f"\nDone. Auto-fixes:{len(FIXES)} | Manual:{len(manual)}")
    print(analysis)

if __name__ == "__main__":
    main()
