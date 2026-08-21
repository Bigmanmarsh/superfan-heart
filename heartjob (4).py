#!/usr/bin/env python3
"""
🫀 Superfan Heart — GitHub Actions edition.
Runs every 20 min on GitHub's free runners (public repo = free forever).

Commands (text them to your bot in Telegram):
  /hunt <words>     release a hound
  /list             show hounds + finds
  /pause <n>        pause hound #n
  /resume <n>       resume hound #n
  /off <n>          call off hound #n
  /status           heartbeat status

Secrets (repo → Settings → Secrets and variables → Actions):
  FANBASE_CLIENT_ID, FANBASE_REFRESH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json, os, re, time, urllib.request, urllib.parse
from datetime import datetime, timezone

MCP = "https://api.copilot.fanbase.gg/mcp"
TOK = "https://api.copilot.fanbase.gg/token"
CID = os.environ.get("FANBASE_CLIENT_ID", "")
RT_SEED = os.environ.get("FANBASE_REFRESH_TOKEN", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
STATE = "state"
LOG = []

def log(m):
    LOG.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}")
    print(m, flush=True)

def load(name, default):
    try: return json.load(open(f"{STATE}/{name}.json"))
    except Exception: return default

def save(name, obj):
    os.makedirs(STATE, exist_ok=True)
    json.dump(obj, open(f"{STATE}/{name}.json", "w"), indent=1)

def tg(text):
    if not (TG_TOKEN and TG_CHAT):
        log(f"(no telegram) {text[:80]}"); return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT, "text": text}).encode(),
            headers={"Content-Type": "application/json"}), timeout=15)
    except Exception as e:
        log(f"telegram failed: {e}")

# ─── FanBase MCP client ──────────────────────────────────────────────────
_access = {"token": None, "exp": 0}

def mint():
    rt = load("token", {}).get("rt") or RT_SEED
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt,
                                   "client_id": CID}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(TOK, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"}), timeout=20).read())
    _access["token"] = r["access_token"]
    _access["exp"] = time.time() + r.get("expires_in", 3600) - 120
    if r.get("refresh_token"):
        save("token", {"rt": r["refresh_token"]})
    log("auth minted")

def mcp(tool, args=None):
    if not _access["token"] or time.time() > _access["exp"]:
        mint()
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": tool, "arguments": args or {}}}).encode()
    raw = urllib.request.urlopen(urllib.request.Request(MCP, data=payload, headers={
        "Authorization": f"Bearer {_access['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}), timeout=60).read().decode()
    m = re.search(r"data: (.+)", raw)
    d = json.loads(m.group(1) if m else raw)
    if "error" in d:
        raise RuntimeError(str(d["error"])[:150])
    texts = [c.get("text", "") for c in d.get("result", {}).get("content", [])
             if c.get("type") == "text"]
    if texts and texts[0].strip()[:1] in "[{":
        return json.loads(texts[0])
    return texts[0] if texts else None

def run_skill_and_wait(tool, args, check_tool):
    g = mcp(tool, args)
    ex = (g or {}).get("executionId") if isinstance(g, dict) else None
    if not ex:
        return 0
    for _ in range(12):
        time.sleep(10)
        c = mcp(check_tool, {"executionId": ex})
        if isinstance(c, dict) and c.get("status") and c["status"] != "running":
            return len(c.get("recommendations", []))
    return 0

# ─── Telegram commands (control panel) ────────────────────────────────────
STOP = set(("the a an and or for with that this from have has are was were you your our "
            "their about into when will can could should would just now new get got but not "
            "all any out too very much more most some such than then there here what which "
            "who whose where why how also its im me my we they them he she it is be been "
            "being do does did so if of on at to in by as").split())

def keywords_of(q):
    return list(dict.fromkeys(w for w in re.split(r"[^a-z0-9#]+", q.lower())
                              if len(w) >= 3 and w not in STOP))[:8]

HELP = ("🫀 I'm your Superfan Heart. Text me:\n"
        "🐕 /hunt <words> — release a hound (e.g. /hunt brand deal)\n"
        "📜 /list — my hounds + finds\n"
        "🚫 /off <n> · 😴 /pause <n> · 🟢 /resume <n>\n"
        "⚖️ /queue — what's waiting for your approval\n"
        "✍️ /draft — draft posts + replies now\n"
        "💜 /fans — top superfans\n"
        "📊 /numbers — follower stats\n"
        "🌡️ /mood — community mood scan\n"
        "🫀 /status — heartbeat status")

def handle_commands():
    offs = load("tg", {"offset": 0})
    try:
        ups = json.loads(urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offs['offset']}&timeout=0",
            timeout=15).read())
    except Exception as e:
        log(f"getUpdates failed: {e}"); return
    hunts = load("hunts", [])
    changed = False
    for u in ups.get("result", []):
        offs["offset"] = max(offs["offset"], u["update_id"] + 1)
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != TG_CHAT:
            continue
        text = (m.get("text") or "").strip()
        low = text.lower()
        try:
            if low.startswith("/hunt ") or low.startswith("/hunt@"):
                q = text.split(" ", 1)[1].strip() if " " in text else ""
                if len(q) < 3:
                    tg("give the hound a scent — e.g. /hunt paid collab brand deal")
                else:
                    hunts.append({"query": q, "keywords": keywords_of(q),
                                  "active": True, "finds": [], "created": datetime.now(timezone.utc).isoformat()})
                    tg(f"🐕 hound released: “{q}”\nsniffing for: {' · '.join(keywords_of(q))}")
                    changed = True
            elif low.startswith("/list"):
                if not hunts: tg("no hounds yet — try /hunt paid collab")
                else: tg("\n".join(f"{'🟢' if h['active'] else '😴'} {i+1}. {h['query']} — {len(h.get('finds',[]))} find(s)"
                                   for i, h in enumerate(hunts)))
            elif low.startswith("/pause ") or low.startswith("/resume ") or low.startswith("/off "):
                cmd, _, num = low.partition(" ")
                i = int(num.strip() or "0") - 1
                if 0 <= i < len(hunts):
                    if cmd == "/pause": hunts[i]["active"] = False; tg(f"😴 hound #{i+1} napping")
                    elif cmd == "/resume": hunts[i]["active"] = True; tg(f"🟢 hound #{i+1} back on the scent")
                    else: hunts.pop(i); tg(f"🐕‍🦺 hound called off")
                    changed = True
            elif low.startswith("/status"):
                last = load("last", {})
                act = sum(1 for h in hunts if h.get("active"))
                tg(f"🫀 last beat: {str(last.get('at','?'))[:19]} UTC\nhound(s): {act} active · beats every 20 min")
            elif low.startswith("/help"):
                tg(HELP)
            elif low.startswith("/ping"):
                tg("🫀 alive and sniffing 👃")
            elif low.startswith("/queue"):
                try:
                    r = mcp("list_recommendations", {})
                    items = (r or {}).get("recommendations", [])
                    if not items: tg("⚖️ queue is empty — try /draft")
                    else: tg("⚖️ waiting for your approval:\n\n" + "\n".join(
                        f"{i+1}. " + str(x.get("message") or (x.get("data") or {}).get("message") or "(no text)")[:70]
                        for i, x in enumerate(items[:5])))
                except Exception as e: tg(f"⚠️ {e}")
            elif low.startswith("/draft"):
                tg("✍️ on it — reading your community, ~90 seconds…")
                try:
                    n = run_skill_and_wait("trigger_skill", {"skill": "post-creator-x",
                        "instructions": "celebrate the most engaged community members by handle, and re-engage quiet followers"},
                        "check_skill_generation")
                    m = run_skill_and_wait("generate_reply_recommendations",
                        {"platform": "twitter"}, "check_reply_generation")
                    tg(f"✨ done: {n} post draft(s) + {m} reply draft(s) waiting in your approval queue")
                except Exception as e: tg(f"⚠️ draft failed: {e}")
            elif low.startswith("/fans"):
                try:
                    r = mcp("list_crm", {"limit": 5})
                    fans = (r or {}).get("fans", [])
                    tg("💜 top superfans:\n" + "\n".join(
                        f"• @{(f.get('profile') or {}).get('username','?')} — score {f.get('engagementScore','?')}"
                        for f in fans) if fans else "💜 no fans yet")
                except Exception as e: tg(f"⚠️ {e}")
            elif low.startswith("/numbers"):
                try:
                    a = mcp("get_account_analytics", {})
                    accs = (a or {}).get("accounts", [])
                    tg("📊 your accounts:\n" + "\n".join(
                        f"• {x.get('platform')}: {x.get('followers')} followers · {x.get('likes')} likes"
                        for x in accs) if accs else "📊 no accounts found")
                except Exception as e: tg(f"⚠️ {e}")
            elif low.startswith("/mood"):
                tg("🌡️ scanning community mood — ~60 seconds…")
                try:
                    n = run_skill_and_wait("trigger_skill",
                        {"skill": "sentiment-cross-platform"}, "check_skill_generation")
                    tg(f"🌡️ mood scan complete ({n} item(s)) — full write-up in your FanBase Activity tab")
                except Exception as e: tg(f"⚠️ mood scan failed: {e}")
            elif low.startswith("/start"):
                tg(HELP)
        except Exception as e:
            tg(f"⚠️ {e}")
    save("tg", offs)
    if changed: save("hunts", hunts)

# ─── The jobs ─────────────────────────────────────────────────────────────
BUY = ["price", "cost", "how much", "buy", "merch", "shop", "discount", "code", "available", "order"]

def collect_texts():
    texts = []
    def walk(o, d):
        if d > 4 or o is None: return
        if isinstance(o, list):
            for v in o: walk(v, d + 1)
        elif isinstance(o, dict):
            t = " ".join(str(o.get(k, "") or "") for k in
                         ("text", "message", "content", "comment", "lastMessage", "preview")).strip()
            if len(t) > 12: texts.append(t)
            for v in o.values(): walk(v, d + 1)
    for tool, args in [("lookup_socials", {"apps": ["twitter"], "includeMentions": True,
                                           "topRepliesCount": 5, "maxResults": 10}),
                       ("list_conversations", {}),
                       ("query_activity", {"limit": 12})]:
        try: walk(mcp(tool, args), 0)
        except Exception as e: log(f"{tool}: {e}")
    return texts

def web_sniff():
    """Open-web noses: Hacker News + RemoteOK. Finds links, not just vibes."""
    hunts = load("hunts", [])
    out = []
    for h in hunts:
        if not h.get("active"): continue
        kw = [k.lower() for k in h.get("keywords", [])]
        if not kw: continue
        for k in kw[:2]:
            try:
                url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(
                    {"query": k, "tags": "(story,comment)", "hitsPerPage": 15})
                d = json.loads(urllib.request.urlopen(url, timeout=20).read())
                for hit in d.get("hits", []):
                    raw = str(hit.get("title") or hit.get("story_title") or hit.get("comment_text") or "")
                    txt = re.sub(r"<[^>]+>", " ", raw)[:220].strip()
                    if len(txt) > 25 and any(k2 in txt.lower() for k2 in kw):
                        out.append({"hunt": h["query"], "text": txt,
                                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                                    "source": "Hacker News"})
            except Exception: pass
        try:
            req = urllib.request.Request("https://remoteok.com/api", headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) superfan-heart/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            for j in (d[1:] if isinstance(d, list) else []):
                if not isinstance(j, dict) or not j.get("position"): continue
                tags = " ".join(str(t) for t in (j.get("tags") or []) if t)
                txt = f"{j['position']} @ {j.get('company', '?')} {tags}"
                if any(k in txt.lower() for k in kw):
                    out.append({"hunt": h["query"], "text": txt[:200],
                                "url": j.get("url") or "https://remoteok.com/remote-jobs",
                                "source": "RemoteOK"})
        except Exception: pass
    return out

def sweep():
    texts = collect_texts()
    hunts = load("hunts", [])
    alerted = load("alerted", {"keys": []})
    keys = set(alerted["keys"])
    found_any = False
    for h in hunts:
        if not h.get("active"): continue
        for t in texts:
            low = t.lower()
            hits = [k for k in h["keywords"] if k in low]
            need = 1 if len(h["keywords"]) == 1 else min(2, len(h["keywords"]))
            sig = "hunt:" + str(h.get("created")) + ":" + t[:60]
            if len(hits) >= need and sig not in keys:
                h.setdefault("finds", []).insert(0, {"text": t[:200],
                    "ts": datetime.now(timezone.utc).isoformat()})
                keys.add(sig)
                tg(f"🔎 HOUND FOUND for “{h['query'][:40]}”:\n\n{t[:300]}")
                found_any = True
    for t in texts:
        low = t.lower()
        if any(b in low for b in BUY):
            sig = "buy:" + t[:60]
            if sig not in keys:
                keys.add(sig)
                tg(f"💰 possible buy-intent:\n\n{t[:300]}")
                found_any = True
    try:
        crm = mcp("list_crm", {"limit": 10})
        for f in (crm or {}).get("fans", []):
            u = (f.get("profile") or {}).get("username") or f.get("name")
            try:
                d = (datetime.now(timezone.utc) -
                     datetime.fromisoformat(f["lastActive"].replace("Z", "+00:00"))).days
            except Exception:
                continue
            sig = f"quiet:{u}:{d // 5}"
            if d >= 5 and sig not in keys:
                keys.add(sig)
                tg(f"💤 @{u} (score {f.get('engagementScore','?')}) quiet {d} days — win-back time?")
    except Exception as e:
        log(f"crm: {e}")
    alerted["keys"] = list(keys)[-300:]
    save("alerted", alerted)
    save("hunts", hunts)
    webn = 0
    for w in web_sniff():                      # 🌐 the open web
        sig = "web:" + str(w["url"])[:80]
        if sig not in keys:
            keys.add(sig)
            tg(f"🌐 WEB FIND for “{w['hunt'][:40]}”:\n\n{w['text'][:250]}\n\n{w['url']}")
            webn += 1
    alerted["keys"] = list(keys)[-300:]
    save("alerted", alerted)
    log(f"sweep done — {len(texts)} texts sniffed, {len(hunts)} hounds, found={found_any}, web_finds={webn}")

def night_shift():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if load("night", {}).get("day") == today:
        return
    n = 0
    try:
        n += run_skill_and_wait("trigger_skill",
            {"skill": "post-creator-x", "instructions":
             "overnight recap: celebrate active fans by handle and invite quiet ones back"},
            "check_skill_generation")
        n += run_skill_and_wait("generate_reply_recommendations",
            {"platform": "twitter"}, "check_reply_generation")
    except Exception as e:
        log(f"night shift: {e}")
    tg(f"🌙 Night shift done: {n} fresh draft(s) waiting in your Superfan Factory queue.")
    save("night", {"day": today})

if __name__ == "__main__":
    log("🫀 heartbeat begin")
    handle_commands()
    sweep()
    if datetime.now(timezone.utc).hour in (6, 7):
        night_shift()
    log("🫀 heartbeat complete")
    save("last", {"at": datetime.now(timezone.utc).isoformat(), "log": LOG[:20]})
