#!/usr/bin/env python3
"""
🫀 Superfan Heart — GitHub Actions edition.
Runs every 20 min on GitHub's free runners (public repo = free forever).

Commands (text them to your bot in Telegram):
  /status           heartbeat status
  /queue            what's awaiting approval
  /draft            draft posts + replies now
  /fans             top superfans
  /numbers          follower stats
  /mood             community mood scan
  /nightshift       force the morning drafts
  /help             full command list

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
            "want wanting need needing looking scheduled schedule tomorrow today tonight "
            "yesterday week month day someone anybody anything something stuff things trying "
            "try show tell give help might could us were has had does did done make made take "
            "took using use really actual actually kinda sort plus top best good great big small").split())

def keywords_of(q):
    return list(dict.fromkeys(w for w in re.split(r"[^a-z0-9#]+", q.lower())
                              if len(w) >= 3 and w not in STOP))[:8]

HELP = ("🫀 I'm your Superfan Heart. Text me:\n"
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
    for u in ups.get("result", []):
        offs["offset"] = max(offs["offset"], u["update_id"] + 1)
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != TG_CHAT:
            continue
        text = (m.get("text") or "").strip()
        low = text.lower()
        try:
            if low.startswith("/status"):
                last = load("last", {})
                tg(f"🫀 last beat: {str(last.get('at','?'))[:19]} UTC · beats every ~5 min\nwatchers: quiet-fan radar + buy-intent, always on")
            elif low.startswith("/help"):
                tg(HELP)
            elif low.startswith("/ping"):
                tg("🫀 alive and beating")
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
            elif low.startswith("/xray"):
                tg("🩻 x-raying your channels — raw shapes incoming (for Arena to read)…")
                for tool, args in [("lookup_socials", {"apps": ["twitter", "twitch"], "includeMentions": True,
                                                      "topRepliesCount": 2, "maxResults": 3}),
                                   ("lookup_socials", {"apps": ["discord-bot"], "maxResults": 3}),
                                   ("list_conversations", {}),
                                   ("query_activity", {"limit": 5})]:
                    try:
                        r = mcp(tool, args)
                        log(f"XRAY {tool} {json.dumps(args)[:60]}: {json.dumps(r)[:600]}")
                    except Exception as e:
                        log(f"XRAY {tool} ERROR: {e}")
                tg("🩻 x-ray complete — shapes logged to the repo. Tell Arena: “read the x-ray”.")
            elif low.startswith("/nightshift"):
                tg("🌙 forcing the night shift — drafting now, ~2 min…")
                try:
                    n = run_skill_and_wait("trigger_skill", {"skill": "post-creator-x",
                        "instructions": "overnight recap: celebrate active fans by handle and invite quiet ones back"},
                        "check_skill_generation")
                    m = run_skill_and_wait("generate_reply_recommendations",
                        {"platform": "twitter"}, "check_reply_generation")
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    tg(f"🌙 night shift done: {n} post draft(s) + {m} reply draft(s) waiting in your approval queue.")
                    save("night", {"day": today, "forced": True})
                except Exception as e:
                    tg(f"⚠️ forced night shift failed: {e}")
            elif low.startswith("/diag"):
                tg("🔍 deep diagnostic running — full AI response dump in ~90s…")
                try:
                    g = mcp("trigger_skill", {"skill": "post-creator-x",
                        "instructions": "celebrate the most engaged community members by handle"})
                    ex = (g or {}).get("executionId")
                    log(f"DIAG trigger: {json.dumps(g)[:300]}")
                    c = None
                    for i in range(15):
                        time.sleep(10)
                        c = mcp("check_skill_generation", {"executionId": ex})
                        if isinstance(c, dict) and c.get("status") and c["status"] != "running":
                            break
                    log(f"DIAG check FULL: {json.dumps(c)[:1500]}")
                    tg("🔍 diagnostic complete — raw response logged. Tell Arena: read the diag.")
                except Exception as e:
                    log(f"DIAG error: {e}")
                    tg(f"⚠️ diag failed: {e}")
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

# ─── The jobs ─────────────────────────────────────────────────────────────
BUY = ["price", "cost", "how much", "buy", "merch", "shop", "discount", "code", "available", "order"]

def collect_texts():
    texts = []
    def walk(o, d):
        if d > 5 or o is None: return
        if isinstance(o, list):
            for v in o: walk(v, d + 1)
        elif isinstance(o, dict):
            t = " ".join(str(o.get(k, "") or "") for k in
                         ("text", "message", "content", "comment", "lastMessage", "preview", "tweetText")).strip()
            if len(t) > 12: texts.append(t)
            for v in o.values(): walk(v, d + 1)
    for tool, args in [("lookup_socials", {"apps": ["twitter", "instagram", "discord-bot"],
                                           "includeMentions": True, "topRepliesCount": 5, "maxResults": 10}),
                       ("list_conversations", {}),
                       ("query_activity", {"limit": 25})]:
        try: walk(mcp(tool, args), 0)
        except Exception as e: log(f"{tool}: {e}")
    return texts

def sweep():
    texts = collect_texts()
    alerted = load("alerted", {"keys": []})
    keys = set(alerted["keys"])
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
    save("alerted", alerted)
    log(f"sweep done — {len(texts)} texts sniffed")

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
