"""
🫀 THE HEART — Superfan Factory's always-on twin.
Runs on a free Hugging Face Space (Gradio SDK), 24/7:

  🌙 night shift   — every morning it pre-drafts posts + replies so the
                     approval queue is loaded before coffee
  🔎 hunts         — sweeps mentions/comments/DMs for your standing searches
  💤 radar         — quiet superfans + buy-intent → Telegram pings
  🫀 status UI     — a small control panel you can open anywhere

Secrets (Space → Settings → Variables and secrets):
  FANBASE_CLIENT_ID      — OAuth client id
  FANBASE_REFRESH_TOKEN  — the forever-chain seed
  TELEGRAM_BOT_TOKEN     — from @BotFather
  TELEGRAM_CHAT_ID       — your chat id (Arena can fetch it for you)
  HF_TOKEN               — (optional but recommended) write token so the
                           rotated refresh token survives restarts
"""
import json, os, re, time, threading, urllib.request, urllib.parse
from datetime import datetime, timezone
import gradio as gr

MCP_URL   = "https://api.copilot.fanbase.gg/mcp"
TOKEN_URL = "https://api.copilot.fanbase.gg/token"
CLIENT_ID = os.environ.get("FANBASE_CLIENT_ID", "")
RT_SEED   = os.environ.get("FANBASE_REFRESH_TOKEN", "")
TG_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "")
HF_TOKEN  = os.environ.get("HF_TOKEN", "")
REPO_ID   = os.environ.get("HEART_REPO", "")            # e.g. "Bigmanmarsh/superfan-heart"
HERE      = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE= os.path.join(HERE, "refresh_token.txt")
HUNT_FILE = os.path.join(HERE, "hunts.json")
LOG       = []

_state = {"access_token": None, "expires_at": 0}

def log(msg):
    LOG.insert(0, f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}")
    del LOG[40:]
    print(msg, flush=True)

# ─── persistence (survive restarts via HF repo commits) ──────────────────
def persist(filename, content):
    try:
        with open(os.path.join(HERE, filename), "w") as f:
            f.write(content)
        if HF_TOKEN and REPO_ID:
            from huggingface_hub import HfApi
            HfApi(token=HF_TOKEN).upload_file(path_or_fileobj=os.path.join(HERE, filename),
                path_in_repo=filename, repo_id=REPO_ID, repo_type="space",
                commit_message=f"heartbeat: persist {filename}")
    except Exception as e:
        log(f"persist {filename} failed: {e}")

def load_file(filename, default):
    try:
        return open(os.path.join(HERE, filename)).read().strip()
    except Exception:
        return default

# ─── FanBase MCP client ──────────────────────────────────────────────────
def mint():
    global _state
    rt = load_file("refresh_token.txt", RT_SEED) or RT_SEED
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt,
                                   "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    r = json.loads(urllib.request.urlopen(req, timeout=20).read())
    _state["access_token"] = r["access_token"]
    _state["expires_at"] = time.time() + r.get("expires_in", 3600) - 120
    if r.get("refresh_token"):
        persist("refresh_token.txt", r["refresh_token"])
    log(f"auth minted ({r.get('expires_in')}s)")
    return r

def mcp(tool, args=None):
    if not _state["access_token"] or time.time() > _state["expires_at"]:
        mint()
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": tool, "arguments": args or {}}}).encode()
    req = urllib.request.Request(MCP_URL, data=payload, headers={
        "Authorization": f"Bearer {_state['access_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    raw = urllib.request.urlopen(req, timeout=40).read().decode()
    m = re.search(r"data: (.+)", raw)
    d = json.loads(m.group(1) if m else raw)
    if "error" in d:
        raise RuntimeError(str(d["error"])[:150])
    texts = [c.get("text", "") for c in d.get("result", {}).get("content", [])
             if c.get("type") == "text"]
    if texts and texts[0].strip()[:1] in "[{":
        return json.loads(texts[0])
    return texts[0] if texts else None

def telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        log(f"(no telegram) {text[:80]}"); return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=15)
    except Exception as e:
        log(f"telegram failed: {e}")

# ─── hunts ────────────────────────────────────────────────────────────────
def load_hunts():
    try:
        return json.load(open(HUNT_FILE))
    except Exception:
        return []

def save_hunts(h):
    persist("hunts.json", json.dumps(h, indent=1))

STOP = set(("the a an and or for with that this from have has are was were you your our "
            "their about into when will can could should would just now new get got but not "
            "all any out too very much more most some such than then there here what which "
            "who whose where why how also its im me my we they them he she it is be been "
            "being do does did so if of on at to in by as").split())

def keywords_of(q):
    return list(dict.fromkeys(w for w in re.split(r"[^a-z0-9#]+", q.lower())
                              if len(w) >= 3 and w not in STOP))[:8]

# ─── the jobs ─────────────────────────────────────────────────────────────
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

def sweep():
    """Hunts + buy-intent + quiet fans → Telegram."""
    texts = collect_texts()
    hunts = load_hunts()
    found_any = False
    for h in hunts:
        if not h.get("active"): continue
        for t in texts:
            low = t.lower()
            hits = [k for k in h["keywords"] if k in low]
            need = 1 if len(h["keywords"]) == 1 else min(2, len(h["keywords"]))
            if hits and len(hits) >= need and not any(
                    f["text"][:60] == t[:60] for f in h.get("finds", [])):
                h.setdefault("finds", []).insert(0, {"text": t[:200], "ts": datetime.now(timezone.utc).isoformat()})
                telegram(f"🔎 HOUND FOUND for “{h['query'][:40]}”:\n\n{t[:300]}")
                found_any = True
    if found_any: save_hunts(hunts)
    for t in texts:
        if any(b in t.lower() for b in BUY):
            telegram(f"💰 possible buy-intent:\n\n{t[:300]}"); break
    try:
        crm = mcp("list_crm", {"limit": 10})
        for f in (crm or {}).get("fans", []):
            try:
                d = (datetime.now(timezone.utc) -
                     datetime.fromisoformat(f["lastActive"].replace("Z", "+00:00"))).days
            except Exception:
                continue
            if d >= 5 and not f.get("_q5"):
                u = (f.get("profile") or {}).get("username") or f.get("name")
                telegram(f"💤 @{u} (score {f.get('engagementScore','?')}) quiet {d} days — win-back time?")
                f["_q5"] = True   # per-sweep guard (best effort)
    except Exception as e:
        log(f"crm: {e}")
    log(f"sweep done — {len(texts)} texts sniffed, {len(hunts)} hounds")

def night_shift():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if load_file("nightshift.txt", "") == today:
        return
    n = 0
    try:
        g = mcp("trigger_skill", {"skill": "post-creator-x",
            "instructions": "overnight recap: celebrate active fans by handle and invite quiet ones back"})
        ex = (g or {}).get("executionId")
        for _ in range(12):
            time.sleep(10)
            c = mcp("check_skill_generation", {"executionId": ex})
            if isinstance(c, dict) and c.get("status") != "running":
                n = len(c.get("recommendations", [])); break
    except Exception as e:
        log(f"night shift drafts: {e}")
    telegram(f"🌙 Night shift done: {n} fresh draft(s) waiting in your Superfan Factory queue. "
             f"Coffee first, approvals second.")
    persist("nightshift.txt", today)

def heart_loop():
    last_sweep = 0
    while True:
        time.sleep(30)
        try:
            now = datetime.now(timezone.utc)
            if time.time() - last_sweep > 30 * 60:
                last_sweep = time.time(); sweep()
            if now.hour == 6:          # 08:00 NL summer
                night_shift()
        except Exception as e:
            log(f"loop error: {e}")

# ─── Gradio control panel ────────────────────────────────────────────────
def ui_status():
    hunts = load_hunts()
    lines = [f"🫀 Heart status — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             f"auth: {'OK' if _state['access_token'] else 'pending'}",
             f"telegram: {'OK' if TG_TOKEN and TG_CHAT else 'NOT SET'}",
             f"hounds active: {sum(1 for h in hunts if h.get('active'))}",
             "— recent activity —"] + LOG[:15]
    return "\n".join(lines)

def ui_add_hunt(q):
    if len(q.strip()) < 3:
        return ui_status(), "give the hound a scent (a few words)"
    hunts = load_hunts()
    hunts.append({"query": q.strip(), "keywords": keywords_of(q), "active": True,
                  "finds": [], "created": datetime.now(timezone.utc).isoformat()})
    save_hunts(hunts)
    telegram(f"🐕 hound released: “{q.strip()}”")
    return ui_status(), f"hound released 🐕 ({len(keywords_of(q))} keywords armed)"

def ui_toggle(i):
    hunts = load_hunts()
    if 0 <= i < len(hunts):
        hunts[i]["active"] = not hunts[i]["active"]; save_hunts(hunts)
    return ui_status()

def ui_scan():
    sweep()
    return ui_status()

def ui_ping():
    telegram("🫀 heartbeat test — if you can read this, the Heart is alive.")
    return ui_status()

with gr.Blocks(title="Superfan Heart") as demo:
    gr.Markdown("## 🫀 Superfan Heart\nThe always-on twin: hunts 24/7, drafts while you sleep, pings your Telegram.")
    with gr.Row():
        with gr.Column():
            q = gr.Textbox(label="New hunt (what should the hound smell for?)",
                           placeholder="e.g. paid collab, brand deal, creator jobs web3")
            add = gr.Button("release the hound 🐕")
            msg = gr.Textbox(label="result", interactive=False)
        with gr.Column():
            i = gr.Number(label="hunt # to pause/resume", precision=0, value=0)
            tog = gr.Button("toggle")
            scan = gr.Button("scan now 🔍")
            ping = gr.Button("test Telegram 📲")
    status = gr.Textbox(label="status", lines=22, interactive=False, value=ui_status)
    add.click(ui_add_hunt, inputs=q, outputs=[status, msg])
    tog.click(ui_toggle, inputs=i, outputs=status)
    scan.click(ui_scan, outputs=status)
    ping.click(ui_ping, outputs=status)

threading.Thread(target=heart_loop, daemon=True).start()
demo.launch()
