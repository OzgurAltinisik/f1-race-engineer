import numpy as np
import plotly.graph_objects as go

ABBREV = {
    "Max Verstappen": "VER", "Sergio Perez": "PER",
    "Lewis Hamilton": "HAM", "Charles Leclerc": "LEC",
    "Lando Norris": "NOR", "Oscar Piastri": "PIA",
    "George Russell": "RUS", "Kimi Antonelli": "ANT",
    "Carlos Sainz": "SAI", "Alex Albon": "ALB",
    "Fernando Alonso": "ALO", "Lance Stroll": "STR",
    "Nico Hulkenberg": "HUL", "Gabriel Bortoleto": "BOR",
    "Pierre Gasly": "GAS", "Jack Doohan": "DOO",
    "Yuki Tsunoda": "TSU", "Isack Hadjar": "HAD",
    "Oliver Bearman": "BEA", "Esteban Ocon": "OCO",
}


def get_abbrev(name: str) -> str:
    return ABBREV.get(name, name.split()[-1][:3].upper())


# ── Silverstone waypoints ───────────────────────────────────────────────────
_WP = [
    (0.500, 0.380),
    (0.555, 0.372), (0.620, 0.362), (0.678, 0.352),
    (0.728, 0.344), (0.768, 0.348),
    (0.798, 0.366), (0.814, 0.394),
    (0.812, 0.424), (0.796, 0.450),
    (0.770, 0.466), (0.740, 0.470),
    (0.710, 0.460), (0.692, 0.444),
    (0.682, 0.472), (0.680, 0.512),
    (0.680, 0.558), (0.675, 0.602),
    (0.660, 0.642), (0.634, 0.666),
    (0.600, 0.675), (0.562, 0.670),
    (0.530, 0.660), (0.500, 0.652),
    (0.466, 0.645), (0.432, 0.648),
    (0.400, 0.660), (0.370, 0.674),
    (0.340, 0.680), (0.310, 0.672),
    (0.282, 0.650), (0.262, 0.618),
    (0.255, 0.582), (0.258, 0.548),
    (0.266, 0.514), (0.270, 0.480),
    (0.268, 0.448), (0.260, 0.418),
    (0.254, 0.385), (0.260, 0.354),
    (0.279, 0.330), (0.310, 0.318),
    (0.348, 0.318), (0.382, 0.326),
    (0.414, 0.336), (0.446, 0.350),
    (0.474, 0.365), (0.500, 0.380),
]


def _build_track(waypoints, n: int = 600):
    pts = np.array(waypoints + [waypoints[0]])
    dx, dy = np.diff(pts[:, 0]), np.diff(pts[:, 1])
    seg = np.sqrt(dx**2 + dy**2)
    cum = np.r_[0, np.cumsum(seg)]
    t = np.linspace(0, cum[-1], n, endpoint=False)
    return np.interp(t, cum, pts[:, 0]), np.interp(t, cum, pts[:, 1])


TRACK_X, TRACK_Y = _build_track(_WP)
N = len(TRACK_X)


def _compute_speed_profile() -> np.ndarray:
    """km/h speed at each track point based on local curvature."""
    x, y = TRACK_X, TRACK_Y
    dx  = np.gradient(x)
    dy  = np.gradient(y)
    ddx = np.gradient(dx)
    ddy = np.gradient(dy)
    denom = (dx**2 + dy**2)**1.5 + 1e-10
    curv = np.abs(dx * ddy - dy * ddx) / denom

    # Smooth (circular)
    w = 18
    k = np.ones(w) / w
    curv_s = np.convolve(np.tile(curv, 3), k, mode="same")[N: 2 * N]

    curv_n = np.clip(curv_s / (curv_s.max() + 1e-10), 0, 1)
    speed = 115 + (325 - 115) * (1 - np.clip(curv_n * 2.8, 0, 1))
    return speed


SPEED_PROFILE: np.ndarray = _compute_speed_profile()   # shape (N,)


def lap_time_at_speed_profile(push_level: int = 1) -> float:
    """Rough expected lap time from the speed profile (seconds)."""
    seg_dx = np.diff(np.append(TRACK_X, TRACK_X[0]))
    seg_dy = np.diff(np.append(TRACK_Y, TRACK_Y[0]))
    seg_len = np.sqrt(seg_dx**2 + seg_dy**2)       # normalised units
    circuit_m = 5891.0                              # Silverstone metres
    unit_m = circuit_m / seg_len.sum()
    push_mult = {0: 0.93, 1: 1.0, 2: 1.07}[push_level]
    speed_ms = SPEED_PROFILE * push_mult / 3.6
    times = (seg_len * unit_m) / speed_ms
    return float(times.sum())


def compute_visual_positions(race, visual_time: float, lap_expected: float) -> dict:
    """
    Each driver advances at their own pace (best_lap or base_pace estimate).
    This causes gaps to naturally update every tick as faster drivers gain.
    """
    if race.current_lap == 0:
        out = {}
        for d in race.drivers:
            grid_start = max(0.0, 1.0 - (d.grid_pos - 1) * 0.006)
            drv_lt = max(d.base_pace + d.tire.pace_penalty, 85.0)
            out[d.name] = (grid_start + visual_time / drv_lt) % 1.0
        return out

    ordered = race.standings
    leader = ordered[0]
    leader_lt = leader.best_lap or lap_expected

    out = {}
    for d in ordered:
        drv_lt = d.best_lap or (d.base_pace + d.tire.pace_penalty)
        drv_lt = max(drv_lt, 85.0)
        accumulated_gap = d.total_time - leader.total_time
        drv_elapsed = visual_time - accumulated_gap
        progress = (drv_elapsed % drv_lt) / drv_lt
        out[d.name] = progress % 1.0
    return out


def compute_live_gaps(race, vis_positions: dict, lap_expected: float) -> dict:
    """
    Convert track position differences to time gaps (seconds).
    Updates every visual tick as drivers move at different speeds.
    """
    ordered = race.standings
    if not ordered:
        return {}

    leader = ordered[0]
    leader_prog = vis_positions.get(leader.name, 0.0)

    gaps: dict = {leader.name: 0.0}
    for d in ordered[1:]:
        drv_lt = d.best_lap or lap_expected
        drv_prog = vis_positions.get(d.name, 0.0)
        diff = (leader_prog - drv_prog) % 1.0
        gaps[d.name] = round(diff * drv_lt, 3)
    return gaps


def get_telemetry(track_progress: float, push_level: int, has_drs: bool) -> dict:
    """Returns telemetry values based on track position and push level."""
    idx      = int(track_progress * N) % N
    idx_next = (idx + 12) % N

    base_spd    = SPEED_PROFILE[idx]
    push_mult   = {0: 0.93, 1: 1.0, 2: 1.07}[push_level]
    speed_kmh   = int(base_spd * push_mult)

    rpm  = int(speed_kmh * 31.8 + np.random.randint(-250, 250))
    rpm  = int(np.clip(rpm, 4000, 13800))
    gear = max(1, min(8, int(speed_kmh / 43)))

    spd_next = SPEED_PROFILE[idx_next] * push_mult
    delta    = spd_next - speed_kmh
    if delta >= 0:                                  # accelerating
        throttle = int(np.clip(80 * push_mult + delta * 0.4, 0, 100))
        brake    = 0
    else:                                           # braking
        throttle = int(np.clip(20 + delta * 0.2,   0,  60))
        brake    = int(np.clip(-delta * 0.7,        0, 100))

    drs_active = has_drs and speed_kmh > 240

    return dict(speed=speed_kmh, rpm=rpm, gear=gear,
                throttle=throttle, brake=brake, drs=drs_active)


# ── Figures ─────────────────────────────────────────────────────────────────

# ── Pit lane waypoints (Silverstone: parallel to pit straight, inside the track)
_PIT_WP = [
    (0.466, 0.378),   # pit entry gate (before start/finish, left side)
    (0.468, 0.390),   # entry ramp
    (0.480, 0.395),
    (0.496, 0.397),
    (0.512, 0.398),
    (0.530, 0.397),
    (0.548, 0.394),
    (0.556, 0.388),   # exit ramp
    (0.558, 0.375),   # pit exit gate (after start/finish, right side)
]
_PX = [p[0] for p in _PIT_WP]
_PY = [p[1] for p in _PIT_WP]


def _interp_pit_lane(pit_prog: float):
    """Return (x, y) along _PIT_WP for pit_prog in [0, 1]."""
    pit_prog = max(0.0, min(1.0, pit_prog))
    n = len(_PIT_WP) - 1
    fi = pit_prog * n
    i = min(int(fi), n - 1)
    frac = fi - i
    x = _PIT_WP[i][0] + frac * (_PIT_WP[i + 1][0] - _PIT_WP[i][0])
    y = _PIT_WP[i][1] + frac * (_PIT_WP[i + 1][1] - _PIT_WP[i][1])
    return x, y


def track_figure(race, track_positions: dict = None, sc_active: bool = False,
                 pit_drivers: dict = None) -> go.Figure:
    """
    track_positions: {driver_name: progress_0_to_1}  (optional)
    sc_active: True to paint racing line yellow (Safety Car)
    pit_drivers: {driver_name: pit_progress_0_to_1} — rendered on pit lane
    """
    fig = go.Figure()

    tx = np.append(TRACK_X, TRACK_X[0])
    ty = np.append(TRACK_Y, TRACK_Y[0])

    # Track base layers
    fig.add_trace(go.Scatter(x=tx, y=ty, mode="lines",
                             line=dict(color="#2a2a2a", width=18),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=tx, y=ty, mode="lines",
                             line=dict(color="#3d3d3d", width=12),
                             hoverinfo="skip", showlegend=False))
    # Racing line — 3 sectors; all yellow during SC
    if sc_active:
        s1_col = s2_col = s3_col = "#ffcc00"
    else:
        s1_col, s2_col, s3_col = "#4466ee", "#ff7700", "#cc44bb"

    n1, n2 = N // 3, 2 * N // 3
    fig.add_trace(go.Scatter(
        x=TRACK_X[:n1 + 1], y=TRACK_Y[:n1 + 1], mode="lines",
        line=dict(color=s1_col, width=3),
        hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=TRACK_X[n1:n2 + 1], y=TRACK_Y[n1:n2 + 1], mode="lines",
        line=dict(color=s2_col, width=3),
        hoverinfo="skip", showlegend=False))
    s3x = list(TRACK_X[n2:]) + [TRACK_X[0]]
    s3y = list(TRACK_Y[n2:]) + [TRACK_Y[0]]
    fig.add_trace(go.Scatter(
        x=s3x, y=s3y, mode="lines",
        line=dict(color=s3_col, width=3),
        hoverinfo="skip", showlegend=False))

    # Sector boundary markers
    for idx, label, col in [(0, "S1", s1_col), (n1, "S2", s2_col), (n2, "S3", s3_col)]:
        bx, by = TRACK_X[idx], TRACK_Y[idx]
        cx, cy = 0.5, 0.5
        dx, dy = cx - bx, cy - by
        length = max((dx**2 + dy**2)**0.5, 0.001)
        lx = bx + dx / length * 0.024
        ly = by + dy / length * 0.024
        fig.add_annotation(x=lx, y=ly, text=label, showarrow=False,
                           font=dict(size=8, color=col, family="Arial Black"),
                           xanchor="center")

    # ── Pit lane ────────────────────────────────────────────────────────────
    # Outer border
    fig.add_trace(go.Scatter(x=_PX, y=_PY, mode="lines",
                             line=dict(color="#1a1a1a", width=14),
                             hoverinfo="skip", showlegend=False))
    # Surface
    fig.add_trace(go.Scatter(x=_PX, y=_PY, mode="lines",
                             line=dict(color="#2e2e2e", width=9),
                             hoverinfo="skip", showlegend=False))
    # Centre dashes
    fig.add_trace(go.Scatter(x=_PX, y=_PY, mode="lines",
                             line=dict(color="#ffcc00", width=1, dash="dot"),
                             hoverinfo="skip", showlegend=False))
    # Entry / exit gates (white line)
    for gx, gy in [(_PIT_WP[0], _PIT_WP[1]), (_PIT_WP[-2], _PIT_WP[-1])]:
        fig.add_trace(go.Scatter(
            x=[gx[0], gy[0]], y=[gx[1], gy[1]], mode="lines",
            line=dict(color="white", width=3),
            hoverinfo="skip", showlegend=False,
        ))
    # "PIT LANE" label
    mid = len(_PIT_WP) // 2
    fig.add_annotation(x=_PX[mid], y=_PY[mid] + 0.012, text="PIT LANE",
                       showarrow=False,
                       font=dict(size=8, color="#666", family="Arial Black"),
                       xanchor="center")

    # ── Start/Finish line ───────────────────────────────────────────────────
    dx_sf = TRACK_X[1] - TRACK_X[0]
    dy_sf = TRACK_Y[1] - TRACK_Y[0]
    norm  = max(np.sqrt(dx_sf**2 + dy_sf**2), 1e-9)
    px, py = -dy_sf / norm * 0.022, dx_sf / norm * 0.022
    fig.add_trace(go.Scatter(
        x=[TRACK_X[0] - px, TRACK_X[0] + px],
        y=[TRACK_Y[0] - py, TRACK_Y[0] + py],
        mode="lines", line=dict(color="white", width=5),
        hoverinfo="skip", showlegend=False,
    ))

    # SC board on pit wall
    if sc_active:
        fig.add_annotation(
            x=_PX[mid] + 0.02, y=_PY[mid] - 0.018,
            text="🟡 SC", showarrow=False,
            font=dict(size=11, color="#ffcc00", family="Arial Black"),
            xanchor="center",
        )

    # Fallback: compute from race gaps if no explicit positions given
    if track_positions is None:
        if race.current_lap > 0:
            ordered = race.standings
            lap_times = [d.best_lap for d in ordered if d.best_lap]
            avg_lap = float(np.mean(lap_times)) if lap_times else 91.0
            leader_time = ordered[0].total_time
            track_positions = {}
            for d in ordered:
                gap  = d.total_time - leader_time
                frac = 1.0 - min(1.0, gap / avg_lap)
                track_positions[d.name] = frac
        else:
            track_positions = {d.name: max(0.0, 1.0 - (d.grid_pos - 1) * 0.006)
                               for d in race.drivers}

    drivers_by_name = {d.name: d for d in race.drivers}

    pit_set = set(pit_drivers.keys()) if pit_drivers else set()

    for name, progress in track_positions.items():
        if name in pit_set:
            continue  # rendered on pit lane below
        driver = drivers_by_name.get(name)
        if driver is None:
            continue
        idx   = int(progress * N) % N
        x, y  = TRACK_X[idx], TRACK_Y[idx]
        abbr  = get_abbrev(name)
        is_p  = driver.is_player
        size  = 18 if is_p else 11

        # Glow ring (renders under the main dot)
        glow_size    = 34 if is_p else 22
        glow_opacity = 0.25 if is_p else 0.18
        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode="markers",
            marker=dict(size=glow_size, color=driver.color,
                        opacity=glow_opacity, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))

        # Main dot
        fig.add_trace(go.Scatter(
            x=[x], y=[y],
            mode="markers+text",
            marker=dict(size=size, color=driver.color,
                        line=dict(width=3 if is_p else 1.5,
                                  color="white" if is_p else "#bbbbbb")),
            text=[abbr],
            textposition="top center",
            textfont=dict(size=9 if is_p else 7,
                          color="white" if is_p else driver.color,
                          family="Arial Black"),
            hovertext=f"P{driver.position} {name}",
            hoverinfo="text",
            showlegend=False,
        ))

    # ── Pit lane drivers ────────────────────────────────────────────────────
    for name, pit_prog in (pit_drivers or {}).items():
        driver = drivers_by_name.get(name)
        if driver is None:
            continue
        x, y = _interp_pit_lane(pit_prog)
        abbr  = get_abbrev(name)
        is_p  = driver.is_player
        # Glow ring
        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode="markers",
            marker=dict(size=34 if is_p else 20, color=driver.color,
                        opacity=0.30 if is_p else 0.20, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        # Dot — slightly faded with "PIT" label
        fig.add_trace(go.Scatter(
            x=[x], y=[y],
            mode="markers+text",
            marker=dict(size=18 if is_p else 11, color=driver.color,
                        opacity=0.75,
                        line=dict(width=3 if is_p else 1.5,
                                  color="#ffcc00" if is_p else "#888888")),
            text=["PIT"],
            textposition="top center",
            textfont=dict(size=9 if is_p else 7,
                          color="#ffcc00" if is_p else "#aaaaaa",
                          family="Arial Black"),
            hovertext=f"PIT STOP | {abbr}  P{driver.position}",
            hoverinfo="text",
            showlegend=False,
        ))

    fig.add_annotation(x=0.50, y=0.50, text="SILVERSTONE",
                       showarrow=False,
                       font=dict(size=13, color="#444444", family="Arial Black"),
                       xanchor="center")

    fig.update_layout(
        paper_bgcolor="#0d0d0d", plot_bgcolor="#0d0d0d",
        xaxis=dict(visible=False, range=[0.18, 0.85]),
        yaxis=dict(visible=False, range=[0.28, 0.72],
                   scaleanchor="x", scaleratio=1),
        height=420, margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


def telemetry_figure(speed: int, rpm: int, throttle: int, brake: int) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(go.Indicator(
        mode="gauge+number", value=speed,
        title=dict(text="KMH", font=dict(color="#aaa", size=12)),
        number=dict(font=dict(color="white", size=30, family="Arial Black")),
        gauge=dict(
            axis=dict(range=[0, 340], tickfont=dict(color="#555", size=9), dtick=80),
            bar=dict(color="#00FF41", thickness=0.28), bgcolor="#111",
            borderwidth=1, bordercolor="#333",
            steps=[dict(range=[0, 150], color="#0a0a0a"),
                   dict(range=[150, 260], color="#111"),
                   dict(range=[260, 310], color="#161606"),
                   dict(range=[310, 340], color="#1a0606")],
            threshold=dict(line=dict(color="#ff4400", width=3),
                           thickness=0.85, value=318),
        ),
        domain=dict(x=[0.00, 0.32], y=[0, 1]),
    ))

    fig.add_trace(go.Indicator(
        mode="gauge+number", value=rpm,
        title=dict(text="RPM", font=dict(color="#aaa", size=12)),
        number=dict(font=dict(color="white", size=22)),
        gauge=dict(
            axis=dict(range=[0, 14000], tickfont=dict(color="#555", size=8), dtick=3500),
            bar=dict(color="#e10600", thickness=0.28), bgcolor="#111",
            borderwidth=1, bordercolor="#333",
            threshold=dict(line=dict(color="orange", width=3),
                           thickness=0.85, value=12000),
        ),
        domain=dict(x=[0.35, 0.65], y=[0, 1]),
    ))

    fig.add_trace(go.Indicator(
        mode="gauge+number", value=throttle,
        title=dict(text="THROTTLE %", font=dict(color="#aaa", size=12)),
        number=dict(font=dict(color="white", size=22), suffix="%"),
        gauge=dict(
            axis=dict(range=[0, 100], tickfont=dict(color="#555", size=8), dtick=25),
            bar=dict(color="#44cc44", thickness=0.28), bgcolor="#111",
            borderwidth=1, bordercolor="#333",
        ),
        domain=dict(x=[0.68, 1.00], y=[0, 1]),
    ))

    fig.update_layout(paper_bgcolor="#0d0d0d", font=dict(color="white"),
                      height=190, margin=dict(l=10, r=10, t=5, b=5))
    return fig
