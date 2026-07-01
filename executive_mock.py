# ═══════════════════════════════════════════════════════════════════════════
# MOCKUP DATA — Executive Reliability Dashboard
#
# This module is the SINGLE source of data for /dashboard/executive.
# Everything returned by get_executive_snapshot() is placeholder ("mockup")
# data shaped to match the real source datasets listed below.
#
# TO GO LIVE: replace the body of get_executive_snapshot() with real DB/API
# queries. Keep the SAME dict shape and the same status tokens
# ("healthy" | "watch" | "critical" | "nodata") and NO template/CSS changes
# are required.
#
# Source datasets that feed this dashboard (28 tables):
#   Anggaran Maintenance (RKAP/PLAN/AKTUAL per RU) .... Maintenance Spend + RKAP
#   PAF .............................................. PAF KPI + scorecard
#   Monitoring Operasi ............................... capacity/util + OA basis
#   ATG Monitoring / Metering Monitoring ............. Regulatory + PLO
#   Readiness Jetty / Tank / SPM ..................... Regulatory + Readiness + PLO
#   Bad Actor / ICU / Power & Steam / Zero Clamp ..... Reliability Hotspots + Readiness
#   Issue PAF ........................................ Reliability Hotspots
#   Jumlah Eqp UTL / Critical Eqp UTL ................ Readiness (Critical Utility)
#   Critical Prim/Sec ................................ Readiness (Critical Primary/Secondary)
#   Rotor Monitoring ................................. Readiness (Rotor)
#   BOC (MTTR/MTBF) .................................. Readiness (BOC)
#   Pipeline Inspection / Inspection Plan ............ Readiness (Inspection) + Program
#   Program Kerja ATG ................................ Program Execution
#   Workplan Jetty / Tank / SPM ...................... Program Execution
#   RCPS / RCPS Rekomendasi .......................... Program Execution + Reliability
#   Monitoring PR (reservasi) ........................ Program Execution
#   TKDN ............................................. Compliance (Data Freshness / drill-down)
#
# NOTE: there is no standalone "OA" or "PLO master" table — OA is DERIVED from
# Monitoring Operasi, and PLO/certification readiness is AGGREGATED from
# ATG / Metering / Jetty / Tank / SPM.
# ═══════════════════════════════════════════════════════════════════════════

MOCK_LABEL = "Mockup data — replace with live snapshot API."


def get_executive_snapshot() -> dict:
    """Return the full executive dashboard snapshot (mock).

    Shape:
        {
          "meta":         {...},
          "kpis":         {oa, paf, rkap, spend, plo},
          "refineries":   [ {..7 units..} ],
          "issues":       {regulatory:[], reliability:[], program:[]},
          "alerts":       {regulatory:[], operational:[], program:[], data:[]},
          "interpretation": [ "...", ... ],
          "readiness":    [ {...cards...} ],
          "data_freshness": [ {...rows...} ],
        }
    """
    refineries = _refineries()
    return {
        "meta": {
            "period": "Jun 2026",
            "last_snapshot": "30 Jun 2026, 08:00 WIB",
            "freshness_pct": 92,
            "freshness_status": "healthy",  # healthy|watch|critical
            "mock_label": MOCK_LABEL,
        },
        "kpis": _kpis(),
        "refineries": refineries,
        "issues": _issues(),
        "alerts": _alerts(),
        "interpretation": _interpretation(),
        "readiness": _readiness(),
        "data_freshness": _data_freshness(),
    }


# ─── KPI strip (5 executive scorecards) ──────────────────────────────────────
def _kpis() -> dict:
    return {
        "oa": {
            "label": "Operational Availability", "sub": "National OA",
            "value": 94.2, "unit": "%", "target": 95.0, "actual": 94.2,
            "status": "watch", "trend": 0.4,
            "source": "Monitoring Operasi (derived)",
        },
        "paf": {
            "label": "Plant Availability Factor", "sub": "National PAF",
            "value": 92.8, "unit": "%", "target": 94.0, "realization": 92.8,
            "status": "watch", "trend": -0.7,
            "source": "PAF",
        },
        "rkap": {
            "label": "RKAP / Strategic Program Realization", "sub": "",
            "value": 71, "unit": "%",
            "completed": 42, "total": 59, "overdue": 6,
            "status": "watch",
            "source": "Anggaran Maintenance · Program Kerja ATG",
        },
        "spend": {
            "label": "Maintenance Spend", "sub": "",
            "actual_t": 2.84, "plan_t": 3.05, "pct_of_plan": 93,
            "interpretation": "On Plan",  # On Plan|Under-spend Risk|Over-spend Watch|Execution Risk
            "status": "healthy",
            "source": "Anggaran Maintenance (RKAP/PLAN/AKTUAL)",
        },
        "plo": {
            "label": "PLO / Certification Readiness", "sub": "",
            "active": 184, "exp_90": 17, "exp_30": 6, "expired": 3,
            "status": "critical",
            "source": "ATG · Metering · Jetty · Tank · SPM",
        },
    }


# ─── 7 Refinery units (map markers + scorecard rows) ─────────────────────────
# map_x / map_y are % positions on the stylized Indonesia SVG (0-100).
def _refineries() -> list:
    return [
        {
            "code": "RU2", "name": "RU II Dumai",
            "current": 158, "design": 170, "util": 93,
            "oa": 95, "paf": 94, "program": 82,
            "spend": "On Plan", "spend_status": "healthy",
            "plo": "Safe", "plo_status": "healthy",
            "alerts": 1, "status": "healthy",
            "map_x": 23.0, "map_y": 32.0,
        },
        {
            "code": "RU3", "name": "RU III Plaju",
            "current": 112, "design": 126, "util": 89,
            "oa": 93, "paf": 91, "program": 75,
            "spend": "Watch", "spend_status": "watch",
            "plo": "Watch", "plo_status": "watch",
            "alerts": 2, "status": "watch",
            "map_x": 26.0, "map_y": 51.0,
        },
        {
            "code": "RU4", "name": "RU IV Cilacap",
            "current": 320, "design": 348, "util": 92,
            "oa": 96, "paf": 95, "program": 79,
            "spend": "Under-spend Risk", "spend_status": "watch",
            "plo": "Safe", "plo_status": "healthy",
            "alerts": 1, "status": "healthy",
            "map_x": 37.0, "map_y": 80.5,
        },
        {
            "code": "RU5", "name": "RU V Balikpapan",
            "current": 226, "design": 260, "util": 87,
            "oa": 90, "paf": 88, "program": 66,
            "spend": "Execution Risk", "spend_status": "critical",
            "plo": "Watch", "plo_status": "watch",
            "alerts": 4, "status": "critical",
            "map_x": 54.0, "map_y": 47.0,
        },
        {
            "code": "RU6", "name": "RU VI Balongan",
            "current": 129, "design": 150, "util": 86,
            "oa": 91, "paf": 89, "program": 61,
            "spend": "On Plan", "spend_status": "healthy",
            "plo": "Critical", "plo_status": "critical",
            "alerts": 5, "status": "critical",
            "map_x": 33.5, "map_y": 76.0,
        },
        {
            "code": "RU7", "name": "RU VII Kasim",
            "current": 8, "design": 10, "util": 80,
            "oa": 92, "paf": 90, "program": 72,
            "spend": "Watch", "spend_status": "watch",
            "plo": "Watch", "plo_status": "watch",
            "alerts": 2, "status": "watch",
            "map_x": 85.5, "map_y": 44.0,
        },
        {
            "code": "TPPI", "name": "TPPI Tuban",
            "current": 76, "design": 98, "util": 78,
            "oa": 88, "paf": 85, "program": 58,
            "spend": "Over-spend watch", "spend_status": "watch",
            "plo": "Critical", "plo_status": "critical",
            "alerts": 3, "status": "critical",
            "map_x": 45.0, "map_y": 76.0,
        },
    ]


# ─── Highlight issue panels ──────────────────────────────────────────────────
def _issues() -> dict:
    return {
        "regulatory": [
            {"ru": "RU VI", "severity": "critical", "title": "PLO jetty permit expired",
             "impact": "risk to marine operation continuity", "action": "expedite permit closure",
             "aging": "Aging 14d", "owner": "HSSE / Asset Integrity", "source": "PLO"},
            {"ru": "RU V", "severity": "watch", "title": "ATG certification expiring in 21 days",
             "impact": "tank compliance risk", "action": "finalize recertification",
             "aging": "Due 21 Jul", "owner": "Operations", "source": "ATG"},
            {"ru": "TPPI", "severity": "critical", "title": "Metering certification expired",
             "impact": "custody transfer compliance risk", "action": "execute recalibration",
             "aging": "Aging 9d", "owner": "Metering", "source": "Metering"},
            {"ru": "RU III", "severity": "watch", "title": "Inspection compliance below threshold",
             "impact": "integrity exposure", "action": "recover overdue inspections",
             "aging": "Due 05 Jul", "owner": "Inspection", "source": "IIMS"},
        ],
        "reliability": [
            {"ru": "RU VI", "severity": "critical", "title": "3 ICU high severity items",
             "impact": "PAF suppression risk", "action": "accelerate mitigation",
             "aging": "Aging 18d", "owner": "Reliability", "source": "ICU"},
            {"ru": "RU V", "severity": "critical", "title": "Power & Steam readiness low",
             "impact": "unit stability threat", "action": "secure boiler support",
             "aging": "Due 03 Jul", "owner": "Utility", "source": "Power & Steam"},
            {"ru": "TPPI", "severity": "critical", "title": "Repeat failure on transfer pump",
             "impact": "throughput loss risk", "action": "execute bad actor action",
             "aging": "Aging 27d", "owner": "Maintenance", "source": "Bad Actor"},
            {"ru": "RU VII", "severity": "watch", "title": "Zero clamp on utility line",
             "impact": "temporary repair exposure", "action": "permanent repair plan",
             "aging": "Due 15 Jul", "owner": "Inspection", "source": "Zero Clamp"},
        ],
        "program": [
            {"ru": "RU IV", "severity": "watch", "title": "Spend below plan while critical programs lag",
             "impact": "execution risk", "action": "recover package release",
             "aging": "Aging 11d", "owner": "MPS", "source": "RKAP"},
            {"ru": "RU V", "severity": "critical", "title": "PR / reservation delays for turnaround package",
             "impact": "schedule risk", "action": "expedite procurement gate",
             "aging": "Aging 16d", "owner": "SCM", "source": "SAP / MM"},
            {"ru": "RU II", "severity": "watch", "title": "RCPS recommendations overdue",
             "impact": "risk closure delay", "action": "finalize action owners",
             "aging": "Due 08 Jul", "owner": "Reliability", "source": "RCPS"},
            {"ru": "RU VI", "severity": "critical", "title": "Jetty/Tank/SPM workplan delayed",
             "impact": "permit and availability risk", "action": "escalate director support",
             "aging": "Aging 22d", "owner": "Project", "source": "Workplan"},
        ],
    }


# ─── Critical Alerts tab (grouped) ───────────────────────────────────────────
def _alerts() -> dict:
    iss = _issues()
    return {
        "regulatory": iss["regulatory"],
        "operational": iss["reliability"],
        "program": iss["program"],
        "data": [
            {"ru": "PLO / Certification", "severity": "critical",
             "title": "Certification dataset stale (>36h)",
             "impact": "readiness figures may be outdated", "action": "re-sync source",
             "aging": "28 Jun 16:00", "owner": "Data Ops", "source": "PLO / Certification"},
            {"ru": "RKAP Programs", "severity": "watch",
             "title": "Program completeness 96%", "impact": "minor gaps in program status",
             "action": "backfill missing records", "aging": "29 Jun 18:00",
             "owner": "Data Ops", "source": "RKAP Programs"},
        ],
    }


# ─── Executive interpretation (briefing note) ────────────────────────────────
def _interpretation() -> list:
    return [
        "RU VI requires immediate attention due to PAF below target, 3 high-severity ICU items, and a PLO permit gap.",
        "RU V is the leading operational risk with Power & Steam readiness pressure and four critical alerts.",
        "Maintenance spend in RU IV is below plan while program realization trails target, indicating execution risk rather than cost efficiency.",
        "Certification watchlist is dominated by ATG and metering items nearing expiry within 90 days.",
        "National OA remains near target, but red units in West and East clusters constrain overall resilience.",
    ]


# ─── Reliability Readiness cards ─────────────────────────────────────────────
def _readiness() -> list:
    return [
        {"title": "Bad Actor", "status": "watch", "primary": "12 active",
         "detail": "4 with open action plan", "source": "Bad Actor Monitoring"},
        {"title": "ICU", "status": "critical", "primary": "7 high severity",
         "detail": "RU VI dominant", "source": "ICU Monitoring"},
        {"title": "Power & Steam", "status": "critical", "primary": "RU V at risk",
         "detail": "boiler redundancy low", "source": "Power & Steam"},
        {"title": "Critical Utility", "status": "watch", "primary": "9 critical eqp",
         "detail": "2 without mitigation", "source": "Jumlah / Critical Eqp UTL"},
        {"title": "Critical Primary/Secondary", "status": "healthy", "primary": "All covered",
         "detail": "no open critical gap", "source": "Critical Prim/Sec"},
        {"title": "Rotor Readiness", "status": "watch", "primary": "3 spare gaps",
         "detail": "workplan in progress", "source": "Rotor Monitoring"},
        {"title": "Zero Clamp", "status": "watch", "primary": "6 active clamps",
         "detail": "1 aging > 90d", "source": "Zero Clamp"},
        {"title": "BOC MTBF/MTTR", "status": "healthy", "primary": "MTBF on target",
         "detail": "MTTR within basis", "source": "BOC"},
        {"title": "Inspection Risk", "status": "watch", "primary": "88% plan realized",
         "detail": "overdue in RU III", "source": "Pipeline Inspection / Inspection Plan"},
    ]


# ─── Data Freshness summary ──────────────────────────────────────────────────
def _data_freshness() -> list:
    return [
        {"dataset": "OA Snapshot", "updated": "30 Jun 07:50", "records": "7 RU",
         "completeness": 100, "status": "healthy"},
        {"dataset": "PAF Daily", "updated": "30 Jun 07:40", "records": "7 RU",
         "completeness": 100, "status": "healthy"},
        {"dataset": "RKAP Programs", "updated": "29 Jun 18:00", "records": "59",
         "completeness": 96, "status": "watch"},
        {"dataset": "Maintenance Spend", "updated": "29 Jun 20:15", "records": "7 RU",
         "completeness": 98, "status": "healthy"},
        {"dataset": "PLO / Certification", "updated": "28 Jun 16:00", "records": "210",
         "completeness": 89, "status": "critical"},
        {"dataset": "Bad Actor / ICU", "updated": "30 Jun 06:30", "records": "47",
         "completeness": 94, "status": "healthy"},
    ]
