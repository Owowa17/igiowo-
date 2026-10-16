import streamlit as st
import requests
import datetime
import math

st.set_page_config(page_title="Igiowo", page_icon="⚽", layout="centered")

SHEET_ID = "1Vx5V6paYAMivmjDu4tbt3u0x-W7z37W8R1ZqswNulys"
FC_URL = "https://footballcharts-backend.onrender.com/api/v1"
LEAGUES = ["algir1","eng1","eng2","eng3","esp1","esp2","ita1","ita2","ger1","ger2","fra1","fra2","por1","ned1","bel1","tur1","sco1","arg1","japan1","greece1","brazil1","brazil2","egypt1","morocco1","saudi1","saudi2"]

st.title("⚽ Igiowo")
st.caption("Multi-Market Prediction Engine · 26 leagues · 3 markets")


def fetch_all():
    H = {"Authorization": "Bearer " + st.secrets["FC_API_KEY"]}
    all_m = []
    for lg in LEAGUES:
        try:
            r = requests.get(FC_URL + "/leagues/" + lg + "/fixtures/", headers=H, timeout=30).json().get("matches", []) or []
            all_m.extend(r)
        except Exception:
            pass
    return all_m


def rate_u15(p):
    return (1 if (1-p["raw_over"]) >= 0.78 else 0) + (1 if (1-p["cal_over"]) >= 0.78 else 0) + (1 if abs(p["raw_over"]-p["cal_over"]) <= 0.05 else 0) + (1 if (p.get("exp_ht") or 99) <= 1.30 else 0) + (1 if 0.30 <= (p.get("ht05") or 0) <= 0.70 else 0)


def rate_u25(p):
    return (1 if p["raw_u25"] >= 0.75 else 0) + (1 if p["cal_u25"] >= 0.75 else 0) + (1 if abs(p["raw_u25"]-p["cal_u25"]) <= 0.05 else 0) + (1 if (p.get("exp_home") or 9) + (p.get("exp_away") or 9) <= 2.30 else 0) + (1 if (p.get("over15") or 99) <= 0.70 else 0)


poisson_pmf = lambda k, lam: 0 if lam <= 0 else (lam ** k) * math.exp(-lam) / math.factorial(k)
score_prob = lambda h, a, lh, la: poisson_pmf(h, lh) * poisson_pmf(a, la)


if st.button("🔄 Fetch Fresh Picks", type="primary", use_container_width=True):
    with st.spinner("Fetching 26 leagues from Football-Charts..."):
        st.session_state["matches"] = fetch_all()
    st.success("Fetched " + str(len(st.session_state["matches"])) + " fixtures")


if "matches" not in st.session_state:
    st.info("Tap 'Fetch Fresh Picks' to load today's predictions.")
    st.caption("Data source: Football-Charts API · Updated live")
    st.stop()


matches = st.session_state["matches"]
today = datetime.datetime.now().strftime("%Y-%m-%d")
tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

u15_picks = []
u25_picks = []
cs_picks = []

for m in matches:
    mp = (m.get("model_predictions") or {}).get("dc_v2", {}).get("raw", {})
    cal = (m.get("model_predictions") or {}).get("dc_v2", {}).get("calibrated", {})
    date = m.get("match_date", "")
    if date not in [today, tomorrow]:
        continue

    # HT Under 1.5
    if mp.get("ht_over_1.5") is not None and cal.get("ht_over_1.5") is not None:
        if (1 - mp["ht_over_1.5"]) >= 0.78 and (1 - cal["ht_over_1.5"]) >= 0.78:
            p = {"home": m.get("home_team",""), "away": m.get("away_team",""), "league": m.get("real_league_name",""), "country": m.get("country",""), "date": date, "time": m.get("time","00:00"), "raw_over": mp["ht_over_1.5"], "cal_over": cal["ht_over_1.5"], "exp_ht": mp.get("expected_ht_goals"), "ht05": mp.get("ht_over_0.5")}
            p["stars"] = rate_u15(p)
            if p["stars"] >= 4: u15_picks.append(p)

    # FT Under 2.5
    if mp.get("over_2.5") is not None and cal.get("over_2.5") is not None:
        if (1 - mp["over_2.5"]) >= 0.72 and (1 - cal["over_2.5"]) >= 0.72:
            p = {"home": m.get("home_team",""), "away": m.get("away_team",""), "league": m.get("real_league_name",""), "country": m.get("country",""), "date": date, "time": m.get("time","00:00"), "raw_u25": 1 - mp["over_2.5"], "cal_u25": 1 - cal["over_2.5"], "exp_home": mp.get("expected_home_goals"), "exp_away": mp.get("expected_away_goals"), "over15": mp.get("over_1.5")}
            p["stars"] = rate_u25(p)
            if p["stars"] >= 4: u25_picks.append(p)

    # Correct Score (low-scoring matches only)
    eh, ea = mp.get("expected_home_goals"), mp.get("expected_away_goals")
    if eh is not None and ea is not None and (eh + ea) <= 2.60:
        probs = {"0-0": score_prob(0,0,eh,ea), "1-0": score_prob(1,0,eh,ea), "0-1": score_prob(0,1,eh,ea), "1-1": score_prob(1,1,eh,ea), "2-0": score_prob(2,0,eh,ea), "0-2": score_prob(0,2,eh,ea)}
        best_score = max(probs, key=probs.get)
        best_prob = probs[best_score]
        if best_prob >= 0.10:
            cs_picks.append({"home": m.get("home_team",""), "away": m.get("away_team",""), "league": m.get("real_league_name",""), "country": m.get("country",""), "date": date, "time": m.get("time","00:00"), "score": best_score, "prob": best_prob, "fair": round(1/best_prob, 2), "xg": round(eh+ea, 2)})

u15_picks.sort(key=lambda x: x["stars"], reverse=True)
u25_picks.sort(key=lambda x: x["stars"], reverse=True)
cs_picks.sort(key=lambda x: x["prob"], reverse=True)


st.divider()
st.subheader("📊 Today's Summary")
c1, c2, c3 = st.columns(3)
c1.metric("HT U1.5", len(u15_picks))
c2.metric("FT U2.5", len(u25_picks))
c3.metric("Correct Score", len(cs_picks))


st.divider()
st.subheader("🎯 HT Under 1.5 — Top Picks")
if u15_picks:
    for p in u15_picks[:5]:
        prob = round((1 - p["raw_over"]) * 100, 1)
        st.write("*" * p["stars"] + " **" + p["home"] + " vs " + p["away"] + "** · " + p["league"] + " · " + str(prob) + "% · _HT U1.5_")
else:
    st.write("_No 4+ star HT U1.5 picks today._")


st.divider()
st.subheader("📉 FT Under 2.5 — Top Picks")
if u25_picks:
    for p in u25_picks[:5]:
        prob = round(p["raw_u25"] * 100, 1)
        st.write("*" * p["stars"] + " **" + p["home"] + " vs " + p["away"] + "** · " + p["league"] + " · " + str(prob) + "% · _FT U2.5_")
else:
    st.write("_No 4+ star FT U2.5 picks today._")


st.divider()
st.subheader("⚽ Correct Score — Top Picks")
if cs_picks:
    for p in cs_picks[:5]:
        st.write("**" + p["home"] + " vs " + p["away"] + "** · " + p["league"] + " · Score: **" + p["score"] + "** · Prob: " + str(round(p["prob"]*100,1)) + "% · Fair odds: " + str(p["fair"]) + " · xG: " + str(p["xg"]))
else:
    st.write("_No low-scoring matches today._")


st.divider()
st.caption("📋 Full track record: owowa17.github.io/igiowo-/")
st.caption("Probabilities are for informational purposes. No guarantee of profit. 18+. Gamble responsibly.")
