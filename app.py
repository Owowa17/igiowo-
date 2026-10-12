import streamlit as st
import requests
import datetime

SHEET_ID = "1Vx5V6paYAMivmjDu4tbt3u0x-W7z37W8R1ZqswNulys"
TAB = "Daily prediction"
FC_URL = "https://footballcharts-backend.onrender.com/api/v1"
LEAGUES = ["algir1","eng1","eng2","eng3","esp1","esp2","ita1","ita2","ger1","ger2","fra1","fra2","por1","ned1","bel1","tur1","sco1","arg1","japan1","greece1"]


def fetch_predictions():
    H = {"Authorization": "Bearer " + st.secrets["FC_API_KEY"]}
    all_m = []
    for lg in LEAGUES:
        try:
            r = requests.get(
                FC_URL + "/leagues/" + lg + "/fixtures/",
                headers=H,
                timeout=30
            ).json().get("matches", []) or []
            all_m.extend(r)
        except Exception:
            pass
    return all_m


def rate_u15(p):
    score = 0
    if (1 - p["raw_over"]) >= 0.78: score += 1
    if (1 - p["cal_over"]) >= 0.78: score += 1
    if abs(p["raw_over"] - p["cal_over"]) <= 0.05: score += 1
    if (p.get("exp_ht") or 99) <= 1.30: score += 1
    if 0.30 <= (p.get("ht05") or 0) <= 0.70: score += 1
    return score


def rate_u25(p):
    score = 0
    if p["raw_u25"] >= 0.75: score += 1
    if p["cal_u25"] >= 0.75: score += 1
    if abs(p["raw_u25"] - p["cal_u25"]) <= 0.05: score += 1
    if (p.get("exp_home") or 9) + (p.get("exp_away") or 9) <= 2.30: score += 1
    if (p.get("over15") or 99) <= 0.70: score += 1
    return score


def display_dashboard(matches):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    u15_picks = []
    u25_picks = []

    for m in matches:
        mp = (m.get("model_predictions") or {}).get("dc_v2", {}).get("raw", {})
        cal = (m.get("model_predictions") or {}).get("dc_v2", {}).get("calibrated", {})
        date = m.get("match_date", "")
        if date not in [today, tomorrow]:
            continue

        if mp.get("ht_over_1.5") is not None and cal.get("ht_over_1.5") is not None:
            if (1 - mp["ht_over_1.5"]) >= 0.78 and (1 - cal["ht_over_1.5"]) >= 0.78:
                p = {
                    "home": m.get("home_team", ""),
                    "away": m.get("away_team", ""),
                    "league": m.get("real_league_name", ""),
                    "raw_over": mp["ht_over_1.5"],
                    "cal_over": cal["ht_over_1.5"],
                    "exp_ht": mp.get("expected_ht_goals"),
                    "ht05": mp.get("ht_over_0.5")
                }
                p["stars"] = rate_u15(p)
                if p["stars"] >= 4:
                    u15_picks.append(p)

        if mp.get("over_2.5") is not None and cal.get("over_2.5") is not None:
            if (1 - mp["over_2.5"]) >= 0.72 and (1 - cal["over_2.5"]) >= 0.72:
                p = {
                    "home": m.get("home_team", ""),
                    "away": m.get("away_team", ""),
                    "league": m.get("real_league_name", ""),
                    "raw_u25": 1 - mp["over_2.5"],
                    "cal_u25": 1 - cal["over_2.5"],
                    "exp_home": mp.get("expected_home_goals"),
                    "exp_away": mp.get("expected_away_goals"),
                    "over15": mp.get("over_1.5")
                }
                p["stars"] = rate_u25(p)
                if p["stars"] >= 4:
                    u25_picks.append(p)

    u15_picks.sort(key=lambda x: x["stars"], reverse=True)
    u25_picks.sort(key=lambda x: x["stars"], reverse=True)

    st.subheader("🎯 HT Under 1.5 — Top Picks")
    if u15_picks:
        for p in u15_picks[:5]:
            prob = round((1 - p["raw_over"]) * 100, 1)
            st.write("*" * p["stars"] + " | " + p["home"] + " vs " + p["away"] + " | " + p["league"] + " | " + str(prob) + "%")
    else:
        st.write("No 4+ star HT Under 1.5 picks today.")

    st.subheader("📉 FT Under 2.5 — Top Picks")
    if u25_picks:
        for p in u25_picks[:5]:
            prob = round(p["raw_u25"] * 100, 1)
            st.write("*" * p["stars"] + " | " + p["home"] + " vs " + p["away"] + " | " + p["league"] + " | " + str(prob) + "%")
    else:
        st.write("No 4+ star FT Under 2.5 picks today.")


st.set_page_config(page_title="Igiowo", page_icon="⚽")
st.title("⚽ Igiowo")
st.caption("Multi-Market Prediction Engine")

if st.button("🔄 Fetch Fresh Picks", type="primary"):
    with st.spinner("Fetching 20 leagues from Football-Charts..."):
        st.session_state["matches"] = fetch_predictions()
    st.success("Fetched " + str(len(st.session_state["matches"])) + " fixtures")

if "matches" not in st.session_state:
    st.info("Click 'Fetch Fresh Picks' to load today's predictions.")
else:
    display_dashboard(st.session_state["matches"])
