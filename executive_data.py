"""
executive_data.py
──────────────────────────────────────────────────────────────────────────────
LIVE data layer for /dashboard/executive.

Builds the executive snapshot from the real PostgreSQL reliability tables
(same DB/connection as db_equipment.py). It is intentionally *defensive*:

  • Sections backed by tables whose columns are known from existing working
    queries (bad_actor_monitoring, icu_monitoring, atg/metering_monitoring,
    zero_clamp, power_stream, boc, pipeline_inspection, inspection_plan,
    readiness_*, workplan_*, irkap_*, critical_eqp_prim_sec) are computed with
    real SQL.
  • Sections whose source tables have no existing query in the codebase
    (paf, monitoring_operasi, anggaran_maintenance, rcps_rekomendasi, ...) are
    resolved via runtime schema introspection with conservative name
    heuristics; if the expected columns are not found the value FALLS BACK to
    the mock skeleton (executive_mock) and is flagged as mock.
  • ANY failure (no DATABASE_URL, missing table/column, cast error) falls back
    per-section to the mock value. The whole thing is wrapped so the route
    always renders.

The public entrypoint mirrors executive_mock: `get_executive_snapshot()`.
Swap-in is a one-line change in main.py.

Verification note: this build environment has no DB, so only the fallback
(mock) path is exercised here. Live values appear when run where
DATABASE_URL points at the real database. The RU-level KPI tables
(paf / monitoring_operasi / anggaran_maintenance) use column heuristics that
should be confirmed against the real schema.
"""
from __future__ import annotations

import datetime as _dt

import executive_mock

try:
    import db_equipment as _dbe
    _get_conn = _dbe.get_conn
except Exception:  # pragma: no cover - import-time safety
    _dbe = None
    _get_conn = None


# ─── RU normalization ─────────────────────────────────────────────────────────
# Map any raw RU / refinery_unit string coming from the DB onto our 7 codes.
RU_CODES = ["RU2", "RU3", "RU4", "RU5", "RU6", "RU7", "TPPI"]

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
    """Best-effort map a raw RU string to one of our codes. Order matters so
    that longer roman numerals (VII/VI/IV/III/II) win before shorter ones."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    # city names first (unambiguous)
    for code, kws in _RU_KEYWORDS:
        for kw in kws:
            if kw.isalpha() and len(kw) > 3 and kw in s:
                return code
    # then roman / ru-number tokens (padded so 'ii' doesn't match inside 'iii')
    padded = f" {s.replace('-', ' ')} "
    for code, kws in _RU_KEYWORDS:
        for kw in kws:
            if f" {kw} " in padded:
                return code
    return None


# ─── introspection helpers ────────────────────────────────────────────────────
def _columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name=%s
           ORDER BY ordinal_position""",
        (table,),
    ).fetchall()
    return [r["column_name"] for r in rows]


def _pick(cols: list[str], *needles: str) -> str | None:
    """First column whose name contains any needle (case-insensitive)."""
    low = {c.lower(): c for c in cols}
    for needle in needles:
        for lc, orig in low.items():
            if needle in lc:
                return orig
    return None


def _ru_col(cols: list[str]) -> str | None:
    for c in cols:
        if c.lower() in ("refinery_unit", "ru", "refinery", "unit_kerja", "kilang"):
            return c
    return _pick(cols, "refinery", "ru")


def _table_exists(conn, table: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s LIMIT 1",
        (table,),
    ).fetchone()
    return bool(r)


# ─── per-section builders (each returns a dict keyed by RU code) ───────────────
def _blank_ru_map():
    return {code: 0 for code in RU_CODES}


def _count_by_ru(conn, table, ru_col, where_sql="", params=()):
    """GROUP BY ru → count, mapped to our RU codes."""
    out = _blank_ru_map()
    sql = f'SELECT "{ru_col}" AS ru, COUNT(*) AS n FROM {table} '
    if where_sql:
        sql += "WHERE " + where_sql + " "
    sql += f'GROUP BY "{ru_col}"'
    for row in conn.execute(sql, params).fetchall():
        code = match_ru(row["ru"])
        if code:
            out[code] += int(row["n"] or 0)
    return out


def _reliability_counts(conn):
    """Active bad-actor + high ICU + active zero-clamp per RU (all known cols)."""
    bad = _blank_ru_map(); icu = _blank_ru_map(); zc = _blank_ru_map()
    try:
        bad = _count_by_ru(
            conn, "bad_actor_monitoring", "ru",
            "COALESCE(UPPER(status),'') NOT IN ('CLOSED','SELESAI','CLOSE','DONE')")
    except Exception:
        pass
    try:
        icu = _count_by_ru(
            conn, "icu_monitoring", "ru",
            "UPPER(COALESCE(icu_status,'')) IN ('HIGH','CRITICAL')")
    except Exception:
        pass
    try:
        zc = _count_by_ru(
            conn, "zero_clamp", "ru",
            "COALESCE(UPPER(status),'') NOT IN ('CLOSED','SELESAI','LEPAS','DONE')")
    except Exception:
        pass
    return {"bad_actor": bad, "icu": icu, "zero_clamp": zc}


def _plo_counts(conn):
    """PLO/certification expiry buckets from ATG + metering (+ jetty/spm),
    aggregated nationally and per-RU. Columns known from db_equipment."""
    national = {"active": 0, "exp_90": 0, "exp_30": 0, "expired": 0}
    per_ru = {code: {"expired": 0, "exp_90": 0} for code in RU_CODES}

    def _bucket(table, ru_col, date_col, active_where=""):
        sql = (
            f'SELECT "{ru_col}" AS ru, '
            f'  COUNT(*) AS active, '
            f'  COUNT(*) FILTER (WHERE "{date_col}"::date < CURRENT_DATE) AS expired, '
            f'  COUNT(*) FILTER (WHERE "{date_col}"::date >= CURRENT_DATE '
            f'      AND "{date_col}"::date <= CURRENT_DATE + INTERVAL \'30 day\') AS exp_30, '
            f'  COUNT(*) FILTER (WHERE "{date_col}"::date >= CURRENT_DATE '
            f'      AND "{date_col}"::date <= CURRENT_DATE + INTERVAL \'90 day\') AS exp_90 '
            f"FROM {table} "
        )
        if active_where:
            sql += "WHERE " + active_where + " "
        sql += f'GROUP BY "{ru_col}"'
        for r in conn.execute(sql).fetchall():
            national["active"] += int(r["active"] or 0)
            national["expired"] += int(r["expired"] or 0)
            national["exp_30"] += int(r["exp_30"] or 0)
            national["exp_90"] += int(r["exp_90"] or 0)
            code = match_ru(r["ru"])
            if code:
                per_ru[code]["expired"] += int(r["expired"] or 0)
                per_ru[code]["exp_90"] += int(r["exp_90"] or 0)

    for tbl, rucol, dcol in (
        ("atg_monitoring", "refinery_unit", "date_expired_atg"),
        ("metering_monitoring", "refinery_unit", "date_expired_metering"),
        ("readiness_jetty", "refinery_unit", "expired_tuks"),
        ("readiness_spm", "refinery_unit", "expired_laik_operasi"),
    ):
        try:
            _bucket(tbl, rucol, dcol)
        except Exception:
            continue
    return national, per_ru


def _rkap_from_irkap(conn):
    """RKAP realization from irkap_program (known cols: status_prognosa,
    finish_plan). completed = done/selesai, overdue = finish_plan past & not done."""
    try:
        r = conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 COUNT(*) FILTER (WHERE UPPER(COALESCE(status_prognosa,'')) IN
                     ('DONE','SELESAI','COMPLETE','COMPLETED','FINISH','CLOSED')) AS completed,
                 COUNT(*) FILTER (WHERE finish_plan::date < CURRENT_DATE
                     AND UPPER(COALESCE(status_prognosa,'')) NOT IN
                     ('DONE','SELESAI','COMPLETE','COMPLETED','FINISH','CLOSED')) AS overdue
               FROM irkap_program"""
        ).fetchone()
        total = int(r["total"] or 0)
        if total == 0:
            return None
        completed = int(r["completed"] or 0)
        return {
            "total": total,
            "completed": completed,
            "overdue": int(r["overdue"] or 0),
            "pct": round(completed * 100 / total),
        }
    except Exception:
        return None


def _program_realization_by_ru(conn):
    """Per-RU % program realization from irkap_program."""
    out = {}
    try:
        rows = conn.execute(
            """SELECT refinery_unit AS ru,
                 COUNT(*) AS total,
                 COUNT(*) FILTER (WHERE UPPER(COALESCE(status_prognosa,'')) IN
                     ('DONE','SELESAI','COMPLETE','COMPLETED','FINISH','CLOSED')) AS done
               FROM irkap_program GROUP BY refinery_unit"""
        ).fetchall()
    except Exception:
        return out
    agg = {}
    for r in rows:
        code = match_ru(r["ru"])
        if not code:
            continue
        a = agg.setdefault(code, [0, 0])
        a[0] += int(r["total"] or 0)
        a[1] += int(r["done"] or 0)
    for code, (tot, done) in agg.items():
        if tot:
            out[code] = round(done * 100 / tot)
    return out


def _introspect_ru_metric(conn, table, target_needles, actual_needles):
    """Generic best-effort: find RU col + a 'target' col + an 'actual/realisasi'
    col by name; return {ru_code: (target, actual)}. Only used for tables with
    no existing query (paf, monitoring_operasi, anggaran_maintenance)."""
    if not _table_exists(conn, table):
        return None
    cols = _columns(conn, table)
    rucol = _ru_col(cols)
    tcol = _pick(cols, *target_needles)
    acol = _pick(cols, *actual_needles)
    if not (rucol and acol):
        return None
    sel = f'"{rucol}" AS ru, AVG(NULLIF("{acol}",0)::numeric) AS actual'
    if tcol:
        sel += f', AVG(NULLIF("{tcol}",0)::numeric) AS target'
    try:
        rows = conn.execute(
            f'SELECT {sel} FROM {table} GROUP BY "{rucol}"').fetchall()
    except Exception:
        return None
    out = {}
    for r in rows:
        code = match_ru(r["ru"])
        if not code:
            continue
        out[code] = (
            float(r["target"]) if tcol and r.get("target") is not None else None,
            float(r["actual"]) if r.get("actual") is not None else None,
        )
    return out or None


def _capacity_by_ru(conn):
    """Per-RU operating vs design capacity + utilization from monitoring_operasi
    (unknown schema → introspected by column-name heuristics)."""
    if not _table_exists(conn, "monitoring_operasi"):
        return None
    cols = _columns(conn, "monitoring_operasi")
    rucol = _ru_col(cols)
    design = _pick(cols, "design", "desain", "kapasitas_design", "design_capacity")
    actual = _pick(cols, "actual", "aktual", "operasi", "current", "realisasi")
    if not (rucol and actual):
        return None
    sel = f'"{rucol}" AS ru, AVG(NULLIF("{actual}",0)::numeric) AS actual'
    if design:
        sel += f', AVG(NULLIF("{design}",0)::numeric) AS design'
    try:
        rows = conn.execute(
            f'SELECT {sel} FROM monitoring_operasi GROUP BY "{rucol}"').fetchall()
    except Exception:
        return None
    out = {}
    for r in rows:
        code = match_ru(r["ru"])
        if not code or r.get("actual") is None:
            continue
        a = float(r["actual"])
        d = float(r["design"]) if design and r.get("design") else None
        out[code] = {"current": round(a),
                     "design": round(d) if d else None,
                     "util": round(a / d * 100) if d else None}
    return out or None


def _spend(conn):
    """National maintenance spend from anggaran_maintenance (unknown schema →
    introspected). Returns actual/plan totals + per-RU interpretation."""
    if not _table_exists(conn, "anggaran_maintenance"):
        return None
    cols = _columns(conn, "anggaran_maintenance")
    rucol = _ru_col(cols)
    plan = _pick(cols, "plan", "rkap", "anggaran")
    actual = _pick(cols, "aktual", "actual", "realisasi", "realization")
    if not (plan and actual):
        return None
    try:
        tot = conn.execute(
            f'SELECT SUM("{actual}"::numeric) AS actual, SUM("{plan}"::numeric) AS plan '
            f"FROM anggaran_maintenance").fetchone()
    except Exception:
        return None
    if not tot or not tot["plan"]:
        return None
    national = {"actual": float(tot["actual"] or 0), "plan": float(tot["plan"])}
    per_ru = {}
    if rucol:
        try:
            for r in conn.execute(
                f'SELECT "{rucol}" AS ru, SUM("{actual}"::numeric) AS a, '
                f'SUM("{plan}"::numeric) AS p FROM anggaran_maintenance '
                f'GROUP BY "{rucol}"').fetchall():
                code = match_ru(r["ru"])
                if code and r["p"]:
                    per_ru[code] = float(r["a"] or 0) / float(r["p"]) * 100
        except Exception:
            pass
    return national, per_ru


def _spend_interpretation(pct):
    if pct is None:
        return "On Plan", "healthy"
    if pct < 80:
        return "Under-spend Risk", "watch"
    if pct > 110:
        return "Over-spend watch", "watch"
    if pct > 100:
        return "Execution Risk", "critical"
    return "On Plan", "healthy"


def _data_freshness(conn):
    """Real row counts + latest update per source table."""
    specs = [
        ("PAF Daily", "paf", None),
        ("Monitoring Operasi", "monitoring_operasi", None),
        ("Anggaran Maintenance", "anggaran_maintenance", None),
        ("ATG / Metering", "atg_monitoring", "month_update"),
        ("Bad Actor", "bad_actor_monitoring", "periode"),
        ("ICU", "icu_monitoring", "report_date"),
        ("Readiness Jetty/Tank/SPM", "readiness_jetty", "month_update"),
        ("IRKAP Program", "irkap_program", "start_plan"),
    ]
    out = []
    for label, table, date_col in specs:
        try:
            if not _table_exists(conn, table):
                continue
            cols = _columns(conn, table)
            dcol = date_col if (date_col and date_col in cols) else _pick(
                cols, "month_update", "update", "date", "periode", "tanggal")
            sel = "COUNT(*) AS n"
            if dcol:
                sel += f', MAX("{dcol}"::text) AS latest'
            r = conn.execute(f"SELECT {sel} FROM {table}").fetchone()
            n = int(r["n"] or 0)
            latest = str(r["latest"]) if dcol and r.get("latest") else "—"
            status = "healthy" if n > 0 else "nodata"
            out.append({"dataset": label, "updated": latest,
                        "records": str(n), "completeness": 100 if n else 0,
                        "status": status})
        except Exception:
            continue
    return out


# ─── main assembly ────────────────────────────────────────────────────────────
def get_executive_snapshot() -> dict:
    """Live snapshot with per-section fallback to executive_mock."""
    snap = executive_mock.get_executive_snapshot()  # skeleton / fallback
    if _get_conn is None:
        snap["meta"]["mock_label"] = executive_mock.MOCK_LABEL
        snap["meta"]["mode"] = "mock"
        return snap

    live_sections = []
    try:
        conn = _get_conn()
    except Exception:
        snap["meta"]["mock_label"] = (
            "Mockup data (database unavailable) — replace with live snapshot API.")
        snap["meta"]["mode"] = "mock"
        return snap

    try:
        with conn:
            # by-RU code lookup into the refineries skeleton
            ru_by_code = {r["code"]: r for r in snap["refineries"]}

            # ---- PLO / certification (real) ----
            try:
                national, per_ru = _plo_counts(conn)
                if national["active"] > 0:
                    snap["kpis"]["plo"].update({
                        "active": national["active"],
                        "exp_90": national["exp_90"],
                        "exp_30": national["exp_30"],
                        "expired": national["expired"],
                        "status": "critical" if national["expired"] else
                                  "watch" if national["exp_30"] else "healthy",
                    })
                    for code, v in per_ru.items():
                        ru = ru_by_code.get(code)
                        if ru:
                            st = "critical" if v["expired"] else \
                                 "watch" if v["exp_90"] else "healthy"
                            ru["plo_status"] = st
                            ru["plo"] = {"healthy": "Safe", "watch": "Watch",
                                         "critical": "Critical"}[st]
                    live_sections.append("plo")
            except Exception:
                pass

            # ---- RKAP realization (real, from irkap) ----
            try:
                rkap = _rkap_from_irkap(conn)
                if rkap:
                    snap["kpis"]["rkap"].update({
                        "value": rkap["pct"], "completed": rkap["completed"],
                        "total": rkap["total"], "overdue": rkap["overdue"],
                        "status": "critical" if rkap["overdue"] > 5 else
                                  "watch" if rkap["pct"] < 85 else "healthy",
                    })
                    live_sections.append("rkap")
            except Exception:
                pass

            # ---- Per-RU program realization (real, from irkap) ----
            try:
                prog = _program_realization_by_ru(conn)
                for code, pct in prog.items():
                    ru = ru_by_code.get(code)
                    if ru:
                        ru["program"] = pct
                if prog:
                    live_sections.append("program")
            except Exception:
                pass

            # ---- Reliability counts → per-RU alerts (real) ----
            try:
                rc = _reliability_counts(conn)
                for code in RU_CODES:
                    ru = ru_by_code.get(code)
                    if ru:
                        ru["alerts"] = (rc["bad_actor"][code] + rc["icu"][code]
                                        + rc["zero_clamp"][code])
                live_sections.append("reliability")
            except Exception:
                pass

            # ---- Headline KPIs from unknown-schema tables (best effort) ----
            try:
                paf = _introspect_ru_metric(
                    conn, "paf",
                    ("target", "rencana"), ("real", "aktual", "actual", "capai"))
                if paf:
                    vals = [a for _, a in paf.values() if a is not None]
                    tgts = [t for t, _ in paf.values() if t is not None]
                    if vals:
                        nat = round(sum(vals) / len(vals), 1)
                        snap["kpis"]["paf"]["value"] = nat
                        snap["kpis"]["paf"]["realization"] = nat
                        if tgts:
                            snap["kpis"]["paf"]["target"] = round(sum(tgts)/len(tgts), 1)
                        for code, (t, a) in paf.items():
                            ru = ru_by_code.get(code)
                            if ru and a is not None:
                                ru["paf"] = round(a)
                        live_sections.append("paf")
            except Exception:
                pass

            # ---- Capacity / utilization (best effort: monitoring_operasi) ----
            try:
                cap = _capacity_by_ru(conn)
                if cap:
                    for code, v in cap.items():
                        ru = ru_by_code.get(code)
                        if ru:
                            if v["current"] is not None:
                                ru["current"] = v["current"]
                            if v["design"]:
                                ru["design"] = v["design"]
                            if v["util"] is not None:
                                ru["util"] = v["util"]
                    live_sections.append("capacity")
            except Exception:
                pass

            # ---- Maintenance spend (best effort: anggaran_maintenance) ----
            try:
                sp = _spend(conn)
                if sp:
                    national, per_ru = sp
                    pct = round(national["actual"] / national["plan"] * 100) \
                        if national["plan"] else None
                    interp, st = _spend_interpretation(pct)
                    snap["kpis"]["spend"].update({
                        "actual_t": round(national["actual"] / 1e12, 2),
                        "plan_t": round(national["plan"] / 1e12, 2),
                        "pct_of_plan": pct if pct is not None else
                                       snap["kpis"]["spend"]["pct_of_plan"],
                        "interpretation": interp, "status": st,
                    })
                    for code, rpct in per_ru.items():
                        ru = ru_by_code.get(code)
                        if ru:
                            i2, s2 = _spend_interpretation(round(rpct))
                            ru["spend"], ru["spend_status"] = i2, s2
                    live_sections.append("spend")
            except Exception:
                pass

            # ---- Data freshness (real) ----
            try:
                fresh = _data_freshness(conn)
                if fresh:
                    snap["data_freshness"] = fresh
                    live_sections.append("data_freshness")
            except Exception:
                pass

            # recompute overall RU status from refreshed metrics
            for ru in snap["refineries"]:
                _recompute_status(ru)

    except Exception:
        snap["meta"]["mock_label"] = (
            "Mockup data (query error) — replace with live snapshot API.")
        snap["meta"]["mode"] = "mock"
        return snap

    # label reflects how much is live
    if not live_sections:
        snap["meta"]["mode"] = "mock"
        snap["meta"]["mock_label"] = executive_mock.MOCK_LABEL
    else:
        snap["meta"]["mode"] = "partial"
        snap["meta"]["live_sections"] = live_sections
        snap["meta"]["mock_label"] = (
            "Partial live data — sections not yet wired still use mock values.")
    return snap


def diagnostics() -> dict:
    """Self-contained probe to explain WHY sections are live vs mock.

    Returns per-table: exists / row count / real columns / RU sample values +
    whether they match the normalizer; plus a simulation of the cast-risky
    queries (ATG expiry, IRKAP RKAP, PAF/capacity/spend introspection) with the
    actual exception text when they fail. Consumed by /dashboard/executive/debug.
    """
    out = {"db": "unknown", "live_sections": None, "tables": {}, "sections": {}}
    if _get_conn is None:
        out["db"] = "db_equipment import failed (psycopg/dotenv missing?)"
        return out
    try:
        conn = _get_conn()
    except Exception as e:
        out["db"] = f"connect failed: {e!r}"
        return out
    out["db"] = "connected"

    probe_tables = [
        "paf", "monitoring_operasi", "anggaran_maintenance", "atg_monitoring",
        "metering_monitoring", "readiness_jetty", "readiness_spm",
        "irkap_program", "bad_actor_monitoring", "icu_monitoring", "zero_clamp",
    ]
    try:
        with conn:
            for t in probe_tables:
                info = {}
                try:
                    if not _table_exists(conn, t):
                        out["tables"][t] = {"exists": False}
                        continue
                    cols = _columns(conn, t)
                    info["columns"] = cols
                    info["rows"] = conn.execute(
                        f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                    rc = _ru_col(cols)
                    info["ru_col"] = rc
                    if rc and info["rows"]:
                        vals = conn.execute(
                            f'SELECT DISTINCT "{rc}" v FROM {t} '
                            f'WHERE "{rc}" IS NOT NULL LIMIT 15').fetchall()
                        info["ru_samples"] = {str(r["v"]): match_ru(r["v"])
                                              for r in vals}
                except Exception as e:
                    info["error"] = repr(e)
                out["tables"][t] = info

            # simulate cast-risky sections, capture real errors
            def _sim(name, fn):
                try:
                    out["sections"][name] = {"ok": True, "result": fn()}
                except Exception as e:
                    out["sections"][name] = {"ok": False, "error": repr(e)}

            _sim("plo", lambda: _plo_counts(conn)[0])
            _sim("rkap", lambda: _rkap_from_irkap(conn))
            _sim("program", lambda: _program_realization_by_ru(conn))
            _sim("reliability", lambda: {k: v for k, v in
                                         _reliability_counts(conn).items()})
            _sim("paf", lambda: _introspect_ru_metric(
                conn, "paf", ("target", "rencana"),
                ("real", "aktual", "actual", "capai")))
            _sim("capacity", lambda: _capacity_by_ru(conn))
            _sim("spend", lambda: _spend(conn))
    except Exception as e:
        out["db"] = f"query phase failed: {e!r}"
    return out


def _recompute_status(ru: dict):
    """Derive overall RU status from its (possibly refreshed) metrics."""
    score = 0
    if ru.get("plo_status") == "critical" or ru.get("spend_status") == "critical":
        score = max(score, 2)
    elif ru.get("plo_status") == "watch" or ru.get("spend_status") == "watch":
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
