"""
executive_data.py
──────────────────────────────────────────────────────────────────────────────
LIVE data layer for /dashboard/executive.

Design goals:
  • Robust to messy real-world data. Dates and numbers are parsed in PYTHON
    (multi-format) instead of SQL ``::date`` / ``::numeric`` casts that fail
    silently on text columns. So a section only falls back to mock when the
    table/column genuinely does not exist or is empty — not because of a format.
  • Period-aware. get_executive_snapshot(period) computes an "as-of" reference
    date from the period selector; expiry/overdue buckets and time-series rows
    are evaluated relative to it, so changing the dropdown changes the numbers.
  • Per-section fallback to executive_mock; the whole thing never raises.

Columns for the tag-based tables come from the working queries in
db_equipment.py. The RU-level tables (paf, monitoring_operasi,
anggaran_maintenance) have no query in the repo, so their columns are matched
by name heuristics at runtime (see _pick). /dashboard/executive/debug reports
exactly what was found.
"""
from __future__ import annotations

import datetime as _dt

import executive_mock

try:
    import db_equipment as _dbe
    _get_conn = _dbe.get_conn
except Exception:  # pragma: no cover
    _dbe = None
    _get_conn = None


# ─── RU normalization ─────────────────────────────────────────────────────────
RU_CODES = ["RU2", "RU3", "RU4", "RU5", "RU6", "RU7", "TPPI"]
RU_DISPLAY = {"RU2": "RU II", "RU3": "RU III", "RU4": "RU IV", "RU5": "RU V",
              "RU6": "RU VI", "RU7": "RU VII", "TPPI": "TPPI"}

_RU_KEYWORDS = [
    ("TPPI", ["tppi", "tuban"]),
    ("RU7",  ["vii", "ru 7", "ru7", "ru-7", "kasim", "sorong"]),
    ("RU6",  ["vi", "ru 6", "ru6", "ru-6", "balongan"]),
    ("RU5",  ["v", "ru 5", "ru5", "ru-5", "balikpapan"]),
    ("RU4",  ["iv", "ru 4", "ru4", "ru-4", "cilacap"]),
    ("RU3",  ["iii", "ru 3", "ru3", "ru-3", "plaju", "palembang"]),
    ("RU2",  ["ii", "ru 2", "ru2", "ru-2", "dumai"]),
]


def match_ru(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    for code, kws in _RU_KEYWORDS:
        for kw in kws:
            if kw.isalpha() and len(kw) > 3 and kw in s:
                return code
    padded = f" {s.replace('-', ' ')} "
    for code, kws in _RU_KEYWORDS:
        for kw in kws:
            if f" {kw} " in padded:
                return code
    return None


# ─── value parsing (tolerant) ─────────────────────────────────────────────────
_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
              "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%b %Y", "%b-%y", "%B %Y",
              "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y")


def _parse_date(v):
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except Exception:
        pass
    for fmt in _DATE_FMTS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def _parse_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(" ", "").replace("Rp", "")
    if not s:
        return None
    if "," in s and "." in s:               # both separators present
        if s.rfind(",") > s.rfind("."):     # 1.234,56  → Indonesian
            s = s.replace(".", "").replace(",", ".")
        else:                               # 1,234.56  → US
            s = s.replace(",", "")
    elif "," in s:                          # comma only → decimal comma
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:                  # 1.000.000.000  → dot thousand sep
        s = s.replace(".", "")
    try:
        return float(s)
    except Exception:
        return None


# ─── period → reference date ──────────────────────────────────────────────────
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def period_asof(period: str):
    """Return (label, as_of_date, start_date) for a period like 'Jun 2026' or
    'YTD 2026'. Falls back to today."""
    today = _dt.date.today()
    if not period:
        return ("", today, None)
    p = period.strip()
    low = p.lower()
    if low.startswith("ytd"):
        year = _year(p) or today.year
        start = _dt.date(year, 1, 1)
        end = min(today, _dt.date(year, 12, 31))
        return (p, end, start)
    for name, mnum in _MONTHS.items():
        if name in low:
            year = _year(p) or today.year
            start = _dt.date(year, mnum, 1)
            end = _month_end(year, mnum)
            return (p, end, start)
    return (p, today, None)


def _year(s):
    import re
    m = re.search(r"(20\d{2})", s)
    return int(m.group(1)) if m else None


def _month_end(year, m):
    if m == 12:
        return _dt.date(year, 12, 31)
    return _dt.date(year, m + 1, 1) - _dt.timedelta(days=1)


# ─── introspection ────────────────────────────────────────────────────────────
def _columns(conn, table):
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,)).fetchall()
    return [r["column_name"] for r in rows]


def _table_exists(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s LIMIT 1", (table,)).fetchone())


def _pick(cols, *needles):
    low = {c.lower(): c for c in cols}
    for needle in needles:
        for lc, orig in low.items():
            if needle in lc:
                return orig
    return None


def _ru_col(cols):
    for c in cols:
        if c.lower() in ("refinery_unit", "ru", "refinery", "unit_kerja", "kilang"):
            return c
    return _pick(cols, "refinery", "ru")


def _fetch(conn, table, wanted):
    """SELECT only the columns from `wanted` that actually exist; return dicts."""
    cols = _columns(conn, table)
    use = [c for c in wanted if c in cols]
    if not use:
        return []
    quoted = ", ".join(f'"{c}"' for c in use)
    return conn.execute(f"SELECT {quoted} FROM {table}").fetchall()


def _date_col(cols):
    return _pick(cols, "month_update", "update", "report_date", "periode",
                 "tanggal", "date", "tahun")


# ─── section builders ─────────────────────────────────────────────────────────
def _plo(conn, as_of):
    national = {"active": 0, "exp_90": 0, "exp_30": 0, "expired": 0}
    per_ru = {c: {"expired": 0, "exp_90": 0, "exp_30": 0} for c in RU_CODES}
    items = []
    sources = [
        ("atg_monitoring", "refinery_unit", "date_expired_atg", "ATG certification", "ATG"),
        ("metering_monitoring", "refinery_unit", "date_expired_metering", "Metering certification", "Metering"),
        ("readiness_jetty", "refinery_unit", "expired_tuks", "Jetty TUKS permit", "Jetty"),
        ("readiness_spm", "refinery_unit", "expired_laik_operasi", "SPM laik-operasi", "SPM"),
    ]
    for table, rucol, dcol, title, src in sources:
        if not _table_exists(conn, table):
            continue
        for r in _fetch(conn, table, [rucol, dcol]):
            national["active"] += 1
            code = match_ru(r.get(rucol))
            d = _parse_date(r.get(dcol))
            if d is None:
                continue
            delta = (d - as_of).days
            bucket = None
            if delta < 0:
                bucket = "expired"
            elif delta <= 30:
                bucket = "exp_30"
            elif delta <= 90:
                bucket = "exp_90"
            if bucket:
                national[bucket] += 1
                if bucket == "exp_30":
                    national["exp_90"] += 1  # 30d also counts within 90d
                if code:
                    per_ru[code][bucket] += 1
                sev = "critical" if bucket == "expired" else "watch"
                items.append({
                    "ru": RU_DISPLAY.get(code, code or "—"), "severity": sev,
                    "title": f"{title} {'expired' if bucket=='expired' else 'expiring'}",
                    "impact": "certification / compliance risk",
                    "action": "expedite recertification",
                    "aging": f"Aging {-delta}d" if delta < 0 else f"Due in {delta}d",
                    "owner": "HSSE / Operations", "source": src, "_sort": delta})
    items.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, x["_sort"]))
    return national, per_ru, items[:5]


def _rkap(conn, as_of):
    if not _table_exists(conn, "irkap_program"):
        return None, {}, []
    rows = _fetch(conn, "irkap_program",
                  ["refinery_unit", "status_prognosa", "finish_plan", "program_kerja"])
    if not rows:
        return None, {}, []
    done_words = ("DONE", "SELESAI", "COMPLETE", "COMPLETED", "FINISH", "CLOSED", "100")
    total = completed = overdue = 0
    per = {}
    items = []
    for r in rows:
        st = str(r.get("status_prognosa") or "").upper()
        is_done = any(w in st for w in done_words)
        fp = _parse_date(r.get("finish_plan"))
        is_overdue = (fp is not None and fp < as_of and not is_done)
        total += 1
        completed += 1 if is_done else 0
        overdue += 1 if is_overdue else 0
        code = match_ru(r.get("refinery_unit"))
        if code:
            a = per.setdefault(code, [0, 0])
            a[0] += 1
            a[1] += 1 if is_done else 0
        if is_overdue and len(items) < 20:
            items.append({
                "ru": RU_DISPLAY.get(code, code or "—"), "severity": "watch",
                "title": (str(r.get("program_kerja") or "Program") + " overdue")[:60],
                "impact": "RKAP schedule slip", "action": "recover package",
                "aging": f"Due {fp.isoformat()}", "owner": "MPS", "source": "IRKAP"})
    if total == 0:
        return None, {}, []
    kpi = {"value": round(completed * 100 / total), "completed": completed,
           "total": total, "overdue": overdue}
    prog = {c: round(a[1] * 100 / a[0]) for c, a in per.items() if a[0]}
    return kpi, prog, items[:5]


def _introspect_metric(conn, table, target_needles, actual_needles):
    if not _table_exists(conn, table):
        return None
    cols = _columns(conn, table)
    rucol = _ru_col(cols)
    tcol = _pick(cols, *target_needles)
    acol = _pick(cols, *actual_needles)
    if not (rucol and acol):
        return None
    rows = _fetch(conn, table, [rucol, tcol, acol] if tcol else [rucol, acol])
    agg = {}
    for r in rows:
        code = match_ru(r.get(rucol))
        if not code:
            continue
        a = _parse_num(r.get(acol))
        t = _parse_num(r.get(tcol)) if tcol else None
        if a is None:
            continue
        g = agg.setdefault(code, {"a": [], "t": []})
        g["a"].append(a)
        if t is not None:
            g["t"].append(t)
    out = {}
    for code, g in agg.items():
        out[code] = (sum(g["t"]) / len(g["t"]) if g["t"] else None,
                     sum(g["a"]) / len(g["a"]) if g["a"] else None)
    return out or None


def _capacity(conn):
    if not _table_exists(conn, "monitoring_operasi"):
        return None
    cols = _columns(conn, "monitoring_operasi")
    rucol = _ru_col(cols)
    design = _pick(cols, "design", "desain", "kapasitas_design", "nameplate")
    actual = _pick(cols, "actual", "aktual", "operasi", "current", "realisasi", "feed")
    if not (rucol and actual):
        return None
    rows = _fetch(conn, "monitoring_operasi",
                  [rucol, design, actual] if design else [rucol, actual])
    agg = {}
    for r in rows:
        code = match_ru(r.get(rucol))
        a = _parse_num(r.get(actual))
        if not code or a is None:
            continue
        g = agg.setdefault(code, {"a": [], "d": []})
        g["a"].append(a)
        d = _parse_num(r.get(design)) if design else None
        if d:
            g["d"].append(d)
    out = {}
    for code, g in agg.items():
        av = sum(g["a"]) / len(g["a"])
        dv = sum(g["d"]) / len(g["d"]) if g["d"] else None
        out[code] = {"current": round(av), "design": round(dv) if dv else None,
                     "util": round(av / dv * 100) if dv else None}
    return out or None


def _oa(conn):
    """OA if monitoring_operasi has an explicit availability column."""
    if not _table_exists(conn, "monitoring_operasi"):
        return None
    cols = _columns(conn, "monitoring_operasi")
    rucol = _ru_col(cols)
    oacol = _pick(cols, "oa", "availability", "ketersediaan", "avail")
    if not (rucol and oacol):
        return None
    rows = _fetch(conn, "monitoring_operasi", [rucol, oacol])
    agg = {}
    for r in rows:
        code = match_ru(r.get(rucol))
        v = _parse_num(r.get(oacol))
        if code and v is not None:
            agg.setdefault(code, []).append(v)
    return {c: round(sum(v) / len(v), 1) for c, v in agg.items()} or None


def _spend(conn):
    if not _table_exists(conn, "anggaran_maintenance"):
        return None
    cols = _columns(conn, "anggaran_maintenance")
    rucol = _ru_col(cols)
    plan = _pick(cols, "plan", "rkap", "anggaran", "budget")
    actual = _pick(cols, "aktual", "actual", "realisasi", "realization", "spend")
    if not (plan and actual):
        return None
    rows = _fetch(conn, "anggaran_maintenance", [rucol, plan, actual] if rucol else [plan, actual])
    nat_a = nat_p = 0.0
    per = {}
    for r in rows:
        a = _parse_num(r.get(actual)) or 0
        p = _parse_num(r.get(plan)) or 0
        nat_a += a
        nat_p += p
        code = match_ru(r.get(rucol)) if rucol else None
        if code:
            g = per.setdefault(code, [0.0, 0.0])
            g[0] += a
            g[1] += p
    if nat_p == 0:
        return None
    return {"actual": nat_a, "plan": nat_p}, per


def _spend_interp(pct):
    if pct is None:
        return "On Plan", "healthy"
    if pct < 80:
        return "Under-spend Risk", "watch"
    if pct > 105:
        return "Over-spend watch", "watch"
    if pct > 100:
        return "Execution Risk", "critical"
    return "On Plan", "healthy"


def _reliability(conn, as_of):
    """Per-RU alert counts + reliability hotspot issue items."""
    counts = {c: 0 for c in RU_CODES}
    items = []

    def add(code, sev, title, impact, action, aging, owner, src):
        items.append({"ru": RU_DISPLAY.get(code, code or "—"), "severity": sev,
                      "title": title[:70], "impact": impact, "action": action,
                      "aging": aging, "owner": owner, "source": src})

    # ICU high severity
    if _table_exists(conn, "icu_monitoring"):
        icu_by_ru = {}
        for r in _fetch(conn, "icu_monitoring", ["ru", "icu_status", "issue"]):
            if str(r.get("icu_status") or "").upper() in ("HIGH", "CRITICAL"):
                code = match_ru(r.get("ru"))
                if code:
                    counts[code] += 1
                    icu_by_ru[code] = icu_by_ru.get(code, 0) + 1
        for code, n in sorted(icu_by_ru.items(), key=lambda x: -x[1])[:3]:
            add(code, "critical", f"{n} ICU high-severity item(s)",
                "PAF suppression risk", "accelerate mitigation", f"{n} open",
                "Reliability", "ICU")
    # Bad actor active
    if _table_exists(conn, "bad_actor_monitoring"):
        for r in _fetch(conn, "bad_actor_monitoring", ["ru", "status", "problem"]):
            if str(r.get("status") or "").upper() not in ("CLOSED", "SELESAI", "CLOSE", "DONE", ""):
                code = match_ru(r.get("ru"))
                if code:
                    counts[code] += 1
    # Zero clamp active
    if _table_exists(conn, "zero_clamp"):
        for r in _fetch(conn, "zero_clamp", ["ru", "status"]):
            if str(r.get("status") or "").upper() not in ("CLOSED", "SELESAI", "LEPAS", "DONE", ""):
                code = match_ru(r.get("ru"))
                if code:
                    counts[code] += 1
    # Power & steam not-normal
    if _table_exists(conn, "power_stream"):
        ps = {}
        for r in _fetch(conn, "power_stream", ["refinery_unit", "status_operation", "status_n0"]):
            so = str(r.get("status_operation") or "").upper()
            if so and so not in ("NORMAL", "OK", "RUN", "RUNNING", "AVAILABLE"):
                code = match_ru(r.get("refinery_unit"))
                if code:
                    counts[code] += 1
                    ps[code] = ps.get(code, 0) + 1
        for code, n in sorted(ps.items(), key=lambda x: -x[1])[:2]:
            add(code, "critical", "Power & Steam readiness issue",
                "unit stability threat", "secure utility support", f"{n} eqp",
                "Utility", "Power & Steam")
    items.sort(key=lambda x: 0 if x["severity"] == "critical" else 1)
    return counts, items[:5]


def _program_items(conn, as_of):
    """Program-execution watchlist from workplan_* + inspection_plan."""
    items = []
    for table, rucol, src in (("workplan_jetty", "refinery_unit", "Workplan Jetty"),
                              ("workplan_tank", "refinery_unit", "Workplan Tank"),
                              ("spm_workplan", "refinery_unit", "Workplan SPM")):
        if not _table_exists(conn, table):
            continue
        for r in _fetch(conn, table, [rucol, "item", "status_rtl", "target"]):
            st = str(r.get("status_rtl") or "").upper()
            if st in ("DONE", "CLOSED", "SELESAI", "COMPLETE"):
                continue
            code = match_ru(r.get(rucol))
            tgt = _parse_date(r.get("target"))
            items.append({"ru": RU_DISPLAY.get(code, code or "—"),
                          "severity": "critical" if (tgt and tgt < as_of) else "watch",
                          "title": (str(r.get("item") or "Workplan item") + " delayed")[:60],
                          "impact": "availability / permit risk",
                          "action": "escalate execution",
                          "aging": (f"Due {tgt.isoformat()}" if tgt else "open"),
                          "owner": "Project", "source": src})
    if _table_exists(conn, "inspection_plan"):
        for r in _fetch(conn, "inspection_plan",
                        ["refinery_unit", "due_date", "actual_date", "type_inspection"]):
            due = _parse_date(r.get("due_date"))
            act = _parse_date(r.get("actual_date"))
            if due and due < as_of and act is None:
                code = match_ru(r.get("refinery_unit"))
                items.append({"ru": RU_DISPLAY.get(code, code or "—"), "severity": "watch",
                              "title": (str(r.get("type_inspection") or "Inspection") + " overdue")[:60],
                              "impact": "integrity exposure", "action": "recover inspection",
                              "aging": f"Due {due.isoformat()}", "owner": "Inspection",
                              "source": "Inspection Plan"})
    items.sort(key=lambda x: 0 if x["severity"] == "critical" else 1)
    return items[:5]


def _readiness_cards(conn):
    """Live counts for the readiness cards where columns are known."""
    cards = {c["title"]: dict(c) for c in executive_mock._readiness()}

    def setcard(title, primary, detail, status):
        if title in cards:
            cards[title].update({"primary": primary, "detail": detail, "status": status})

    if _table_exists(conn, "bad_actor_monitoring"):
        rows = _fetch(conn, "bad_actor_monitoring", ["status"])
        active = sum(1 for r in rows if str(r.get("status") or "").upper()
                     not in ("CLOSED", "SELESAI", "CLOSE", "DONE", ""))
        setcard("Bad Actor", f"{active} active", f"of {len(rows)} total",
                "critical" if active > 8 else "watch" if active else "healthy")
    if _table_exists(conn, "icu_monitoring"):
        rows = _fetch(conn, "icu_monitoring", ["icu_status"])
        high = sum(1 for r in rows if str(r.get("icu_status") or "").upper() in ("HIGH", "CRITICAL"))
        setcard("ICU", f"{high} high severity", f"of {len(rows)} items",
                "critical" if high else "healthy")
    if _table_exists(conn, "zero_clamp"):
        rows = _fetch(conn, "zero_clamp", ["status"])
        active = sum(1 for r in rows if str(r.get("status") or "").upper()
                     not in ("CLOSED", "SELESAI", "LEPAS", "DONE", ""))
        setcard("Zero Clamp", f"{active} active clamps", f"of {len(rows)} total",
                "watch" if active else "healthy")
    if _table_exists(conn, "boc"):
        rows = _fetch(conn, "boc", ["mtbf"])
        setcard("BOC MTBF/MTTR", f"{len(rows)} equipment", "monitored", "healthy")
    if _table_exists(conn, "inspection_plan"):
        rows = _fetch(conn, "inspection_plan", ["due_date", "actual_date"])
        today = _dt.date.today()
        overdue = sum(1 for r in rows if (_parse_date(r.get("due_date"))
                      and _parse_date(r.get("due_date")) < today
                      and _parse_date(r.get("actual_date")) is None))
        setcard("Inspection Risk", f"{overdue} overdue", f"of {len(rows)} planned",
                "critical" if overdue > 5 else "watch" if overdue else "healthy")
    return list(cards.values())


def _data_freshness(conn, as_of):
    specs = [
        ("PAF", "paf"), ("Monitoring Operasi", "monitoring_operasi"),
        ("Anggaran Maintenance", "anggaran_maintenance"),
        ("ATG Monitoring", "atg_monitoring"), ("Metering", "metering_monitoring"),
        ("Bad Actor", "bad_actor_monitoring"), ("ICU", "icu_monitoring"),
        ("IRKAP Program", "irkap_program"),
    ]
    out = []
    for label, table in specs:
        if not _table_exists(conn, table):
            continue
        try:
            cols = _columns(conn, table)
            n = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            dcol = _date_col(cols)
            latest = "—"
            if dcol and n:
                vals = [_parse_date(r[dcol]) for r in _fetch(conn, table, [dcol])]
                vals = [v for v in vals if v]
                if vals:
                    latest = max(vals).isoformat()
            out.append({"dataset": label, "updated": latest, "records": str(n),
                        "completeness": 100 if n else 0,
                        "status": "healthy" if n else "nodata"})
        except Exception:
            continue
    return out


def _interpretation(snap):
    """Evidence bullets generated from the live numbers."""
    notes = []
    reds = [r for r in snap["refineries"] if r["status"] == "critical"]
    if reds:
        names = ", ".join(RU_DISPLAY.get(r["code"], r["code"]) for r in reds[:3])
        notes.append(f"{names} require director attention (critical overall status).")
    plo = snap["kpis"]["plo"]
    if plo.get("expired"):
        notes.append(f"{plo['expired']} certification item(s) already expired and "
                     f"{plo.get('exp_30',0)} expiring within 30 days — regulatory priority.")
    rkap = snap["kpis"]["rkap"]
    if rkap.get("overdue"):
        notes.append(f"{rkap['overdue']} critical program(s) overdue; RKAP realization "
                     f"at {rkap['value']}%.")
    worst_paf = min(snap["refineries"], key=lambda r: r.get("paf", 100))
    if worst_paf.get("paf", 100) < 90:
        notes.append(f"{RU_DISPLAY.get(worst_paf['code'])} has the lowest PAF "
                     f"({worst_paf['paf']}%), constraining national availability.")
    return notes or executive_mock._interpretation()


# ─── main assembly ────────────────────────────────────────────────────────────
def get_executive_snapshot(period: str = "") -> dict:
    snap = executive_mock.get_executive_snapshot()
    label, as_of, _start = period_asof(period)
    if label:
        snap["meta"]["period"] = label

    if _get_conn is None:
        snap["meta"]["mode"] = "mock"
        snap["meta"]["mock_label"] = executive_mock.MOCK_LABEL
        return snap
    try:
        conn = _get_conn()
    except Exception:
        snap["meta"]["mode"] = "mock"
        snap["meta"]["mock_label"] = "Mockup data (database unavailable) — check DATABASE_URL."
        return snap

    live = []
    try:
        with conn:
            ru_by = {r["code"]: r for r in snap["refineries"]}

            _try(live, "plo", lambda: _apply_plo(conn, as_of, snap, ru_by))
            _try(live, "rkap", lambda: _apply_rkap(conn, as_of, snap, ru_by))
            _try(live, "reliability", lambda: _apply_reliability(conn, as_of, snap, ru_by))
            _try(live, "program", lambda: _apply_program(conn, as_of, snap))
            _try(live, "paf", lambda: _apply_paf(conn, snap, ru_by))
            _try(live, "oa", lambda: _apply_oa(conn, snap, ru_by))
            _try(live, "capacity", lambda: _apply_capacity(conn, snap, ru_by))
            _try(live, "spend", lambda: _apply_spend(conn, snap, ru_by))
            _try(live, "readiness", lambda: snap.__setitem__("readiness", _readiness_cards(conn)))
            _try(live, "data_freshness", lambda: _apply_fresh(conn, as_of, snap))

            for ru in snap["refineries"]:
                _recompute_status(ru)
            snap["interpretation"] = _interpretation(snap)
    except Exception as e:
        snap["meta"]["mode"] = "mock"
        snap["meta"]["mock_label"] = f"Mockup data (query error) — {e!r}"
        return snap

    if not live:
        snap["meta"]["mode"] = "mock"
        snap["meta"]["mock_label"] = executive_mock.MOCK_LABEL
    elif len(live) >= 8:
        snap["meta"]["mode"] = "live"
        snap["meta"]["mock_label"] = f"Live snapshot from database · {label or 'latest'}"
        snap["meta"]["live_sections"] = live
    else:
        snap["meta"]["mode"] = "partial"
        snap["meta"]["mock_label"] = "Partial live data — some sections still use mock values."
        snap["meta"]["live_sections"] = live
    return snap


def _try(live, name, fn):
    try:
        res = fn()
        if res is not False:
            live.append(name)
    except Exception:
        pass


def _apply_plo(conn, as_of, snap, ru_by):
    national, per_ru, items = _plo(conn, as_of)
    if national["active"] == 0:
        return False
    snap["kpis"]["plo"].update({
        "active": national["active"], "exp_90": national["exp_90"],
        "exp_30": national["exp_30"], "expired": national["expired"],
        "status": "critical" if national["expired"] else "watch" if national["exp_30"] else "healthy"})
    for code, v in per_ru.items():
        ru = ru_by.get(code)
        if ru:
            st = "critical" if v["expired"] else "watch" if v["exp_90"] else "healthy"
            ru["plo_status"] = st
            ru["plo"] = {"healthy": "Safe", "watch": "Watch", "critical": "Critical"}[st]
    if items:
        snap["issues"]["regulatory"] = items
        snap["alerts"]["regulatory"] = items


def _apply_rkap(conn, as_of, snap, ru_by):
    kpi, prog, items = _rkap(conn, as_of)
    if not kpi:
        return False
    snap["kpis"]["rkap"].update({
        "value": kpi["value"], "completed": kpi["completed"], "total": kpi["total"],
        "overdue": kpi["overdue"],
        "status": "critical" if kpi["overdue"] > 5 else "watch" if kpi["value"] < 85 else "healthy"})
    for code, pct in prog.items():
        if ru_by.get(code):
            ru_by[code]["program"] = pct


def _apply_program(conn, as_of, snap):
    items = _program_items(conn, as_of)
    if not items:
        return False
    snap["issues"]["program"] = items
    snap["alerts"]["program"] = items


def _apply_reliability(conn, as_of, snap, ru_by):
    counts, items = _reliability(conn, as_of)
    for code in RU_CODES:
        if ru_by.get(code):
            ru_by[code]["alerts"] = counts[code]
    if items:
        snap["issues"]["reliability"] = items
        snap["alerts"]["operational"] = items


def _apply_paf(conn, snap, ru_by):
    paf = _introspect_metric(conn, "paf", ("target", "rencana"),
                             ("real", "aktual", "actual", "capai", "paf"))
    if not paf:
        return False
    vals = [a for _, a in paf.values() if a is not None]
    tgts = [t for t, _ in paf.values() if t is not None]
    if not vals:
        return False
    nat = round(sum(vals) / len(vals), 1)
    snap["kpis"]["paf"]["value"] = nat
    snap["kpis"]["paf"]["realization"] = nat
    if tgts:
        snap["kpis"]["paf"]["target"] = round(sum(tgts) / len(tgts), 1)
    snap["kpis"]["paf"]["status"] = "critical" if nat < 88 else "watch" if nat < 94 else "healthy"
    for code, (t, a) in paf.items():
        if ru_by.get(code) and a is not None:
            ru_by[code]["paf"] = round(a)


def _apply_oa(conn, snap, ru_by):
    oa = _oa(conn)
    if not oa:
        return False
    vals = list(oa.values())
    nat = round(sum(vals) / len(vals), 1)
    snap["kpis"]["oa"]["value"] = nat
    snap["kpis"]["oa"]["actual"] = nat
    snap["kpis"]["oa"]["status"] = "critical" if nat < 90 else "watch" if nat < 95 else "healthy"
    for code, v in oa.items():
        if ru_by.get(code):
            ru_by[code]["oa"] = round(v)


def _apply_capacity(conn, snap, ru_by):
    cap = _capacity(conn)
    if not cap:
        return False
    for code, v in cap.items():
        ru = ru_by.get(code)
        if ru:
            if v["current"] is not None:
                ru["current"] = v["current"]
            if v["design"]:
                ru["design"] = v["design"]
            if v["util"] is not None:
                ru["util"] = v["util"]


def _apply_spend(conn, snap, ru_by):
    sp = _spend(conn)
    if not sp:
        return False
    national, per = sp
    pct = round(national["actual"] / national["plan"] * 100) if national["plan"] else None
    interp, st = _spend_interp(pct)
    snap["kpis"]["spend"].update({
        "actual_t": round(national["actual"] / 1e12, 2),
        "plan_t": round(national["plan"] / 1e12, 2),
        "pct_of_plan": pct if pct is not None else snap["kpis"]["spend"]["pct_of_plan"],
        "interpretation": interp, "status": st})
    for code, g in per.items():
        ru = ru_by.get(code)
        if ru and g[1]:
            i2, s2 = _spend_interp(round(g[0] / g[1] * 100))
            ru["spend"], ru["spend_status"] = i2, s2


def _apply_fresh(conn, as_of, snap):
    fresh = _data_freshness(conn, as_of)
    if not fresh:
        return False
    snap["data_freshness"] = fresh


def _recompute_status(ru):
    score = 0
    for key, w, c in (("plo_status", "watch", "critical"), ("spend_status", "watch", "critical")):
        if ru.get(key) == "critical":
            score = max(score, 2)
        elif ru.get(key) == "watch":
            score = max(score, 1)
    if ru.get("paf", 100) < 88 or ru.get("program", 100) < 65:
        score = max(score, 2)
    elif ru.get("paf", 100) < 94 or ru.get("program", 100) < 80:
        score = max(score, 1)
    if ru.get("alerts", 0) >= 4:
        score = max(score, 2)
    elif ru.get("alerts", 0) >= 2:
        score = max(score, 1)
    ru["status"] = ["healthy", "watch", "critical"][score]


# ─── methodology (transparency) ───────────────────────────────────────────────
# Each entry documents a metric: its source table(s), the columns used, the
# formula/aggregation, the status thresholds, and a note. For the introspected
# metrics the actually-detected column is filled in live (or marked ❓).
def _methodology_entries():
    return [
        {"metric": "Operational Availability (OA)",
         "source": "monitoring_operasi",
         "detect": ("monitoring_operasi", ("oa", "availability", "ketersediaan", "avail")),
         "columns": "kolom OA (mengandung 'oa'/'availability')",
         "formula": "Rata-rata OA per RU, lalu nasional = rata-rata antar RU.",
         "thresholds": "≥95 Healthy · 90–94 Watch · <90 Critical",
         "note": "OA tidak punya tabel sendiri — diambil dari kolom availability di monitoring_operasi. Jika kolom tak ada → tetap mock."},
        {"metric": "Plant Availability Factor (PAF)",
         "source": "paf",
         "detect2": ("paf", ("target", "rencana"), ("real", "aktual", "actual", "capai", "paf")),
         "columns": "target + realisasi",
         "formula": "Rata-rata realisasi per RU; nasional = rata-rata antar RU.",
         "thresholds": "≥94 Healthy · 88–93 Watch · <88 Critical",
         "note": "Angka teks (mis. '94,5') diparse otomatis."},
        {"metric": "RKAP / Program Realization",
         "source": "irkap_program",
         "columns": "refinery_unit, status_prognosa, finish_plan, program_kerja",
         "formula": "Realisasi% = selesai / total × 100 (selesai = status mengandung DONE/SELESAI/COMPLETE/100). Overdue = finish_plan < tanggal-acuan & belum selesai. Program% per RU = selesai/total per RU.",
         "thresholds": "overdue>5 Critical · realisasi<85% Watch · selain itu Healthy",
         "note": "Tanggal finish_plan dibanding tanggal-acuan (dari periode)."},
        {"metric": "Maintenance Spend",
         "source": "anggaran_maintenance",
         "detect2": ("anggaran_maintenance", ("plan", "rkap", "anggaran", "budget"),
                     ("aktual", "actual", "realisasi", "spend")),
         "columns": "plan + aktual",
         "formula": "Nasional = Σ aktual / Σ plan. %= aktual/plan × 100 (per RU untuk interpretasi).",
         "thresholds": "<80% Under-spend · 100–105% Execution Risk · >105% Over-spend · selain itu On Plan",
         "note": "Format ribuan titik (1.000.000.000) & desimal koma diparse otomatis."},
        {"metric": "PLO / Certification Readiness",
         "source": "atg_monitoring · metering_monitoring · readiness_jetty · readiness_spm",
         "columns": "date_expired_atg / date_expired_metering / expired_tuks / expired_laik_operasi + refinery_unit",
         "formula": "Active = jumlah baris. Bucket vs tanggal-acuan: Expired (tgl<acuan), ≤30 hari (exp_30), ≤90 hari (exp_90).",
         "thresholds": "ada Expired → Critical · ada ≤30 hari → Watch · selain itu Healthy",
         "note": "Semua item sertifikasi diagregasi dari 4 tabel ini (tidak ada 'PLO master')."},
        {"metric": "Capacity / Utilization",
         "source": "monitoring_operasi",
         "detect2": ("monitoring_operasi", ("design", "desain", "nameplate"),
                     ("actual", "aktual", "operasi", "feed", "current", "realisasi")),
         "columns": "design_capacity + actual_capacity",
         "formula": "Utilisasi% = actual / design × 100 per RU.",
         "thresholds": "—",
         "note": "Ditampilkan di peta & scorecard (current / design MBSD)."},
        {"metric": "Alerts per RU (Reliability Hotspots)",
         "source": "icu_monitoring · bad_actor_monitoring · zero_clamp · power_stream",
         "columns": "icu_status, status, status_operation, ru/refinery_unit",
         "formula": "Jumlah item aktif per RU: ICU HIGH/CRITICAL + Bad Actor status≠closed + Zero Clamp status≠lepas/closed + Power&Steam status_operation≠normal.",
         "thresholds": "≥4 Critical · 2–3 Watch · <2 Healthy (mempengaruhi status keseluruhan RU)",
         "note": "Item teratas ditampilkan sebagai kartu isu Reliability Hotspots."},
        {"metric": "Regulatory Watchlist",
         "source": "atg_monitoring · metering_monitoring · readiness_jetty/spm",
         "columns": "date_expired_* + refinery_unit",
         "formula": "Item sertifikasi yang Expired (severity Critical) atau expiring ≤90 hari (Watch), diurut severity lalu aging. Top 5.",
         "thresholds": "Expired → Critical · Expiring → Watch",
         "note": "Aging/Due dihitung relatif ke tanggal-acuan periode."},
        {"metric": "Program Execution Watchlist",
         "source": "workplan_jetty · workplan_tank · spm_workplan · inspection_plan",
         "columns": "status_rtl, target, due_date, actual_date, refinery_unit",
         "formula": "Workplan status_rtl≠done (target<acuan → Critical) + Inspection due_date<acuan & actual kosong (overdue). Top 5.",
         "thresholds": "target/due terlewat → Critical · selain itu Watch",
         "note": "—"},
        {"metric": "Reliability Readiness cards",
         "source": "bad_actor_monitoring · icu_monitoring · zero_clamp · boc · inspection_plan",
         "columns": "status, icu_status, mtbf, due_date/actual_date",
         "formula": "Hitung: Bad Actor aktif, ICU high-severity, Zero Clamp aktif, jumlah BOC, Inspeksi overdue.",
         "thresholds": "per kartu (mis. ICU: ada high → Critical)",
         "note": "Kartu tanpa kolom yang dikenal tetap memakai nilai contoh."},
        {"metric": "Data Freshness",
         "source": "semua tabel sumber",
         "columns": "COUNT(*) + kolom tanggal (month_update/periode/date)",
         "formula": "Jumlah record + tanggal update terbaru per tabel.",
         "thresholds": "ada data → Current · kosong → No data",
         "note": "Menunjukkan kesegaran & kelengkapan sumber."},
        {"metric": "Overall RU status",
         "source": "turunan",
         "columns": "plo_status, spend_status, paf, program, alerts",
         "formula": "Skor = maksimum dari sinyal (PLO/Spend critical=2/watch=1; PAF<88 atau Program<65 =2, <94/<80 =1; Alerts≥4=2, ≥2=1). 2=Critical, 1=Watch, 0=Healthy.",
         "thresholds": "0 Healthy · 1 Watch · 2 Critical",
         "note": "Menentukan warna marker peta & badge scorecard."},
        {"metric": "Periode (tanggal-acuan)",
         "source": "selector periode",
         "columns": "—",
         "formula": "Bulan (mis. 'Jun 2026') → akhir bulan sebagai tanggal-acuan. 'YTD 2026' → hari ini / akhir tahun. Dipakai untuk bucket expiry & overdue.",
         "thresholds": "—",
         "note": "Mengubah periode mengubah PLO/overdue/severity."},
    ]


def _detect_column(conn, table, needles):
    if not _table_exists(conn, table):
        return "❓ tabel tidak ada"
    col = _pick(_columns(conn, table), *needles)
    return col or "❓ tidak terdeteksi"


def methodology(period: str = "") -> dict:
    label, as_of, _ = period_asof(period)
    entries = _methodology_entries()
    detected = {}
    if _get_conn is not None:
        try:
            with _get_conn() as conn:
                for e in entries:
                    if "detect" in e:
                        t, needles = e["detect"]
                        e["columns"] = f"{_detect_column(conn, t, needles)} (terdeteksi)"
                    elif "detect2" in e:
                        t, tn, an = e["detect2"]
                        tc = _detect_column(conn, t, tn)
                        ac = _detect_column(conn, t, an)
                        e["columns"] = f"target={tc}, aktual={ac} (terdeteksi)"
                    detected["_db"] = "connected"
        except Exception as ex:
            detected["_db"] = f"tidak konek: {ex!r}"
    else:
        detected["_db"] = "db_equipment tidak ter-import"
    for e in entries:
        e.pop("detect", None)
        e.pop("detect2", None)
    return {"as_of": as_of.isoformat(), "period": label or "latest",
            "db": detected.get("_db", "unknown"), "entries": entries}


# ─── diagnostics ──────────────────────────────────────────────────────────────
def diagnostics(period: str = "") -> dict:
    out = {"db": "unknown", "period": period, "tables": {}, "sections": {}}
    if _get_conn is None:
        out["db"] = "db_equipment import failed (psycopg/dotenv missing?)"
        return out
    try:
        conn = _get_conn()
    except Exception as e:
        out["db"] = f"connect failed: {e!r}"
        return out
    out["db"] = "connected"
    _, as_of, _s = period_asof(period)
    out["as_of"] = as_of.isoformat()
    probe = ["paf", "monitoring_operasi", "anggaran_maintenance", "atg_monitoring",
             "metering_monitoring", "readiness_jetty", "readiness_spm",
             "irkap_program", "bad_actor_monitoring", "icu_monitoring", "zero_clamp",
             "power_stream", "inspection_plan", "workplan_jetty"]
    try:
        with conn:
            for t in probe:
                info = {}
                try:
                    if not _table_exists(conn, t):
                        out["tables"][t] = {"exists": False}
                        continue
                    cols = _columns(conn, t)
                    info["columns"] = cols
                    info["rows"] = conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                    rc = _ru_col(cols)
                    info["ru_col"] = rc
                    if rc and info["rows"]:
                        vals = conn.execute(
                            f'SELECT DISTINCT "{rc}" v FROM {t} WHERE "{rc}" IS NOT NULL LIMIT 15').fetchall()
                        info["ru_samples"] = {str(r["v"]): match_ru(r["v"]) for r in vals}
                except Exception as e:
                    info["error"] = repr(e)
                out["tables"][t] = info

            def sim(name, fn):
                try:
                    out["sections"][name] = {"ok": True, "result": str(fn())[:200]}
                except Exception as e:
                    out["sections"][name] = {"ok": False, "error": repr(e)}
            sim("plo", lambda: _plo(conn, as_of)[0])
            sim("rkap", lambda: _rkap(conn, as_of)[0])
            sim("paf", lambda: _introspect_metric(conn, "paf", ("target", "rencana"),
                                                  ("real", "aktual", "actual", "capai", "paf")))
            sim("capacity", lambda: _capacity(conn))
            sim("spend", lambda: _spend(conn))
            sim("oa", lambda: _oa(conn))
            sim("reliability", lambda: _reliability(conn, as_of)[0])
    except Exception as e:
        out["db"] = f"query phase failed: {e!r}"
    return out
