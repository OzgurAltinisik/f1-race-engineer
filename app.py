import time
import random as _rng
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from simulation import create_race, Race, Driver, DRIVER_NAMES, DRIVERS_DATA
from track import (track_figure, telemetry_figure, get_abbrev,
                   compute_visual_positions, compute_live_gaps,
                   get_telemetry, lap_time_at_speed_profile)

st.set_page_config(page_title="F1 Race Engineer", page_icon="🏎",
                   layout="wide", initial_sidebar_state="collapsed")

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@keyframes f1pulse {
    0%,100% { text-shadow:0 0 40px rgba(225,6,0,0.55); }
    50%      { text-shadow:0 0 100px rgba(225,6,0,1),0 0 180px rgba(255,80,0,0.5); }
}
@keyframes cardGlow {
    0%,100% { box-shadow:0 0 6px rgba(225,6,0,0.25); }
    50%      { box-shadow:0 0 22px rgba(225,6,0,0.7),0 0 40px rgba(225,6,0,0.2); }
}
@keyframes slideIn {
    from { opacity:0; transform:translateY(12px); }
    to   { opacity:1; transform:translateY(0); }
}
@keyframes subtleScan {
    0%   { background-position:0 0; }
    100% { background-position:0 80px; }
}
body, .stApp { background-color:#0d0d0d; color:#f0f0f0; }
h1,h2,h3 { color:#e10600; font-family:'Arial Black',sans-serif; letter-spacing:2px; }
.stButton>button {
    background:linear-gradient(135deg,#c00000,#e10600);
    color:white; border:none; font-weight:bold; border-radius:6px;
    padding:8px 20px; transition:all 0.2s;
    box-shadow:0 2px 10px rgba(225,6,0,0.35);
}
.stButton>button:hover {
    background:linear-gradient(135deg,#e10600,#ff3300);
    box-shadow:0 4px 20px rgba(225,6,0,0.65);
    transform:translateY(-1px);
}
.stTabs [data-baseweb="tab"] { color:#888; font-weight:bold; }
.stTabs [aria-selected="true"] { color:#e10600 !important; border-bottom:2px solid #e10600; }
.stDataFrame { background:#1a1a1a; }
div[data-testid="stMetricValue"] { font-size:1.5rem; font-weight:bold; }
.driver-card {
    border-radius:12px; padding:18px 22px; margin-top:12px;
    animation:slideIn 0.35s ease;
}
.strat-card-sel { animation:cardGlow 2.2s ease-in-out infinite; }
.alert-drs  { background:#002b6e; border-left:4px solid #0080ff; padding:8px 12px; border-radius:4px; margin:3px 0; }
.alert-hunt { background:#2b2200; border-left:4px solid #ffcc00; padding:8px 12px; border-radius:4px; margin:3px 0; }
.alert-def  { background:#2b0000; border-left:4px solid #ff2200; padding:8px 12px; border-radius:4px; margin:3px 0; }
.alert-tire { background:#1a1a00; border-left:4px solid #ffaa00; padding:8px 12px; border-radius:4px; margin:3px 0; }
.alert-sc   { background:#1a1500; border-left:4px solid #ffff00; padding:8px 12px; border-radius:4px; margin:3px 0; }
.alert-ok   { background:#001a00; border-left:4px solid #00cc44; padding:8px 12px; border-radius:4px; margin:3px 0; }
.alert-fl   { background:#1a0033; border-left:4px solid #cc44ff; padding:8px 12px; border-radius:4px; margin:3px 0; }
.radio-msg  { background:#0a1020; border-left:3px solid #0088cc; padding:5px 10px;
              border-radius:4px; margin:2px 0; font-size:0.80rem; font-family:monospace; color:#88ccff; }
</style>
""", unsafe_allow_html=True)

# ── Helpers ─────────────────────────────────────────────────────────────────────
def _fmt(s: float) -> str:
    m = int(s // 60); return f"{m}:{s % 60:06.3f}"

TACTIC_PUSH = {"Defend": 0, "Normal": 1, "Push": 2}

_RADIO_GENERIC = [
    "Gap under control. Focus focus.",
    "Pace looking good, stick to the strategy.",
    "Car sounding great, push push.",
    "Copy that. Keep it up, rhythm is good.",
    "Cool-down laps normal. Carry on.",
]

def _gen_radio(race, player, prev_pos, fl_name):
    lap = race.current_lap
    p_lap = player.last_lap
    cur_pos = player.position
    msgs = []
    if p_lap and p_lap.is_pit_lap:
        msgs.append("Box done! Clean exit — get into rhythm!")
    if fl_name == player.name and player.best_lap:
        msgs.append(f"FASTEST LAP! {_fmt(player.best_lap)} — brilliant lap!")
    if p_lap and p_lap.safety_car:
        msgs.append("Green green green — SC over, push now!")
    if player.tire.wear_pct >= 80:
        nxt = [l for l in player.pit_laps if l > lap]
        if nxt:
            msgs.append(f"Tires critical {player.tire.wear_pct}%. Box box box — Lap {nxt[0]}!")
        else:
            msgs.append(f"Tires critical {player.tire.wear_pct}%. Push to the finish!")
    elif player.tire.wear_pct >= 60 and not msgs:
        msgs.append(f"Tires {player.tire.wear_pct}%, degradation incoming. Watch out.")
    if prev_pos is not None and cur_pos != prev_pos and not msgs:
        msgs.append(f"Great overtake! P{cur_pos}, keep it up!" if cur_pos < prev_pos
                    else f"Dropped to P{cur_pos}. Defend, close the gap!")
    if not msgs:
        msgs.append(_RADIO_GENERIC[lap % len(_RADIO_GENERIC)])
    return {"lap": lap, "msg": msgs[0]}

# ── Session state ───────────────────────────────────────────────────────────────
for k, v in {
    "race_phase": "setup", "race": None, "lights_count": 0,
    "visual_time": 0.0, "lap_expected": 91.0,
    "live_paused": False, "speed": "2x", "tactic": "Normal",
    "selected_strategy": 1, "radio_log": [], "prev_player_pos": None, "prev_fl_name": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════════════════
# SETUP
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.race_phase == "setup":

    _drv_lookup = {name: (team, color, pace) for name, team, color, pace in DRIVERS_DATA}

    st.markdown("""
    <div style="background:linear-gradient(160deg,#0a0a0a 0%,#1a0000 50%,#0a0a0a 100%);
                border:1px solid #e10600;border-radius:16px;padding:48px 40px 36px;
                margin-bottom:28px;position:relative;overflow:hidden;">
        <div style="position:absolute;top:0;left:0;right:0;bottom:0;
            background:repeating-linear-gradient(45deg,transparent,transparent 18px,
            rgba(225,6,0,0.05) 18px,rgba(225,6,0,0.05) 36px);pointer-events:none;"></div>
        <div style="position:relative;text-align:center;">
            <div style="font-size:5.5rem;font-family:'Arial Black',sans-serif;font-weight:900;
                color:#e10600;letter-spacing:10px;line-height:1;
                animation:f1pulse 2.8s ease-in-out infinite;">F1</div>
            <div style="font-size:1.9rem;color:white;letter-spacing:14px;
                font-family:'Arial Black',sans-serif;margin-top:6px;">RACE ENGINEER</div>
            <div style="color:#555;letter-spacing:5px;margin-top:6px;font-size:0.85rem;
                font-family:monospace;">2025 SEASON SIMULATION</div>
            <div style="width:140px;height:2px;
                background:linear-gradient(90deg,transparent,#e10600,transparent);
                margin:20px auto 0;"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    cfg_col, drv_col = st.columns([2, 1])
    with cfg_col:
        st.markdown("""
        <div style="background:#111;border:1px solid #2a2a2a;border-radius:12px;
                    padding:28px 32px;margin-bottom:16px;">
        <div style="color:#e10600;font-family:'Arial Black';font-size:1.1rem;
                    letter-spacing:3px;margin-bottom:20px;">⚙  RACE SETUP</div>
        """, unsafe_allow_html=True)

        mode = st.radio("Mode", ["🎲 Random", "⚙️ Manual"], horizontal=True, label_visibility="collapsed")

        c1, c2 = st.columns(2)
        with c1:
            if "Manual" in mode:
                chosen_driver = st.selectbox("Driver", DRIVER_NAMES)
                chosen_grid   = st.slider("Grid Position", 1, 20, 10,
                                          help="1 = Pole, 20 = Last")
            else:
                chosen_driver, chosen_grid = None, None
                st.markdown('<div style="color:#555;padding:12px 0">Random driver and grid will be assigned.</div>',
                            unsafe_allow_html=True)
        with c2:
            total_laps = st.slider("Total Laps", 10, 70, 57)

        st.markdown("</div>", unsafe_allow_html=True)

    with drv_col:
        if "Manual" in mode and chosen_driver:
            _team, _clr, _pace = _drv_lookup[chosen_driver]
            _abbr = get_abbrev(chosen_driver)
            _grid_bar = "".join([
                f'<div style="width:12px;height:12px;border-radius:2px;margin:1px;'
                f'background:{"#e10600" if i+1==chosen_grid else "#2a2a2a"};'
                f'opacity:{1.0 if i+1==chosen_grid else 0.4};"></div>'
                for i in range(20)
            ])
            st.markdown(f"""
            <div class="driver-card"
                 style="background:linear-gradient(135deg,#0d0d0d,{_clr}22);
                        border:1.5px solid {_clr}88;">
                <div style="font-size:2.6rem;font-weight:900;font-family:'Arial Black';
                    color:{_clr};letter-spacing:3px;line-height:1;">{_abbr}</div>
                <div style="font-size:0.95rem;color:white;font-weight:bold;
                    margin:6px 0 2px;">{chosen_driver}</div>
                <div style="font-size:0.78rem;color:{_clr};letter-spacing:2px;
                    margin-bottom:14px;">{_team.upper()}</div>
                <div style="font-size:0.72rem;color:#888;margin-bottom:8px;">GRID POSITION</div>
                <div style="display:flex;flex-wrap:wrap;gap:2px;margin-bottom:12px;">
                    {_grid_bar}
                </div>
                <div style="display:flex;justify-content:space-between;
                    font-size:0.75rem;color:#aaa;">
                    <span>P<b style="color:white;font-size:1rem;">{chosen_grid}</b></span>
                    <span style="color:{_clr};">● {total_laps} LAPS</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;
                padding:28px;text-align:center;margin-top:4px;">
                <div style="font-size:2.5rem;opacity:0.15;">🏎</div>
                <div style="color:#333;font-size:0.8rem;margin-top:8px;letter-spacing:2px;">
                    SELECT DRIVER
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🏁  START RACE", type="primary", use_container_width=True):
            st.session_state.race        = create_race(total_laps, chosen_driver, chosen_grid)
            st.session_state.race_phase  = "strategy"
            st.session_state.selected_strategy = 1
            st.rerun()

    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY SELECTION
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.race_phase == "strategy":
    race: Race = st.session_state.race
    grid_pos   = race.player.grid_pos
    total_laps = race.total_laps

    pit_early  = int(total_laps * 0.32)
    pit_mid    = int(total_laps * 0.44)
    pit_late   = int(total_laps * 0.56)

    front = grid_pos <= 6
    back  = grid_pos >= 15

    STRATEGIES = [
        {
            "id": 0,
            "name": "⚔️  AGGRESSIVE",
            "tire": "Soft",
            "pit":  pit_early,
            "route": "Soft → Hard",
            "pros": "Full pace early, undercut opportunity" if front else "Fast warm-up, aggressive overtaking",
            "cons": f"Early pit (Lap {pit_early}), high tire wear",
            "color": "#ff4400",
        },
        {
            "id": 1,
            "name": "⚖️  BALANCED",
            "tire": "Medium",
            "pit":  pit_mid,
            "route": "Medium → Hard",
            "pros": "Wide pit window, consistent pace" if not back else "Different window to rivals",
            "cons": f"Medium pace advantage, pit Lap {pit_mid}",
            "color": "#ffcc00",
        },
        {
            "id": 2,
            "name": "🛡️  CONSERVATIVE",
            "tire": "Hard",
            "pit":  pit_late,
            "route": "Hard → Medium",
            "pros": "Long first stint, late pit → track position" if front else "Gain when rivals pit",
            "cons": f"Slow start, tires take time to warm up",
            "color": "#4488ff",
        },
    ]

    st.markdown("""
    <div style="text-align:center;padding:32px 0 16px;">
        <div style="font-size:2.2rem;font-family:'Arial Black';color:#ffcc00;
            letter-spacing:5px;text-shadow:0 0 20px rgba(255,204,0,0.4);">
            STRATEGY SELECT
        </div>
        <div style="color:#666;letter-spacing:3px;margin-top:6px;font-size:0.9rem;">
            3 strategies recommended based on your grid position
        </div>
    </div>
    """, unsafe_allow_html=True)

    tire_sym = {"Soft": "🔴 S", "Medium": "🟡 M", "Hard": "⚪ H"}
    sel = st.session_state.selected_strategy
    cols = st.columns(3)

    _TIRE_COLORS = {"Soft": "#e8002d", "Medium": "#ffd600", "Hard": "#ffffff"}
    _TIRE2_MAP   = {"Soft": "Hard", "Medium": "Hard", "Hard": "Medium"}

    for i, strat in enumerate(STRATEGIES):
        with cols[i]:
            chosen   = (sel == strat["id"])
            border   = strat["color"] if chosen else "#2a2a2a"
            bg       = f"background:{strat['color']}14;" if chosen else "background:#111;"
            glow_cls = "strat-card-sel" if chosen else ""
            badge    = (f'<div style="background:{strat["color"]};color:#000;font-size:0.68rem;'
                        f'font-weight:900;padding:3px 10px;border-radius:3px;display:inline-block;'
                        f'letter-spacing:2px;margin-bottom:12px;">{"✓ SELECTED" if chosen else "SELECT"}</div>')

            # Pit timeline bar
            t1 = strat["tire"]
            t2 = _TIRE2_MAP[t1]
            c1_clr = _TIRE_COLORS[t1]
            c2_clr = _TIRE_COLORS[t2]
            pit_frac = strat["pit"] / total_laps
            stint1_w = int(pit_frac * 100)
            stint2_w = 100 - stint1_w
            timeline = f"""
            <div style="margin:14px 0 6px;">
                <div style="font-size:0.65rem;color:#666;letter-spacing:2px;margin-bottom:5px;">PIT STRATEGY — LAP {strat['pit']}/{total_laps}</div>
                <div style="display:flex;height:10px;border-radius:4px;overflow:hidden;gap:2px;">
                    <div style="width:{stint1_w}%;background:{c1_clr};opacity:0.85;border-radius:3px 0 0 3px;"></div>
                    <div style="width:3px;background:#fff;opacity:0.9;flex-shrink:0;"></div>
                    <div style="width:{stint2_w}%;background:{c2_clr};opacity:0.75;border-radius:0 3px 3px 0;"></div>
                </div>
                <div style="display:flex;justify-content:space-between;font-size:0.62rem;color:#666;margin-top:3px;">
                    <span style="color:{c1_clr};">{t1} ×{strat['pit']}</span>
                    <span style="color:{c2_clr};">{t2} ×{total_laps - strat['pit']}</span>
                </div>
            </div>"""

            st.markdown(f"""
            <div class="{glow_cls}" style="border:2px solid {border};border-radius:12px;
                        padding:20px;min-height:280px;{bg}margin-bottom:8px;
                        transition:border-color 0.3s;">
                <div style="font-size:1.2rem;font-family:'Arial Black';
                            color:{strat['color']};margin-bottom:8px;">{strat['name']}</div>
                {badge}
                <div style="font-size:1.4rem;margin:8px 0 2px;">{tire_sym[strat['tire']]}</div>
                <div style="color:#ccc;font-size:0.9rem;margin-bottom:8px;">{strat['route']}</div>
                {timeline}
                <div style="font-size:0.78rem;color:#aaa;margin-bottom:4px;">✅ {strat['pros']}</div>
                <div style="font-size:0.78rem;color:#666;">⚠️ {strat['cons']}</div>
            </div>
            """, unsafe_allow_html=True)
            if st.button(strat["name"].split()[-1], key=f"strat_{i}",
                         use_container_width=True,
                         type="primary" if chosen else "secondary"):
                st.session_state.selected_strategy = strat["id"]
                st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"**Grid:** P{grid_pos} &nbsp;|&nbsp; **Laps:** {total_laps} &nbsp;|&nbsp; "
                f"**Selected:** {STRATEGIES[sel]['name']} — {STRATEGIES[sel]['route']}",
                unsafe_allow_html=True)

    if st.button("🏁  GO RACING", type="primary", use_container_width=True, key="btn_start_race"):
        chosen_strat = STRATEGIES[sel]
        from simulation import Tire
        race.player.tire     = Tire(chosen_strat["tire"])
        race.player.pit_laps = [chosen_strat["pit"]]

        push = TACTIC_PUSH[st.session_state.tactic]
        st.session_state.race_phase   = "formation"
        st.session_state.lights_count = 0
        st.session_state.visual_time  = 0.0
        st.session_state.lap_expected = lap_time_at_speed_profile(push)
        st.rerun()

    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# FORMATION LAP
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.race_phase == "formation":
    race: Race = st.session_state.race

    st.markdown("""
    <div style="text-align:center;padding:40px 0 20px;">
        <div style="font-size:3.5rem;font-family:'Arial Black';color:#ffcc00;
            letter-spacing:6px;text-shadow:0 0 30px rgba(255,204,0,0.5);">
            FORMATION LAP
        </div>
        <div style="color:#666;letter-spacing:3px;margin-top:8px;font-size:0.95rem;">
            Cars moving to grid positions...
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Cars at grid positions
    grid_pos = {d.name: max(0.0, 1.0 - (d.grid_pos - 1) * 0.006) for d in race.drivers}
    st.plotly_chart(track_figure(race, grid_pos), use_container_width=True)

    time.sleep(2.5)
    st.session_state.race_phase = "lights"
    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# LIGHTS OUT
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.race_phase == "lights":
    race: Race = st.session_state.race
    lc = st.session_state.lights_count

    lights_html = '<div style="text-align:center;padding:60px 0 40px;">'
    lights_html += '<div style="font-size:1.1rem;color:#888;letter-spacing:4px;margin-bottom:40px;font-family:monospace;">SILVERSTONE GRAND PRIX</div>'
    lights_html += '<div style="display:inline-flex;gap:28px;align-items:center;">'
    for i in range(5):
        if i < lc:
            lights_html += ('<div style="width:72px;height:72px;border-radius:50%;'
                            'background:#ff0000;'
                            'box-shadow:0 0 24px #ff0000,0 0 48px rgba(255,0,0,0.6),'
                            '0 0 80px rgba(255,0,0,0.3);"></div>')
        else:
            lights_html += ('<div style="width:72px;height:72px;border-radius:50%;'
                            'background:#1a0000;border:2px solid #440000;"></div>')
    lights_html += '</div></div>'
    st.markdown(lights_html, unsafe_allow_html=True)

    if lc < 5:
        time.sleep(0.85)
        st.session_state.lights_count += 1
        st.rerun()
    else:
        # All 5 on → wait → LIGHTS OUT
        time.sleep(1.4)
        push = TACTIC_PUSH[st.session_state.tactic]
        st.session_state.race_phase  = "racing"
        st.session_state.live_paused = False
        st.session_state.visual_time = 0.0
        st.session_state.lap_expected = lap_time_at_speed_profile(push)
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# RACING
# ══════════════════════════════════════════════════════════════════════════════
race:   Race   = st.session_state.race
player: Driver = race.player

tactic     = st.session_state.tactic
push_level = TACTIC_PUSH[tactic]
speed      = st.session_state.speed
paused     = st.session_state.live_paused

SIM_DT = {"1x": 0.25, "2x": 0.5, "5x": 1.25, "10x": 2.5}

# ── Advance visual clock ───────────────────────────────────────────────────────
if not paused and not race.finished:
    st.session_state.visual_time += SIM_DT[speed]
    if st.session_state.visual_time >= st.session_state.lap_expected:
        _prev_pos_radio = st.session_state.prev_player_pos
        st.session_state.visual_time -= st.session_state.lap_expected
        race.simulate_lap(push_level=push_level, tactic=tactic)
        if player.best_lap:
            base_lt = player.best_lap
            push_adj = {0: 0.35, 1: 0.0, 2: -0.40}[push_level]
            st.session_state.lap_expected = max(85.0, base_lt + push_adj)
        else:
            st.session_state.lap_expected = lap_time_at_speed_profile(push_level)
        # Fastest lap at this moment (for radio + FL change detection)
        _fl_radio, _flt_radio = None, None
        for _rd in race.drivers:
            if _rd.best_lap and (_flt_radio is None or _rd.best_lap < _flt_radio):
                _flt_radio, _fl_radio = _rd.best_lap, _rd.name
        _rmsg = _gen_radio(race, player, _prev_pos_radio, _fl_radio)
        st.session_state.radio_log.append(_rmsg)
        # FL change notification
        _prev_fl = st.session_state.prev_fl_name
        if _fl_radio and _fl_radio != _prev_fl and _prev_fl is not None:
            _fl_change_msg = {"lap": race.current_lap, "msg": f"💜 FASTEST LAP CHANGES — {_fl_radio} ({_fmt(_flt_radio)})"}
            st.session_state.radio_log.append(_fl_change_msg)
        st.session_state.prev_fl_name = _fl_radio
        st.session_state.radio_log = st.session_state.radio_log[-8:]

# ── Compute visual positions & live gaps ───────────────────────────────────────
_vis  = compute_visual_positions(race, st.session_state.visual_time, st.session_state.lap_expected)
_gaps = compute_live_gaps(race, _vis, st.session_state.lap_expected)

# ── Pit lane: drivers whose LAST lap was a pit stop (fixed service position) ──
_pit_drivers: dict = {}
for _d in race.drivers:
    if _d.last_lap and _d.last_lap.is_pit_lap:
        _pit_drivers[_d.name] = 0.5   # stationary at service zone

# ── Live standings: sorted by gap-to-leader every tick (instant overtakes) ────
if race.current_lap == 0:
    # Lap 0: show grid positions for sync between track map and timing tower
    _live_order = sorted(
        [d for d in race.drivers if d.name not in _pit_drivers],
        key=lambda d: d.grid_pos,
    )
else:
    _live_order = sorted(
        [d for d in race.drivers if d.name not in _pit_drivers],
        key=lambda d: _gaps.get(d.name, 9999.0),
    )
_live_order += [d for d in race.drivers if d.name in _pit_drivers]
_live_pos = {d.name: i + 1 for i, d in enumerate(_live_order)}
st.session_state.prev_player_pos = _live_pos.get(player.name)

# ── Fastest lap driver ────────────────────────────────────────────────────────
_fl_driver, _fl_time = None, None
for _d in race.drivers:
    if _d.best_lap and (_fl_time is None or _d.best_lap < _fl_time):
        _fl_time, _fl_driver = _d.best_lap, _d

_player_prog = _vis.get(player.name, 0.0)
_has_drs = (player.last_lap is not None and
            player.last_lap.gap_ahead is not None and
            player.last_lap.gap_ahead <= 1.0)
_telem = get_telemetry(_player_prog, push_level, _has_drs)

# ── HEADER ─────────────────────────────────────────────────────────────────────
st.markdown("# 🏎  F1 RACE ENGINEER")

sc_active  = race.current_lap in race.safety_car_laps
flag_text  = "🟡 SAFETY CAR" if sc_active else ("🏁 FINISHED" if race.finished else "🟢 GREEN FLAG")
cur_pos    = _live_pos.get(player.name, player.grid_pos) if race.current_lap > 0 else player.grid_pos
player_gap = _gaps.get(player.name, 0.0)
p_behind   = None
ordered_now = _live_order
for i, d in enumerate(ordered_now):
    if d.is_player and i < len(ordered_now) - 1:
        p_behind = _gaps.get(ordered_now[i + 1].name, None)
        if p_behind is not None:
            p_behind = round(p_behind - player_gap, 3) if p_behind > player_gap else None
        break

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("LAP",       f"{race.current_lap} / {race.total_laps}")
c2.metric("POSITION",  f"P{cur_pos}")
c3.metric("⏱ LAP",    _fmt(st.session_state.visual_time))
c4.metric("GAP AHEAD", ("LEADER" if player_gap == 0.0 else
                        ("—" if race.current_lap == 0 else f"+{player_gap:.3f}s")))
c5.metric("GAP BEHIND","—" if p_behind is None else f"+{p_behind:.3f}s")
c6.metric("STATUS",    flag_text)

st.markdown("---")

# ── TACTIC CONTROLS ─────────────────────────────────────────────────────────────
if not race.finished:
    ctrl_l, ctrl_r = st.columns([2, 3])

    with ctrl_l:
        st.markdown("**🎯 Driving Tactic**")
        gap_ahead  = _gaps.get(player.name, 0.0)
        suggest = ""
        if 0 < gap_ahead <= 2.0:
            suggest = "💡 DRS range — **Push** recommended!"
        elif p_behind is not None and p_behind <= 1.5:
            suggest = "💡 Under pressure — **Defend** recommended!"

        tb1, tb2, tb3 = st.columns(3)
        for col, label, key in [(tb1,"🛡️ Defend","tactic_s"),
                                  (tb2,"⚪ Normal","tactic_n"),
                                  (tb3,"⚔️ Push","tactic_a")]:
            tac_name = label.split()[-1]
            if col.button(label, key=key, use_container_width=True,
                          type="primary" if tactic == tac_name else "secondary"):
                st.session_state.tactic = tac_name; st.rerun()

        TINFO = {
            "Defend": ("#4488ff", "🛡️ DEFEND",  "+0.08s/lap | Low wear | Errors ↓"),
            "Normal": ("#aaaaaa", "⚪ NORMAL",  "Standard pace"),
            "Push":   ("#ff8800", "⚔️ PUSH",    "−0.15s/lap | Wear ↑ | Errors ↑"),
        }
        c, lbl, desc = TINFO[tactic]
        st.markdown(f'<div style="background:#1a1a1a;border-left:4px solid {c};'
                    f'padding:6px 10px;border-radius:4px;font-size:0.82rem;">'
                    f'<b style="color:{c}">{lbl}</b> — {desc}</div>',
                    unsafe_allow_html=True)
        if suggest:
            st.markdown(f'<div style="font-size:0.78rem;color:#ffcc00;margin-top:4px">{suggest}</div>',
                        unsafe_allow_html=True)

    with ctrl_r:
        st.markdown("**⏱ Speed & Controls**")
        sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
        for col, sp in zip([sc1,sc2,sc3,sc4], ["1x","2x","5x","10x"]):
            if col.button(sp, key=f"spd_{sp}",
                          type="primary" if speed==sp else "secondary",
                          use_container_width=True):
                st.session_state.speed = sp; st.rerun()
        pause_label = "▶ RESUME" if paused else "⏸ PAUSE"
        if sc5.button(pause_label, key="btn_pause", use_container_width=True):
            st.session_state.live_paused = not paused; st.rerun()
        if sc6.button("⏭ FINISH", key="btn_finish", use_container_width=True):
            while not race.finished:
                race.simulate_lap(push_level=push_level, tactic=tactic)
            st.rerun()

    if st.button("🔄  NEW RACE", key="btn_new"):
        for k, v in {"race": None, "race_phase": "setup", "lights_count": 0,
                     "visual_time": 0.0, "lap_expected": 91.0, "live_paused": False,
                     "selected_strategy": 1, "radio_log": [], "prev_player_pos": None, "prev_fl_name": None}.items():
            st.session_state[k] = v
        st.rerun()

st.markdown("---")

# ── Build timing rows (shared) ─────────────────────────────────────────────────
tire_sym = {"Soft":"🔴 S","Medium":"🟡 M","Hard":"⚪ H"}
rows = []
for d in ordered_now:
    is_p   = d.is_player
    is_fl  = (_fl_driver is not None and d.name == _fl_driver.name)
    lp     = _live_pos.get(d.name, d.position)
    in_pit = d.name in _pit_drivers
    gap    = ("PIT" if in_pit else
              ("LEADER" if lp == 1 else
               ("—" if race.current_lap == 0 else f"+{_gaps.get(d.name,0.0):.3f}s")))
    last   = _fmt(d.last_lap.lap_time) if d.last_lap else "—"
    best   = _fmt(d.best_lap)          if d.best_lap  else "—"
    t_sym  = tire_sym.get(d.tire.compound,"🟢")
    fl_tag = " 💜" if is_fl else ""
    rows.append({"P": lp, "Driver":("★ " if is_p else "  ")+d.name+fl_tag,
                 "Team":d.team,"Gap":gap,"Last":last,"Best":best,
                 "Tire":f"{t_sym} L{d.tire.age}","Wear":f"{d.tire.wear_pct}%",
                 "_p":is_p, "_fl":is_fl})

df_all = pd.DataFrame(rows)
p_idx  = set(df_all.index[df_all["_p"]])
fl_idx = set(df_all.index[df_all["_fl"]])
df_disp = df_all.drop(columns=["_p", "_fl"])

def _hl(row):
    if row.name in p_idx:
        return ["background-color:#002200;color:#00FF41;font-weight:bold"]*len(row)
    if row.name in fl_idx:
        return ["background-color:#1a0033;color:#cc44ff;font-weight:bold"]*len(row)
    return ["color:#d0d0d0"]*len(row)

# ── TABS ───────────────────────────────────────────────────────────────────────
tab_pist, tab_detail = st.tabs(["🗺️  LIVE TRACK", "📊  ANALYSIS"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CANLI PİST
# ══════════════════════════════════════════════════════════════════════════════
with tab_pist:
    # ── YARŞ SONU ÖZET ─────────────────────────────────────────────────────────
    if race.finished:
        fin_st = race.standings
        p_fin  = player.position
        emoji  = {1:"🥇",2:"🥈",3:"🥉"}
        pos_change = player.grid_pos - p_fin
        pos_arrow  = f"▲{pos_change}" if pos_change > 0 else (f"▼{abs(pos_change)}" if pos_change < 0 else "▬")
        pos_color  = "#00FF41" if pos_change > 0 else ("#ff4444" if pos_change < 0 else "#aaaaaa")
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#0a0a0a,#1a0a00);
                    border:2px solid #e10600;border-radius:12px;padding:32px;margin-bottom:20px;">
            <div style="text-align:center;font-size:2.8rem;font-family:'Arial Black';
                        color:#e10600;letter-spacing:6px;margin-bottom:8px;">🏁 RACE FINISHED</div>
            <div style="text-align:center;color:#888;letter-spacing:3px;margin-bottom:24px;">
                P{player.grid_pos} GRID → <b style="color:{pos_color};font-size:1.3rem;">P{p_fin} {pos_arrow}</b>
                &nbsp;|&nbsp; {len(player.pitted_laps)} Pit
                &nbsp;|&nbsp; Best: <b style="color:#cc44ff">{_fmt(player.best_lap) if player.best_lap else "—"}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Podyum
        pod_cols = st.columns(3)
        pod_order = [1, 0, 2]  # Altın ortada görsel etki için
        for slot, rank in enumerate(pod_order):
            if rank < len(fin_st):
                d = fin_st[rank]
                pos = rank + 1
                col_bg = "#002200" if d.is_player else "#111"
                col_br = "#00FF41" if d.is_player else "#333"
                ht = {1:"140px", 0:"120px", 2:"100px"}[slot]
                with pod_cols[slot]:
                    st.markdown(f"""
                    <div style="background:{col_bg};border:2px solid {col_br};border-radius:10px;
                                padding:16px;text-align:center;min-height:{ht};">
                        <div style="font-size:2.5rem">{emoji.get(pos,'')}</div>
                        <div style="font-size:0.95rem;font-weight:bold;color:white;margin:4px 0">{d.name}</div>
                        <div style="font-size:0.78rem;color:#888">{d.team}</div>
                        <div style="font-size:0.8rem;color:#aaa;margin-top:6px">{_fmt(d.best_lap) if d.best_lap else "—"}</div>
                    </div>""", unsafe_allow_html=True)

        # FL + tam sonuç
        if _fl_driver:
            st.markdown(f'<div class="alert-fl">💜 FASTEST LAP — {_fl_driver.name} &nbsp; {_fmt(_fl_time)}</div>',
                        unsafe_allow_html=True)
        st.markdown("---")
        st.markdown("#### 📋 FULL RESULTS")
        st.dataframe(df_disp.style.apply(_hl, axis=1).set_properties(**{"text-align":"left"}),
                     hide_index=True, height=400, use_container_width=True)
        st.markdown("---")

    # ── CANLI GÖRÜNÜM (yarış devam ederken veya altında) ───────────────────────
    if not race.finished:
        map_col, tower_col = st.columns([3, 2], gap="medium")

        with map_col:
            st.markdown("#### 🏟️  SILVERSTONE")
            st.plotly_chart(
                track_figure(race, _vis, sc_active=sc_active, pit_drivers=_pit_drivers),
                use_container_width=True,
            )
            st.markdown("---")
            st.markdown("#### 🎧 ENGINEER PANEL")
            if race.current_lap == 0:
                t = player.tire
                sym = {"Soft": "🔴", "Medium": "🟡", "Hard": "⚪"}
                st.markdown('<div class="alert-ok">⬥ READY — All systems nominal.</div>',
                            unsafe_allow_html=True)
                eng_l, eng_r = st.columns(2)
                with eng_l:
                    st.markdown("##### 🛞 TIRES")
                    st.metric("Compound", f"{sym.get(t.compound,'🟢')} {t.compound}")
                    st.metric("Age", "New")
                    st.metric("Wear", "0%")
                with eng_r:
                    st.markdown("##### 🏁 START")
                    st.metric("Grid", f"P{player.grid_pos}")
                    st.metric("Est. Lap", _fmt(player.base_pace + t.pace_penalty))
                    st.metric("Tactic", st.session_state.tactic)
                    if player.pit_laps:
                        st.metric("First Pit", f"Lap {player.pit_laps[0]}")
            else:
                p_lap = player.last_lap
                alerts = []
                if p_lap:
                    if p_lap.gap_ahead is not None and p_lap.gap_ahead <= 1.0:
                        alerts.append(("drs",  f"DRS RANGE — Ahead: {p_lap.gap_ahead:.3f}s | DRS active!"))
                    elif p_lap.gap_ahead is not None and p_lap.gap_ahead <= 3.0:
                        alerts.append(("hunt", f"HUNT — Closing on P{player.position-1}: {p_lap.gap_ahead:.3f}s"))
                    if p_lap.gap_behind is not None and p_lap.gap_behind <= 1.5:
                        alerts.append(("def",  f"DEFEND — Car behind: {p_lap.gap_behind:.3f}s"))
                    if p_lap.has_mistake:
                        alerts.append(("tire", f"MISTAKE — {_fmt(p_lap.lap_time)} | Pace lost"))
                    if p_lap.safety_car:
                        sc_msg = "SAFETY CAR — Pit window open!"
                        if race.sc_reason:
                            sc_msg += f"  ({race.sc_reason})"
                        alerts.append(("sc", sc_msg))
                    if player.tire.wear_pct >= 80:
                        alerts.append(("tire", f"TIRES CRITICAL — {player.tire.wear_pct}% | Box needed!"))
                    elif player.tire.wear_pct >= 60:
                        alerts.append(("tire", f"TIRE WARNING — {player.tire.wear_pct}%"))
                    cl = [l.lap_time for l in player.lap_history[-4:] if not l.is_pit_lap and not l.safety_car]
                    if len(cl) >= 4 and cl[-1]-cl[0] > 0.4:
                        alerts.append(("tire", f"PACE DROP — +{cl[-1]-cl[0]:.3f}s (last 4 laps)"))
                if _fl_driver and _fl_driver.is_player:
                    alerts.insert(0, ("fl", f"FASTEST LAP — {_fmt(_fl_time)} 💜"))
                if not alerts:
                    alerts.append(("ok", "All systems nominal."))
                cm = {"drs":"alert-drs","hunt":"alert-hunt","def":"alert-def",
                      "tire":"alert-tire","sc":"alert-sc","ok":"alert-ok","fl":"alert-fl"}
                for kind, msg in alerts:
                    st.markdown(f'<div class="{cm[kind]}">⬥ {msg}</div>', unsafe_allow_html=True)

                eng_l, eng_r = st.columns(2)
                with eng_l:
                    st.markdown("##### 🛞 TIRES")
                    t = player.tire
                    sym = {"Soft":"🔴","Medium":"🟡","Hard":"⚪"}
                    st.metric("Compound", f"{sym.get(t.compound,'🟢')} {t.compound}")
                    st.metric("Age",      f"{t.age} laps")
                    st.metric("Wear",     f"{t.wear_pct}%")
                    nxt = [l for l in player.pit_laps if l > race.current_lap]
                    if nxt:   st.metric("Laps to pit", f"{nxt[0]-race.current_lap}")
                    elif player.pitted_laps: st.metric("Pit", "✅")
                with eng_r:
                    st.markdown("##### 📐 SECTORS")
                    if p_lap:
                        st_ord = race.standings
                        pidx = player.position - 1
                        if pidx > 0:
                            rival = st_ord[pidx-1]
                            rl = rival.last_lap
                            if rl:
                                st.caption(f"You vs P{rival.position} {rival.name[:14]}")
                                for sn, ms, rs in [("S1",p_lap.s1,rl.s1),
                                                   ("S2",p_lap.s2,rl.s2),
                                                   ("S3",p_lap.s3,rl.s3)]:
                                    dv = ms-rs; sg = "+" if dv>0 else ""
                                    cl2 = "#ff4444" if dv>0 else "#44ff88"
                                    st.markdown(f"**{sn}** `{ms:.3f}s` <span style='color:{cl2}'>({sg}{dv:.3f})</span>",
                                                unsafe_allow_html=True)
                                td = p_lap.lap_time-rl.lap_time
                                tc = "#ff4444" if td>0 else "#44ff88"
                                ts = "+" if td>0 else ""
                                st.markdown(f"**LAP** `{_fmt(p_lap.lap_time)}` <span style='color:{tc}'>({ts}{td:.3f})</span>",
                                            unsafe_allow_html=True)
                        else:
                            st.caption("You are the leader")
                            for sn, v in [("S1",p_lap.s1),("S2",p_lap.s2),("S3",p_lap.s3)]:
                                st.markdown(f"**{sn}** `{v:.3f}s`", unsafe_allow_html=True)

                if st.session_state.radio_log:
                    st.markdown("---")
                    st.markdown("##### 📻 RADIO")
                    for entry in reversed(st.session_state.radio_log[-4:]):
                        st.markdown(
                            f'<div class="radio-msg">T{entry["lap"]:02d} › {entry["msg"]}</div>',
                            unsafe_allow_html=True)

        with tower_col:
            st.markdown("#### 🏁  TIMING TOWER")
            st.dataframe(
                df_disp.style.apply(_hl, axis=1).set_properties(**{"text-align":"left"}),
                hide_index=True, height=1100, use_container_width=True,
            )

        # ── Telemetry ────────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### 📡 TELEMETRY")
        tel_col, _ = st.columns([3, 2])
        with tel_col:
            st.plotly_chart(
                telemetry_figure(_telem["speed"], _telem["rpm"],
                                 _telem["throttle"], _telem["brake"]),
                use_container_width=True,
            )
            drs_html = ('<span style="background:#00FF41;color:#000;font-weight:bold;'
                        'padding:3px 10px;border-radius:4px;">✅ DRS</span>'
                        if _telem["drs"] else
                        '<span style="background:#1a1a1a;color:#555;border:1px solid #333;'
                        'padding:3px 10px;border-radius:4px;">DRS</span>')
            st.markdown(
                f'<div style="text-align:center;padding:6px 0;font-size:0.92rem;color:#ccc;">'
                f'⚙️ <b style="color:white">GEAR {_telem["gear"]}</b>'
                f'&nbsp;&nbsp;&nbsp;{drs_html}</div>',
                unsafe_allow_html=True,
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ANALİZ
# ══════════════════════════════════════════════════════════════════════════════
with tab_detail:
    if race.current_lap < 2:
        st.info("At least 2 laps required for analysis.")
    else:
        al, ar = st.columns([3, 2])
        with al:
            st.markdown("#### 📈 LAP TIMES")
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[l.lap for l in player.lap_history],
                y=[l.lap_time for l in player.lap_history],
                mode="lines+markers", name=player.name,
                line=dict(color="#00FF41",width=2),
                marker=dict(size=5, color=[
                    "#ff4444" if l.has_mistake or l.is_pit_lap else "#00FF41"
                    for l in player.lap_history]),
                text=[_fmt(l.lap_time) for l in player.lap_history],
                hoverinfo="text+x",
            ))
            st_ord2 = race.standings
            if player.position > 1:
                rv2 = st_ord2[player.position - 2]
                fig.add_trace(go.Scatter(
                    x=[l.lap for l in rv2.lap_history],
                    y=[l.lap_time for l in rv2.lap_history],
                    mode="lines", name=f"P{rv2.position} {rv2.name[:12]}",
                    line=dict(color="#ff6666",width=1,dash="dash"), opacity=0.7,
                    text=[_fmt(l.lap_time) for l in rv2.lap_history],
                    hoverinfo="text+x",
                ))
            for pl in player.pitted_laps:
                fig.add_vline(x=pl, line_dash="dot", line_color="yellow",
                              annotation_text="PIT", annotation_font_color="yellow",
                              annotation_position="top left")
            for sc in race.safety_car_laps:
                if sc <= race.current_lap:
                    fig.add_vline(x=sc, line_dash="dot", line_color="orange",
                                  annotation_text="SC", annotation_font_color="orange",
                                  annotation_position="bottom left")
            # Y axis with lap time format
            all_lt = [l.lap_time for l in player.lap_history]
            tick_vals = []
            tick_texts = []
            if all_lt:
                mn, mx = min(all_lt)-1, max(all_lt)+5
                import numpy as _np
                for tv in _np.arange(round(mn), round(mx)+1, 2):
                    tick_vals.append(tv)
                    tick_texts.append(_fmt(tv))
            fig.update_layout(
                paper_bgcolor="#0d0d0d", plot_bgcolor="#1a1a1a",
                font=dict(color="#f0f0f0",size=11),
                xaxis=dict(title="Lap", gridcolor="#333", color="#aaa"),
                yaxis=dict(title="Lap Time", gridcolor="#333", color="#aaa",
                           tickvals=tick_vals, ticktext=tick_texts),
                height=320, margin=dict(l=0,r=0,t=10,b=0),
                legend=dict(orientation="h",y=1.12,font=dict(size=10)),
            )
            st.plotly_chart(fig, use_container_width=True)

        with ar:
            st.markdown("#### 🏆 SUMMARY")
            st.metric("Position",   f"P{player.position}")
            st.metric("Best Lap",   _fmt(player.best_lap) if player.best_lap else "—")
            st.metric("Total Pits", str(len(player.pitted_laps)))

            if race.finished:
                st.markdown("---")
                st.markdown("**🏆 PODIUM**")
                pod2 = race.standings[:3]
                m2 = {1:"🥇",2:"🥈",3:"🥉"}
                for d in pod2:
                    st.markdown(f"{m2[d.position]} **{d.name}** ({d.team})")

        # ── Gap to Leader chart ─────────────────────────────────────────────
        gap_data = [(l.lap, l.gap_to_leader) for l in player.lap_history
                    if l.gap_to_leader is not None]
        if gap_data:
            st.markdown("#### 📉 GAP TO LEADER")
            laps_g = [d[0] for d in gap_data]
            gaps_g = [d[1] for d in gap_data]
            fig_gap = go.Figure()
            fig_gap.add_trace(go.Scatter(
                x=laps_g, y=gaps_g,
                mode="lines+markers",
                line=dict(color="#ffcc00", width=2),
                fill="tozeroy",
                fillcolor="rgba(255,204,0,0.06)",
                marker=dict(size=4, color="#ffcc00"),
                text=["LEADER" if g == 0.0 else f"+{g:.3f}s" for g in gaps_g],
                hoverinfo="text+x",
                showlegend=False,
            ))
            for pl in player.pitted_laps:
                fig_gap.add_vline(x=pl, line_dash="dot", line_color="yellow",
                                  annotation_text="PIT", annotation_font_color="yellow",
                                  annotation_position="top right")
            for sc in race.safety_car_laps:
                if sc <= race.current_lap:
                    fig_gap.add_vline(x=sc, line_dash="dot", line_color="orange",
                                      annotation_text="SC", annotation_font_color="orange",
                                      annotation_position="top left")
            fig_gap.update_layout(
                paper_bgcolor="#0d0d0d", plot_bgcolor="#1a1a1a",
                font=dict(color="#f0f0f0", size=11),
                xaxis=dict(title="Lap", gridcolor="#333", color="#aaa"),
                yaxis=dict(title="Gap to Leader (s)", gridcolor="#333", color="#aaa"),
                height=240, margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig_gap, use_container_width=True)

        # ── Stint visualization ─────────────────────────────────────────────
        if player.lap_history:
            st.markdown("#### 🏎 STINT ANALYSIS")
            _stints, _prev_cmp = [], None
            for _ld in player.lap_history:
                if _ld.tire_compound != _prev_cmp:
                    _stints.append({"cmp": _ld.tire_compound, "laps": []})
                    _prev_cmp = _ld.tire_compound
                _stints[-1]["laps"].append(_ld.lap)
            _cmp_col = {"Soft": "#e10600", "Medium": "#ffcc00", "Hard": "#dddddd"}
            fig_st = go.Figure()
            for _i, _s in enumerate(_stints):
                _col = _cmp_col.get(_s["cmp"], "#888")
                _sl, _el = _s["laps"][0], _s["laps"][-1]
                _cnt = len(_s["laps"])
                fig_st.add_trace(go.Bar(
                    x=[_cnt], y=["Sürücü"], orientation="h",
                    name=f"Stint {_i+1}: {_s['cmp']}",
                    marker=dict(color=_col, line=dict(color="#000", width=1)),
                    base=_sl - 1,
                    text=f"{_s['cmp'][0]} ({_cnt}L)",
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(color="#000" if _s["cmp"] != "Soft" else "#fff", size=11, family="Arial Black"),
                    hovertext=f"Stint {_i+1}: {_s['cmp']}<br>L{_sl}–L{_el} ({_cnt} laps)",
                    hoverinfo="text",
                ))
            # Pit dikey çizgileri
            for _pl in player.pitted_laps:
                fig_st.add_vline(x=_pl, line_dash="dot", line_color="white", line_width=1)
            fig_st.update_layout(
                paper_bgcolor="#0d0d0d", plot_bgcolor="#1a1a1a",
                barmode="stack",
                font=dict(color="#f0f0f0"),
                xaxis=dict(title="Lap", range=[0, race.total_laps],
                           gridcolor="#333", color="#aaa", dtick=5),
                yaxis=dict(visible=False),
                height=110, margin=dict(l=0, r=0, t=5, b=30),
                legend=dict(orientation="h", y=1.6, font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
                showlegend=True,
            )
            st.plotly_chart(fig_st, use_container_width=True)

        # ── Position History chart ──────────────────────────────────────────
        if race.current_lap >= 2:
            st.markdown("#### 🏆 POSITION HISTORY")
            fig_pos = go.Figure()

            for _d in race.drivers:
                if not _d.lap_history:
                    continue
                _laps = [l.lap for l in _d.lap_history]
                _pos  = [l.position for l in _d.lap_history]
                _is_p = _d.is_player

                fig_pos.add_trace(go.Scatter(
                    x=_laps, y=_pos,
                    mode="lines",
                    name=_d.name if _is_p else _d.name[:14],
                    line=dict(
                        color="#00FF41" if _is_p else _d.color,
                        width=3 if _is_p else 1,
                    ),
                    opacity=1.0 if _is_p else 0.45,
                    showlegend=_is_p,
                    hovertemplate=f"<b>{_d.name}</b><br>Tur %{{x}} → P%{{y}}<extra></extra>",
                ))

            # Player pit stop markers
            _pit_m_laps = [l.lap for l in player.lap_history if l.is_pit_lap]
            _pit_m_pos  = [l.position for l in player.lap_history if l.is_pit_lap]
            if _pit_m_laps:
                fig_pos.add_trace(go.Scatter(
                    x=_pit_m_laps, y=_pit_m_pos,
                    mode="markers",
                    name="PIT",
                    marker=dict(size=12, color="#ffcc00", symbol="diamond",
                                line=dict(width=2, color="#000")),
                    hovertemplate="PIT STOP<br>Tur %{x} | P%{y}<extra></extra>",
                ))

            # SC lap markers
            for _sc in race.safety_car_laps:
                if _sc <= race.current_lap:
                    fig_pos.add_vline(x=_sc, line_dash="dot",
                                      line_color="rgba(255,200,0,0.4)", line_width=1)

            fig_pos.update_layout(
                paper_bgcolor="#0d0d0d", plot_bgcolor="#1a1a1a",
                font=dict(color="#f0f0f0", size=11),
                xaxis=dict(title="Tur", gridcolor="#2a2a2a", color="#aaa",
                           range=[1, race.total_laps]),
                yaxis=dict(
                    title="Pozisyon", gridcolor="#2a2a2a", color="#aaa",
                    autorange="reversed",
                    tickvals=list(range(1, 21)),
                    ticktext=[f"P{i}" for i in range(1, 21)],
                ),
                height=400,
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=True,
                legend=dict(orientation="h", y=1.05, font=dict(size=10),
                            bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig_pos, use_container_width=True)

# ── AUTO-RERUN (live mode) ──────────────────────────────────────────────────────
if not paused and not race.finished:
    time.sleep(0.25)
    st.rerun()

if race.finished:
    st.session_state.live_paused = True
