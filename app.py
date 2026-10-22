import streamlit as st
import requests
import os
import re
import io
import json
import math
import time
import asyncio
import difflib
import numpy as np
import pandas as pd
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime, timezone, timedelta

st.set_page_config(page_title="AI Sharp Bettor", page_icon="📈", layout="wide")

# ==========================================================
# MCP IMPORTS (SSE client)
# ==========================================================
try:
    from mcp.client.sse import sse_client
        return os.getenv(key)

GOOGLE_SHEET_URL = get_secret("GOOGLE_SHEET_URL")
GROQ_API_KEY = get_secret("GROQ_API_KEY")
if not all([GOOGLE_SHEET_URL, GROQ_API_KEY]):
    st.error("⚠️ Missing Secrets! Please check your Streamlit Secrets configuration.")
    st.stop()

MODEL = "openai/gpt-oss-20b"
MCP_SSE_URL = "https://livescoremcp.com/sse"
LEAGUE_AVG_GOALS = 1.35
MIN_MATCHES = 5          # finished matches needed per team before the model is trusted
DC_RHO = -0.08           # Dixon-Coles low-score correction
MARGIN_BUFFER = 0.07     # extra edge demanded over fair odds (no market price to compare)
FORM_TTL = 6 * 3600

# ==========================================================
# PURE LOGIC (no Streamlit, no network)
# ==========================================================
_STOP = {"fc", "afc", "cf", "sc", "ac", "club", "de", "the", "cd", "fk", "sk", "gnk", "tc"}


def _norm(n):
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", str(n).lower()).split() if t not in _STOP]
    return " ".join(toks)


def name_sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        r = max(r, 0.85)
    return r


def unwrap_error(e):
    if hasattr(e, "exceptions") and e.exceptions:
        return unwrap_error(e.exceptions[0])
    return e


def lenient_json(text):
    """Parse the first JSON value in text. Tolerates leading/trailing text and control characters.
    Returns (obj, error). On failure the error says where and why, so truncation is visible."""
    t = (text or "").strip()
    if not t:
        return None, "empty response"
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        return None, f"no JSON found; starts with {t[:80]!r}"
    t = t[min(starts):]
    try:
        obj, _ = json.JSONDecoder(strict=False).raw_decode(t)
        return obj, None
    except json.JSONDecodeError as e:
        return None, f"JSON error at char {e.pos} of {len(t)} ({e.msg}); ends with ...{t[-60:]!r}"


def tool_text(res):
    """Join ALL text blocks of an MCP tool result (the old code only read the first)."""
    return "".join(getattr(c, "text", "") or "" for c in (getattr(res, "content", None) or []))


def flat_items(d, prefix="", depth=0):
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 3:
            out.update(flat_items(v, path + ".", depth + 1))
        elif not isinstance(v, (dict, list)):
            out[path] = v
    return out


_H = r"(?:home|hometeam|localteam|team1|hteam)"
_A = r"(?:away|awayteam|visitorteam|visitor|team2|ateam)"
NAME_H = re.compile(rf"^{_H}(?:name|teamname)?$")
NAME_A = re.compile(rf"^{_A}(?:name|teamname)?$")
SC_H = re.compile(rf"^(?:{_H}|h)(?:score|goals|goal|ft|ftscore)$|^(?:score|goals|goal)(?:ft|fulltime|final)?(?:home|h|1)$|^hg$")
SC_A = re.compile(rf"^(?:{_A}|a)(?:score|goals|goal|ft|ftscore)$|^(?:score|goals|goal)(?:ft|fulltime|final)?(?:away|a|2)$|^ag$")
ID_H = re.compile(rf"^{_H}(?:id|idgs)$")
ID_A = re.compile(rf"^{_A}(?:id|idgs)$")
SC_COMBINED = re.compile(r"^(?:score|result|ft|ftscore|finalscore|fulltime)$")
DONE = {"ft", "finished", "full time", "fulltime", "aet", "pen", "ended", "after et", "after pen", "final", "fin", "match finished"}


def _intlike(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return float(v).is_integer()
    return isinstance(v, str) and re.fullmatch(r"\d+", v.strip() or "x") is not None


def _first(f, pat, cond=lambda v: True):
    for k, v in f.items():
        if pat.match(k) and cond(v):
            return v
    return None


def parse_fixture(fx):
    """Schema-tolerant fixture parser. Returns a dict (fields may be None) or None."""
    if not isinstance(fx, dict):
        return None
    f = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in flat_items(fx).items()}
    nm = lambda v: isinstance(v, str) and v.strip() and not v.strip().isdigit()
    home, away = _first(f, NAME_H, nm), _first(f, NAME_A, nm)
    hs, as_ = _first(f, SC_H, _intlike), _first(f, SC_A, _intlike)
    if hs is None or as_ is None:
        comb = _first(f, SC_COMBINED, lambda v: isinstance(v, str) and re.fullmatch(r"\s*\d+\s*[-:–]\s*\d+\s*", v) is not None)
        if comb:
            hs, as_ = re.split(r"[-:–]", comb.strip())[:2]
    hs = int(float(hs)) if hs is not None and _intlike(hs) else None
    as_ = int(float(as_)) if as_ is not None and _intlike(as_) else None
    status = None
    for k, v in f.items():
        if re.match(r"^(status|state|statustext|matchstatus|st)$", k) and isinstance(v, str):
            status = v.strip()
            break
    date = None
    for k, v in f.items():
        if re.search(r"date|kickoff|timestamp|utc", k) and isinstance(v, (str, int, float)):
            d = pd.to_datetime(v, errors="coerce", utc=True)
            if pd.notna(d):
                date = d
                break
    if status is not None:
        s = status.lower()
        finished = s in DONE or s.startswith("ft")
    else:
        finished = hs is not None and as_ is not None and date is not None and date < pd.Timestamp.now(tz="UTC")
    tm = _first(f, re.compile(r"^time$"), lambda v: isinstance(v, str))
    return {"home": home.strip() if home else None, "away": away.strip() if away else None, "hs": hs, "as_": as_,
            "finished": finished, "status": status, "date": date, "time": tm or "",
            "home_id": _first(f, ID_H), "away_id": _first(f, ID_A)}


def collect_fixtures(obj, league=""):
    """Walk any JSON and collect fixture-like dicts, remembering the nearest league/competition name."""
    if isinstance(obj, dict):
        pf = parse_fixture(obj)
        if pf and pf["home"] and pf["away"]:
            pf["league"] = league
            return [pf]
        lg = league
        for k in ("league", "competition", "tournament", "leaguename", "competitionname", "title", "name"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                lg = v.strip()
                break
        out = []
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out += collect_fixtures(v, lg)
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out += collect_fixtures(v, league)
        return out
    return []


def games_from_team_obj(team_obj, search_name):
    """Finished matches for this team from the get_team payload -> list of {date, home, gf, ga}."""
    canon = team_obj.get("teamname") if isinstance(team_obj, dict) else None
    names = [n for n in (canon, search_name) if n]
    games = []
    for m in collect_fixtures(team_obj):
        if not (m["finished"] and m["hs"] is not None and m["as_"] is not None):
            continue
        sh = max(name_sim(n, m["home"]) for n in names)
        sa = max(name_sim(n, m["away"]) for n in names)
        if max(sh, sa) < 0.6:
            continue
        is_home = sh >= sa
        games.append({"date": m["date"], "home": is_home,
                      "gf": m["hs"] if is_home else m["as_"], "ga": m["as_"] if is_home else m["hs"]})
    return games, canon


def form_from_games(games, name):
    n = len(games)
    if all(g["date"] is not None for g in games):
        games = sorted(games, key=lambda g: g["date"])
        w = [0.92 ** (n - 1 - i) for i in range(n)]
    else:
        w = [1.0] * n                      # no dates available: equal weights

    def wavg(vals, ws):
        return sum(v * x for v, x in zip(vals, ws)) / sum(ws) if ws else 0.0

    def agg(idx):
        return (wavg([games[i]["gf"] for i in idx], [w[i] for i in idx]),
                wavg([games[i]["ga"] for i in idx], [w[i] for i in idx]), len(idx))

    allx = list(range(n))
    hx = [i for i in allx if games[i]["home"]]
    ax = [i for i in allx if not games[i]["home"]]
    att, dfn, _ = agg(allx)
    ah, dh, nh = agg(hx)
    aa, da, na = agg(ax)
    last5 = games[-5:]
    return {"name": name, "n": n, "att": att, "def": dfn, "n_home": nh, "att_home": ah, "def_home": dh,
            "n_away": na, "att_away": aa, "def_away": da,
            "form": "".join("W" if g["gf"] > g["ga"] else "D" if g["gf"] == g["ga"] else "L" for g in last5),
            "over25": sum(1 for g in games if g["gf"] + g["ga"] > 2.5) / n,
            "btts": sum(1 for g in games if g["gf"] > 0 and g["ga"] > 0) / n}


def pick_team_id(sobj, name):
    """Choose the best TEAM match from search results (ignores players/competitions when typed)."""
    bad = ("player", "competition", "league", "tournament", "cup", "coach", "referee", "venue", "stadium")
    cands = []

    def visit(o, kind=""):
        if isinstance(o, dict):
            nm = next((o[k] for k in ("name", "teamname", "title", "label") if isinstance(o.get(k), str)), None)
            tid = next((o[k] for k in ("id", "id_gs", "team_id", "teamid") if o.get(k) not in (None, "")), None)
            typ = str(next((o[k] for k in ("type", "kind", "category", "entity", "entitytype") if isinstance(o.get(k), str)), kind)).lower()
            if nm and tid is not None:
                cands.append((nm, tid, typ))
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    visit(v, typ or str(k).lower())
        elif isinstance(o, list):
            for v in o:
                visit(v, kind)

    visit(sobj)
    best, bs = None, 0.0
    for nm, tid, typ in cands:
        s = name_sim(name, nm)
        if any(b in typ for b in bad):
            s -= 0.5
        if s > bs:
            best, bs = tid, s
    return best if bs >= 0.55 else None


def describe(obj, depth=0, maxd=3):
    pad = "  " * depth
    lines = []
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:25]:
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}: {type(v).__name__}({len(v)})")
                if depth < maxd:
                    lines += describe(v, depth + 1, maxd)
            else:
                lines.append(f"{pad}{k}: {str(v)[:40]!r}")
    elif isinstance(obj, list) and obj:
        if isinstance(obj[0], (dict, list)):
            lines.append(f"{pad}[0]:")
            lines += describe(obj[0], depth + 1, maxd)
        else:
            lines.append(f"{pad}[0]: {str(obj[0])[:40]!r}")
    return lines


def first_fixture_sample(obj):
    """Raw text of the first fixture-like dict, to show the real schema when parsing fails."""
    def find(o):
        if isinstance(o, dict):
            if "fixtures" in o and isinstance(o["fixtures"], list) and o["fixtures"]:
                return o["fixtures"][0]
            for v in o.values():
                r = find(v) if isinstance(v, (dict, list)) else None
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r:
                    return r
        return None
    s = find(obj)
    return json.dumps(s, ensure_ascii=False)[:500] if s else "no 'fixtures' list found"


# ---- goals model (Poisson grid + Dixon-Coles) ----
def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _strength(f, venue):
    n_v = f[f"n_{venue}"]
    att = (f[f"att_{venue}"] * n_v + f["att"] * 3) / (n_v + 3)
    dfn = (f[f"def_{venue}"] * n_v + f["def"] * 3) / (n_v + 3)
    n, k = f["n"], 3
    return ((att * n + LEAGUE_AVG_GOALS * k) / (n + k), (dfn * n + LEAGUE_AVG_GOALS * k) / (n + k))


def predict(fh, fa):
    ha, hd = _strength(fh, "home")
    aa, ad = _strength(fa, "away")
    lam_h = min(max((ha + ad) / 2 * 1.05, 0.2), 4.0)
    lam_a = min(max((aa + hd) / 2 * 0.95, 0.2), 4.0)
    size = 11
    g = [[_pois(i, lam_h) * _pois(j, lam_a) for j in range(size)] for i in range(size)]
    g[0][0] *= max(1 - lam_h * lam_a * DC_RHO, 0.01)
    g[0][1] *= max(1 + lam_h * DC_RHO, 0.01)
    g[1][0] *= max(1 + lam_a * DC_RHO, 0.01)
    g[1][1] *= max(1 - DC_RHO, 0.01)
    tot = sum(sum(r) for r in g)
    return {"lam_h": lam_h, "lam_a": lam_a, "grid": [[c / tot for c in r] for r in g]}


def total_over(P, line):
    g = P["grid"]
    return sum(g[i][j] for i in range(11) for j in range(11) if i + j > line)


def fair(p):
    return round(1 / p, 2) if p > 0 else 0.0


def form_line(f):
    return (f"{f['name']}: form {f['form']}, scored {f['att']:.2f} / conceded {f['def']:.2f} per game, "
            f"Over2.5 {f['over25']*100:.0f}%, BTTS {f['btts']*100:.0f}% (last {f['n']})")


_EXCLUDE = re.compile(r"women|\bu1[5-9]\b|\bu2[0-3]\b|reserve|youth|\bw\b", re.I)


def today_wat():
    return datetime.now(timezone(timedelta(hours=1)))

# ==========================================================
# MCP LAYER (one connection per scan, not one per team)
# ==========================================================
async def _with_session(fn):
    async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await fn(session)


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def run_mcp(fn, timeout=60):
    """Returns (result, error_text). Uses wait_for so it also works on Python < 3.11."""
    try:
        return _run(asyncio.wait_for(_with_session(fn), timeout)), None
    except Exception as e:
        e = unwrap_error(e)
        return None, f"{type(e).__name__}: {e}"


async def fetch_form_async(session, name, known_id=None):
    tid = known_id
    if not tid:
        for q in dict.fromkeys([name, re.sub(r"\b(FC|GNK|FK|SC|TC|AFC|CF)\b", "", name, flags=re.I).strip()]):
            if not q:
                continue
            sres = await session.call_tool("search", {"query": q})
            sobj, err = lenient_json(tool_text(sres))
            if err:
                return None, f"search: {err}"
            tid = pick_team_id(sobj, name)
            if tid:
                break
        if not tid:
            return None, "team not found in search results"
    tres = await session.call_tool("get_team", {"id": tid, "format": "json"})
    tobj, err = lenient_json(tool_text(tres))
    if err:
        return None, f"get_team: {err}"
    games, canon = games_from_team_obj(tobj, name)
    if len(games) < MIN_MATCHES:
        return None, (f"only {len(games)} finished matches could be read from the team data (need {MIN_MATCHES}). "
                      f"Fixture sample: {first_fixture_sample(tobj)}")
    return form_from_games(games, canon or name), None


def get_forms(team_list):
    """team_list = [(name, known_id)]. Cached in session for 6h. Returns {name: (form, error)}."""
    cache = st.session_state.setdefault("form_cache", {})
    now = time.time()
    missing = [(n, i) for n, i in team_list if n not in cache or now - cache[n][2] > FORM_TTL]
    if missing:
        async def work(session):
            out = {}
            for n, i in missing:
                try:
                    out[n] = await fetch_form_async(session, n, i)
                except Exception as e:
                    out[n] = (None, f"{type(unwrap_error(e)).__name__}: {unwrap_error(e)}")
                await asyncio.sleep(0.2)
            return out
        res, err = run_mcp(work, timeout=30 + 12 * len(missing))
        for n, _ in missing:
            form, e = (res or {}).get(n, (None, err or "no result"))
            if form:
                cache[n] = (form, None, now)
            else:
                cache[n] = (None, e, 0)       # errors are not reused (timestamp 0 forces retry)
    return {n: (cache[n][0], cache[n][1]) for n, _ in team_list}

# ==========================================================
# CLIENTS
# ==========================================================
@st.cache_resource
def init_clients():
    return Groq(api_key=GROQ_API_KEY), RapidOCR()

groq_client, ocr_engine = init_clients()


def llm(prompt, temperature=0.1, max_tokens=None):
    kw = {"max_tokens": max_tokens} if max_tokens else {}
    r = groq_client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}],
                                            temperature=temperature, **kw)
    return r.choices[0].message.content

# ==========================================================
# UI
# ==========================================================
st.title("📈 AI Sharp Bettor & Auditor")
tab_daily, tab_manual, tab_auditor, tab_settings = st.tabs(
    ["🎯 Hunter & Daily Picks", "📸 Manual Bettor", "🔍 Auditor", "⚙️ Settings & Inspector"])

# ---------------------------------------------------------- TAB 1
with tab_daily:
    st.header("Match Hunter: FULL-TIME Under 1.5 goals")
    st.caption("Team form is read from LiveScore MCP. The maths decides; the AI only comments. Odds aren't available here, so confirm SportyBet's price against 'Min odds to bet'.")

    if "fixtures" not in st.session_state:
        st.session_state.fixtures = None

    if st.button("📥 Fetch today's fixtures", type="primary"):
        day = today_wat().strftime("%d/%m/%Y")

        async def get_day(session):
            return tool_text(await session.call_tool("get_day_fixtures", {"date": day}))

        with st.spinner("Fetching fixtures from LiveScore MCP..."):
            raw, err = run_mcp(get_day, timeout=30)
        if err:
            st.error(f"MCP connection failed: {err}")
        elif not raw:
            st.warning("No fixtures returned for today.")
        else:
            fixtures = []
            if obj is not None:
                for m in collect_fixtures(obj):
                    if m["finished"] or _EXCLUDE.search(f"{m.get('league','')} {m['home']} {m['away']}"):
                        continue
                    fixtures.append({"team1": m["home"], "team2": m["away"], "league": m.get("league") or "Unknown",
                                     "time": m["time"], "id1": m["home_id"], "id2": m["away_id"]})
            if not fixtures:
                st.warning(f"Could not read fixtures directly ({jerr or 'unknown layout'}). Falling back to AI parsing.")
                try:
                    prompt = f"""Extract the upcoming football matches from this text. Only the FIRST 25 matches.
Return ONLY a JSON array of objects with keys "time", "league", "team1" (home), "team2" (away).

{raw[:9000]}"""
                    arr, e2 = lenient_json(llm(prompt, max_tokens=4000))
                    fixtures = [{"team1": x.get("team1"), "team2": x.get("team2"), "league": x.get("league", "Unknown"),
                                 "time": x.get("time", ""), "id1": None, "id2": None}
                                for x in (arr or []) if isinstance(x, dict) and x.get("team1") and x.get("team2")]
                except Exception as e:
                    st.error(f"AI parse failed: {e}")
            st.session_state.fixtures = fixtures
            st.success(f"Loaded {len(fixtures)} upcoming matches.") if fixtures else st.error("No usable fixtures found. Open Settings & Inspector.")

    fixtures = st.session_state.fixtures
    if fixtures:
        labels = {}
        for fx in fixtures:
            labels.setdefault(fx["league"], []).append(fx)
        chosen = st.multiselect("Choose leagues to scan", sorted(labels), format_func=lambda k: f"{k} ({len(labels[k])})")
        pool = [fx for k in chosen for fx in labels[k]]
        c1, c2 = st.columns(2)
        max_m = c1.slider("Max matches", 1, 30, 10)
        thr = c2.slider("Candidate if Under 1.5 chance ≥ %", 20, 70, 40) / 100
        use_comment = st.checkbox("AI commentary (uses the numbers only)", value=True)

        if st.button("🚀 Run Under 1.5 Hunter") and pool:
            scan = pool[:max_m]
            teams = []
            for fx in scan:
                teams += [(fx["team1"], fx["id1"]), (fx["team2"], fx["id2"])]
            with st.spinner(f"Reading form for {len(set(n for n, _ in teams))} teams..."):
                forms = get_forms(teams)
            rows, skipped = [], []
            for fx in scan:
                (f1, e1), (f2, e2) = forms[fx["team1"]], forms[fx["team2"]]
                if not (f1 and f2):
                    skipped.append((fx, "; ".join(f"{n}: {e}" for n, e in ((fx["team1"], e1), (fx["team2"], e2)) if e)))
                    continue
                P = predict(f1, f2)
                p = 1 - total_over(P, 1.5)
                rows.append({"fx": fx, "P": P, "p": p, "f1": f1, "f2": f2,
                             "decision": "CANDIDATE - confirm odds" if p >= thr else "PASS"})
            rows.sort(key=lambda r: r["p"], reverse=True)
            if rows:
                st.dataframe(pd.DataFrame([{
                    "Match": f"{r['fx']['team1']} vs {r['fx']['team2']}", "League": r["fx"]["league"], "Time": r["fx"]["time"],
                    "xG": round(r["P"]["lam_h"] + r["P"]["lam_a"], 2), "Under 1.5 %": round(r["p"] * 100, 1),
                    "Fair odds": fair(r["p"]), "Min odds to bet": round(fair(r["p"]) * (1 + MARGIN_BUFFER), 2),
                    "Decision": r["decision"]} for r in rows]), use_container_width=True, hide_index=True)
                for r in rows:
                    with st.expander(f"⚽ {r['fx']['team1']} vs {r['fx']['team2']} - {r['p']*100:.0f}% - {r['decision']}"):
                        st.write(f"**Expected goals:** {r['P']['lam_h']:.2f} - {r['P']['lam_a']:.2f}")
                        st.info("📊 " + form_line(r["f1"]) + "\n\n📊 " + form_line(r["f2"]))
                st.caption("Full-time Under 1.5 is a low-probability, high-odds market. A typical match is well under 50%, so only the top of this list is interesting.")
                if use_comment:
                    try:
                        summ = [{"match": f"{r['fx']['team1']} vs {r['fx']['team2']}", "under15": round(r["p"], 3)} for r in rows[:8]]
                        st.write("🧠 " + llm("Using ONLY this model output, write 3 short bullets. Do not invent injuries or facts. "
                                             "Remind that no bet is guaranteed.\n" + json.dumps(summ), 0.2))
                    except Exception:
                        pass
            else:
                st.warning("No match had readable form data.")
            for fx, why in skipped:
                with st.expander(f"⛔ {fx['team1']} vs {fx['team2']} - NO DATA"):
                    st.write(why)

# ---------------------------------------------------------- TAB 2
with tab_manual:
    st.header("Manual Bet Analysis")
    st.caption("AI opinion only (no statistics). Use the Hunter for model-based numbers.")
    uploaded_file = st.file_uploader("Upload Bet Slip", type=["png", "jpg", "jpeg"])
    if uploaded_file:
        image = Image.open(uploaded_file).convert("RGB")
        st.image(image, caption="Uploaded Slip", use_container_width=True)
        if st.button("Extract & Analyze"):
            with st.spinner("Scanning image and analyzing..."):
                result, _ = ocr_engine(np.array(image))
                text = "\n".join(item[1] for item in result) if result else ""
                if not text:
                    st.warning("No text found in the image.")
                else:
                    st.info(llm("Audit this bet slip. Say which legs look weakest and why, and note that this is opinion without "
                                "verified statistics.\n\nDetails:\n" + text, 0.2))

# ---------------------------------------------------------- TAB 3
with tab_auditor:
    st.header("Performance Auditor")
    if st.button("Fetch History from Google Sheets"):
        with st.spinner("Loading data..."):
            try:
                r = requests.get(GOOGLE_SHEET_URL, timeout=20)
                try:
                    data = r.json()
                    df = pd.DataFrame(data[1:], columns=data[0])
                except Exception:
                    df = pd.read_csv(io.StringIO(r.text))
                st.dataframe(df, use_container_width=True)
                if "Result" in df.columns:
                    done = df[df["Result"].astype(str).str.lower().isin(["win", "loss"])]
                    if len(done):
                        st.metric("Win rate (settled bets)", f"{(done['Result'].astype(str).str.lower() == 'win').mean() * 100:.1f}%")
                        if "Profit" in df.columns:
                            st.metric("Profit (units)", f"{pd.to_numeric(done['Profit'], errors='coerce').fillna(0).sum():.2f}")
            except Exception as e:
                st.error(f"Failed to load Google Sheet: {e}")

# ---------------------------------------------------------- TAB 4
with tab_settings:
    st.header("Settings & Schema Inspector")
    if st.button("Test MCP connection"):
        async def list_tools(session):
            return [t.name for t in (await session.list_tools()).tools]
        names, err = run_mcp(list_tools, timeout=20)
        st.success(f"Connected. Tools: {', '.join(names)}") if names else st.error(f"Not connected: {err}")

    st.subheader("🔬 Schema Inspector")
    st.caption("If a team shows NO DATA, inspect it here and send me the output.")
    insp = st.text_input("Team name to inspect", "Arsenal")
    if st.button("Inspect") and insp:
        async def inspect(session):
            sres = tool_text(await session.call_tool("search", {"query": insp}))
            sobj, serr = lenient_json(sres)
            tid = pick_team_id(sobj, insp) if sobj is not None else None
            tres = tool_text(await session.call_tool("get_team", {"id": tid, "format": "json"})) if tid else ""
            return sres, serr, tid, tres
        out, err = run_mcp(inspect, timeout=40)
        if err:
            st.error(err)
        else:
            sres, serr, tid, tres = out
            st.markdown("**search → raw (first 600 chars)**")
            st.code(sres[:600])
            st.write(f"Parse error: {serr}" if serr else "search JSON parsed OK")
            st.write(f"Chosen team id: `{tid}`")
            if tres:
                st.markdown(f"**get_team → length {len(tres)} chars; ends with:**")
                st.code(tres[-200:])
                tobj, terr = lenient_json(tres)
                if terr:
                    st.error(terr)
                else:
                    st.markdown("**Structure**")
                    st.code("\n".join(describe(tobj)) or "(empty)")
                    st.markdown("**First fixture (raw)**")
                    st.code(first_fixture_sample(tobj))
                    games, canon = games_from_team_obj(tobj, insp)
                    st.write(f"Finished matches parsed: **{len(games)}**" + (f" (team: {canon})" if canon else ""))
                    if len(games) >= MIN_MATCHES:
                        st.code(form_line(form_from_games(games, canon or insp)))

    if st.button("Clear form cache"):
        st.session_state.pop("form_cache", None)
        st.session_state.fixtures = None
        st.rerun()
