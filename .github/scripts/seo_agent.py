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

def gh_save(path, content, msg):
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
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"Authorization": f"token {TOKEN}", "Content-Type": "application/json"},
        method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def decode(d):
    return base64.b64decode(d["content"].replace("\n","").replace("\r","")).decode("utf-8", errors="replace")

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
            elif e["type"] == "dir" and e["name"] not in [".git",".github","node_modules","seo-reports"]:
                items.extend(list_html(e["path"]))
    except Exception as ex:
        print(f"list error {path}: {ex}")
    return items

def ask_claude(prompt):
    if not ANTHROPIC:
        print("WARNING: ANTHROPIC_API_KEY not set")
        return "No API key — skipping Claude analysis."
    # Build request carefully
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    }
    data = json.dumps(body, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "x-api-key": ANTHROPIC.strip(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.load(r)
            return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Claude API {e.code}: {body[:500]}")
        return f"Claude unavailable ({e.code}): {body[:200]}"
    except Exception as e:
        print(f"Claude error: {e}")
        return f"Claude unavailable: {e}"

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
    h1 = re.sub(r"<[^>]+>","",h1_m.group(1)).strip() if h1_m else ""
    og_t = ogt_m.group(1) if ogt_m else ""
    can = can_m.group(1) if can_m else ""
    if "noindex" in robots: return []
    if t and len(t) > 65: issues.append(f"Title too long ({len(t)})")
    if d and len(d) > 162: issues.append(f"Desc too long ({len(d)})")
    if og_t and t and og_t != t: issues.append("og:title mismatch")
    if not can: issues.append("Missing canonical")
    if not t: issues.append("MISSING TITLE")
    if not d: issues.append("MISSING DESC")
    if not h1: issues.append("MISSING H1")
    wc = len(re.sub(r"<[^>]+>"," ",content).split())
    if wc < 300: issues.append(f"LOW WORDS ({wc})")
    return issues

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== Workplace IT SEO Agent v4 - {now} ===")
    print(f"API key present: {'YES' if ANTHROPIC else 'NO'}")
    print(f"API key length: {len(ANTHROPIC)}")

    all_html = list_html()
    print(f"Found {len(all_html)} HTML pages")

    for fname in all_html:
        try:
            data = gh_get(fname)
            content = decode(data)
            issues = audit_page(fname, content)
            if issues:
                ISSUES[fname] = issues
        except Exception as e:
            print(f"Error {fname}: {e}")

    manual = {k:[i for i in v if "MISSING" in i or "LOW" in i] for k,v in ISSUES.items() if any("MISSING" in i or "LOW" in i for i in v)}
    fixable = {k:[i for i in v if "too long" in i or "mismatch" in i or "canonical" in i] for k,v in ISSUES.items() if any("too long" in i or "mismatch" in i or "canonical" in i for i in v)}

    print(f"Issues found: {len(ISSUES)} pages | Manual: {len(manual)} | Auto-fixable: {len(fixable)}")
    print("Calling Claude API...")

    summary = (
        f"workplaceit.net SEO audit {now}. "
        f"Pages:{len(all_html)} Issues:{len(ISSUES)} Manual:{len(manual)}. "
        f"Top issues: {list(manual.items())[:3]}. "
        f"Give health score 1-10, top 3 fixes, one tip. Under 100 words."
    )
    analysis = ask_claude(summary)
    print(f"Analysis: {analysis}")

    report = (
        f"# SEO Report {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"Pages:{len(all_html)} | Issues:{len(ISSUES)} | Manual:{len(manual)}\n\n"
        f"## Analysis\n{analysis}\n\n"
        f"## Manual Fixes Needed\n" +
        ("\n".join(f"- **{k}**: {v}" for k,v in manual.items()) or "- None") +
        f"\n\n## Auto-Fixable\n" +
        ("\n".join(f"- **{k}**: {v}" for k,v in fixable.items()) or "- None") +
        "\n\n---\n*WIT SEO Agent*\n"
    )

    try:
        gh_save("seo-reports/latest.md", report, f"SEO Report {datetime.now().strftime('%Y-%m-%d')}")
        print("Report saved to seo-reports/latest.md")
    except Exception as e:
        print(f"Report save error: {e}")

    print("=== Done ===")

if __name__ == "__main__":
    main()
