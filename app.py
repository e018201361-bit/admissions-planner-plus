# app.py — Admissions Planner PLUS (Chemo FULL version)
# - Admissions planner as before
# - Hospital/ward add + delete (safe if no patients)
# - Patient details, rounds, photos, transfers
# - Chemo module per patient (regimen templates, BSA, cycle logging, CSV export)

import os
import sqlite3
from datetime import date, datetime, timedelta
import json
import io

import pandas as pd
import requests
import streamlit as st

DB_PATH = "admit_planner.db"
MEDIA_DIR = "media"


# ---------------- DB helpers ----------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # master tables
    c.execute(
        """CREATE TABLE IF NOT EXISTS hospitals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS wards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hospital_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        UNIQUE(hospital_id, name)
    )"""
    )

    # patients table (base)
    c.execute(
        """CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        patient_name TEXT NOT NULL,
        mrn TEXT,
        age INTEGER,
        sex TEXT,
        hospital_id INTEGER,
        ward_id INTEGER,
        status TEXT,
        planned_admit_date TEXT,
        admit_date TEXT,
        bed TEXT,
        diagnosis TEXT,
        responsible_md TEXT,
        priority TEXT,
        precautions TEXT,
        notes TEXT,
        last_rounded_at TEXT
    )"""
    )
    conn.commit()

    # ensure extra columns for body size / chemo plan
    c.execute("PRAGMA table_info(patients)")
    cols = [row[1] for row in c.fetchall()]
    extra_cols = [
        ("weight_kg", "REAL"),
        ("height_cm", "REAL"),
        ("bsa", "REAL"),
        ("chemo_regimen", "TEXT"),
        ("chemo_total_cycles", "INTEGER"),
        ("chemo_interval_days", "INTEGER"),
    ]
    for col_name, col_type in extra_cols:
        if col_name not in cols:
            try:
                c.execute(f"ALTER TABLE patients ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
    conn.commit()

    # logs / transfers / photos / settings
    c.execute(
        """CREATE TABLE IF NOT EXISTS rounds_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        author TEXT,
        note TEXT
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        from_hospital_id INTEGER,
        from_ward_id INTEGER,
        to_hospital_id INTEGER NOT NULL,
        to_ward_id INTEGER,
        moved_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        reason TEXT
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS patient_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        caption TEXT,
        uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )"""
    )

    # chemo templates (JSON payload)
    c.execute(
        """CREATE TABLE IF NOT EXISTS chemo_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        payload TEXT NOT NULL
    )"""
    )

    # chemo courses per cycle & drug
    c.execute(
        """CREATE TABLE IF NOT EXISTS chemo_courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        cycle_no INTEGER NOT NULL,
        given_date TEXT NOT NULL,
        regimen_name TEXT,
        drug_name TEXT,
        mode TEXT,
        dose_per_m2 REAL,
        dose_per_kg REAL,
        fixed_dose_mg REAL,
        dose_mg REAL,
        dose_factor REAL,
        notes TEXT
    )"""
    )

    # assessments (CT / PET / BM etc.)
    c.execute(
        """CREATE TABLE IF NOT EXISTS chemo_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        cycle_no INTEGER,
        assess_date TEXT NOT NULL,
        assess_type TEXT,
        result_summary TEXT,
        response TEXT
    )"""
    )

    conn.commit()

    # seed default hospitals only if table empty
    c.execute("SELECT COUNT(*) FROM hospitals")
    if c.fetchone()[0] == 0:
        for name in ("Hospital 1", "Hospital 2", "Hospital 3"):
            c.execute("INSERT INTO hospitals(name) VALUES (?)", (name,))

    conn.commit()

    # seed chemo templates if empty
    c.execute("SELECT COUNT(*) FROM chemo_templates")
    if c.fetchone()[0] == 0:
        seed_chemo_templates(c)
        conn.commit()

    conn.close()


def seed_chemo_templates(c):
    """Insert built-in chemo templates (simplified regimens)."""
    templates = {
        # CHOP
        "CHOP": [
            {"drug": "Cyclophosphamide", "mode": "per_m2", "dose_per_m2": 750.0},
            {"drug": "Doxorubicin", "mode": "per_m2", "dose_per_m2": 50.0},
            {"drug": "Vincristine", "mode": "per_m2", "dose_per_m2": 1.4, "max_mg": 2.0},
            {"drug": "Prednisolone", "mode": "fixed", "fixed_dose_mg": 100.0},
        ],
        # R-CHOP
        "R-CHOP": [
            {"drug": "Rituximab", "mode": "per_kg", "dose_per_kg": 375.0},
            {"drug": "Cyclophosphamide", "mode": "per_m2", "dose_per_m2": 750.0},
            {"drug": "Doxorubicin", "mode": "per_m2", "dose_per_m2": 50.0},
            {"drug": "Vincristine", "mode": "per_m2", "dose_per_m2": 1.4, "max_mg": 2.0},
            {"drug": "Prednisolone", "mode": "fixed", "fixed_dose_mg": 100.0},
        ],
        # ICE (approx per_m2 for Carboplatin)
        "ICE": [
            {"drug": "Ifosfamide", "mode": "per_m2", "dose_per_m2": 5000.0},
            {"drug": "Carboplatin", "mode": "per_m2", "dose_per_m2": 400.0},
            {"drug": "Etoposide", "mode": "per_m2", "dose_per_m2": 100.0},
        ],
        # BV-AVD
        "BV-AVD": [
            {"drug": "Brentuximab vedotin", "mode": "per_kg", "dose_per_kg": 1.2},
            {"drug": "Doxorubicin", "mode": "per_m2", "dose_per_m2": 25.0},
            {"drug": "Vinblastine", "mode": "per_m2", "dose_per_m2": 6.0},
            {"drug": "Dacarbazine", "mode": "per_m2", "dose_per_m2": 375.0},
        ],
        # Pola-R-CHP
        "Pola-R-CHP": [
            {"drug": "Polatuzumab vedotin", "mode": "per_kg", "dose_per_kg": 1.8},
            {"drug": "Rituximab", "mode": "per_kg", "dose_per_kg": 375.0},
            {"drug": "Cyclophosphamide", "mode": "per_m2", "dose_per_m2": 750.0},
            {"drug": "Doxorubicin", "mode": "per_m2", "dose_per_m2": 50.0},
            {"drug": "Prednisolone", "mode": "fixed", "fixed_dose_mg": 100.0},
        ],
        # DA-EPOCH-R (simplified)
        "DA-EPOCH-R": [
            {"drug": "Etoposide", "mode": "per_m2", "dose_per_m2": 50.0},
            {"drug": "Doxorubicin", "mode": "per_m2", "dose_per_m2": 10.0},
            {"drug": "Vincristine", "mode": "per_m2", "dose_per_m2": 0.4, "max_mg": 2.0},
            {"drug": "Cyclophosphamide", "mode": "per_m2", "dose_per_m2": 750.0},
            {"drug": "Rituximab", "mode": "per_kg", "dose_per_kg": 375.0},
        ],
        # HyperCVAD (block A simplified)
        "HyperCVAD": [
            {"drug": "Cyclophosphamide", "mode": "per_m2", "dose_per_m2": 300.0},
            {"drug": "Vincristine", "mode": "per_m2", "dose_per_m2": 1.4, "max_mg": 2.0},
            {"drug": "Doxorubicin", "mode": "per_m2", "dose_per_m2": 50.0},
            {"drug": "Dexamethasone", "mode": "fixed", "fixed_dose_mg": 40.0},
        ],
        # Daratumumab IV
        "Daratumumab IV": [
            {"drug": "Daratumumab", "mode": "per_kg", "dose_per_kg": 16.0},
        ],
        # Daratumumab SC
        "Daratumumab SC": [
            {"drug": "Daratumumab (SC)", "mode": "fixed", "fixed_dose_mg": 1800.0},
        ],
    }

    for name, payload in templates.items():
        c.execute(
            "INSERT OR IGNORE INTO chemo_templates(name, payload) VALUES (?, ?)",
            (name, json.dumps(payload)),
        )


def fetch_df(q: str, params=()):
    conn = get_conn()
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


def execute(q: str, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q, params)
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_setting(key, default=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key, value):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------------- Common helpers ----------------

STATUSES = ["Planned", "Admitted", "Discharged", "Cancelled"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]
PRECAUTIONS = ["None", "Contact", "Droplet", "Airborne"]


def ensure_media_dir():
    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR, exist_ok=True)


def get_hospitals():
    return fetch_df("SELECT id, name FROM hospitals ORDER BY name")


def get_wards(hospital_id=None):
    if hospital_id:
        return fetch_df("SELECT id, name FROM wards WHERE hospital_id=? ORDER BY name", (hospital_id,))
    return fetch_df(
        """SELECT w.id, w.name, h.name AS hospital
           FROM wards w JOIN hospitals h ON w.hospital_id = h.id
           ORDER BY h.name, w.name"""
    )


def get_patients(filters=None):
    where, params = [], []
    f = filters or {}
    if f.get("hospital_id"):
        where.append("p.hospital_id=?"); params.append(f["hospital_id"])
    if f.get("ward_id"):
        where.append("p.ward_id=?"); params.append(f["ward_id"])
    if f.get("status"):
        where.append("p.status=?"); params.append(f["status"])
    if f.get("planned_only"):
        where.append("p.status='Planned'")
    if f.get("date_start"):
        where.append("(p.planned_admit_date>=? OR p.admit_date>=?)"); params += [f["date_start"], f["date_start"]]
    if f.get("date_end"):
        where.append("(p.planned_admit_date<=? OR p.admit_date<=?)"); params += [f["date_end"], f["date_end"]]

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    q = f"""
    SELECT p.id,
           p.created_at AS "Date Created",
           p.patient_name AS "Patient Name",
           p.mrn AS "HN/MRN",
           p.age AS "Age",
           p.sex AS "Sex",
           h.name AS "Hospital",
           w.name AS "Ward",
           p.status AS "Status",
           p.planned_admit_date AS "Planned Admit Date",
           p.admit_date AS "Admit Date",
           p.bed AS "Bed",
           p.diagnosis AS "Diagnosis",
           p.responsible_md AS "Responsible MD",
           p.priority AS "Priority",
           p.precautions AS "Infection Precautions",
           p.notes AS "Notes",
           p.last_rounded_at AS "Last Rounded"
    FROM patients p
    LEFT JOIN hospitals h ON p.hospital_id = h.id
    LEFT JOIN wards w ON p.ward_id = w.id
    {where_clause}
    ORDER BY CASE WHEN p.status='Planned' THEN 0 ELSE 1 END,
             COALESCE(p.planned_admit_date, p.admit_date) ASC,
             p.id DESC
    """
    return fetch_df(q, tuple(params))


def get_patient_by_id(pid: int):
    df = fetch_df("SELECT * FROM patients WHERE id=?", (pid,))
    return df.iloc[0].to_dict() if len(df) else None


# ---------------- Chemo helpers ----------------

def calc_bsa(weight_kg: float, height_cm: float) -> float:
    if not weight_kg or not height_cm:
        return None
    try:
        return ((height_cm * weight_kg) / 3600.0) ** 0.5
    except Exception:
        return None


def get_chemo_templates_df():
    return fetch_df("SELECT id, name, payload FROM chemo_templates ORDER BY name")


def get_chemo_template_by_name(name: str):
    df = fetch_df("SELECT payload FROM chemo_templates WHERE name=?", (name,))
    if len(df) == 0:
        return None
    try:
        return json.loads(df["payload"].iloc[0])
    except Exception:
        return None


def compute_doses_for_template(template_name: str, weight_kg: float, height_cm: float):
    bsa = calc_bsa(weight_kg, height_cm)
    tpl = get_chemo_template_by_name(template_name)
    if not tpl:
        return [], bsa

    rows = []
    for item in tpl:
        drug = item.get("drug", "?")
        mode = item.get("mode", "")
        max_mg = item.get("max_mg")
        dose_per_m2 = item.get("dose_per_m2")
        dose_per_kg = item.get("dose_per_kg")
        fixed_dose_mg = item.get("fixed_dose_mg")

        dose_mg = None
        if mode == "per_m2" and bsa:
            dose_mg = dose_per_m2 * bsa if dose_per_m2 is not None else None
        elif mode == "per_kg" and weight_kg:
            dose_mg = dose_per_kg * weight_kg if dose_per_kg is not None else None
        elif mode == "fixed":
            dose_mg = fixed_dose_mg

        if max_mg is not None and dose_mg is not None:
            dose_mg = min(dose_mg, max_mg)

        rows.append(
            {
                "drug_name": drug,
                "mode": mode,
                "dose_per_m2": dose_per_m2,
                "dose_per_kg": dose_per_kg,
                "fixed_dose_mg": fixed_dose_mg,
                "dose_mg": round(dose_mg, 1) if isinstance(dose_mg, (int, float)) else None,
            }
        )

    return rows, bsa


def get_chemo_courses(patient_id: int):
    return fetch_df(
        """SELECT cycle_no AS Cycle,
                   given_date AS Date,
                   regimen_name AS Regimen,
                   drug_name AS Drug,
                   dose_mg AS Dose_mg,
                   dose_factor AS Dose_factor,
                   notes AS Notes
            FROM chemo_courses
            WHERE patient_id=?
            ORDER BY cycle_no, drug_name""",
        (patient_id,),
    )


def get_chemo_assessments(patient_id: int):
    return fetch_df(
        """SELECT cycle_no AS Cycle,
                   assess_date AS Date,
                   assess_type AS Type,
                   response AS Response,
                   result_summary AS Summary
            FROM chemo_assessments
            WHERE patient_id=?
            ORDER BY assess_date""",
        (patient_id,),
    )


def export_chemo_csv(patient_id: int, patient_name: str):
    chemo = get_chemo_courses(patient_id)
    assess = get_chemo_assessments(patient_id)
    buffer = io.StringIO()

    buffer.write(f"Chemo history for {patient_name}\n")
    if len(chemo):
        chemo.to_csv(buffer, index=False)
    else:
        buffer.write("No chemo courses recorded\n")

    buffer.write("\nAssessments\n")
    if len(assess):
        assess.to_csv(buffer, index=False)
    else:
        buffer.write("No assessments recorded\n")

    return buffer.getvalue().encode("utf-8")


# ---------------- Streamlit app ----------------

st.set_page_config(page_title="Admissions Planner PLUS", layout="wide")
init_db()
ensure_media_dir()

st.title("🗂️ Admissions Planner — PLUS (with Chemo module)")
st.caption("Admit planner + rounds + photos + transfers + chemo regimens & cycles + CSV export")


# Tabs
TabAdd, TabPlanner, TabDashboard, TabPatient, TabSettings = st.tabs(
    ["➕ เพิ่มผู้ป่วย", "📅 แผน Admit", "📊 Dashboard", "👤 รายละเอียดผู้ป่วย", "⚙️ Settings / Reminders"]
)


# ---------------- SETTINGS ----------------
with TabSettings:
    st.subheader("การแจ้งเตือน (กดส่งเมื่อพร้อม)")

    with st.expander("LINE Notify"):
        line_token = st.text_input("LINE Notify Token", value=get_setting("line_token", ""), type="password")
        if st.button("บันทึก Token LINE"):
            set_setting("line_token", line_token.strip())
            st.success("บันทึกแล้ว")
        st.markdown("วิธีได้ Token: https://notify-bot.line.me/my/")

    st.subheader("ช่วงเวลา Rounds (ใช้ตรวจ Missed)")
    col1, col2 = st.columns(2)
    with col1:
        round_start = st.time_input(
            "เริ่ม", value=pd.to_datetime(get_setting("round_start", "08:00")).time()
        )
    with col2:
        round_end = st.time_input(
            "สิ้นสุด", value=pd.to_datetime(get_setting("round_end", "12:00")).time()
        )
    if st.button("บันทึกช่วงเวลา"):
        set_setting("round_start", round_start.strftime("%H:%M"))
        set_setting("round_end", round_end.strftime("%H:%M"))
        st.success("บันทึกแล้ว")

    st.subheader("โรงพยาบาลและวอร์ด")

    # Add hospital
    with st.form("add_hospital_form", clear_on_submit=True):
        new_hosp = st.text_input("เพิ่มชื่อโรงพยาบาล")
        submitted = st.form_submit_button("เพิ่มโรงพยาบาล")
        if submitted and new_hosp.strip():
            try:
                execute("INSERT INTO hospitals(name) VALUES (?)", (new_hosp.strip(),))
                st.success("เพิ่มโรงพยาบาลแล้ว")
                st.rerun()
            except sqlite3.IntegrityError:
                st.warning("มีโรงพยาบาลชื่อนี้อยู่แล้ว")

    # List hospitals with delete
    hosp_df = get_hospitals()
    if len(hosp_df):
        st.markdown("**รายชื่อโรงพยาบาล**")
        for _, r in hosp_df.iterrows():
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"`#{int(r['id'])}` — **{r['name']}**")
            with c2:
                if st.button("🗑️ ลบ", key=f"del_hosp_{int(r['id'])}"):
                    cnt = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE hospital_id=?", (int(r["id"]),))["c"][0]
                    if cnt > 0:
                        st.error("ลบไม่ได้: ยังมีผู้ป่วยในโรงพยาบาลนี้")
                    else:
                        execute("DELETE FROM wards WHERE hospital_id=?", (int(r["id"]),))
                        execute("DELETE FROM hospitals WHERE id=?", (int(r["id"]),))
                        st.success("ลบโรงพยาบาลเรียบร้อย")
                        st.rerun()
        st.divider()
    else:
        st.info("ยังไม่มีโรงพยาบาล")

    # Add ward
    hospitals = get_hospitals()
    hosp_map = dict(zip(hospitals["name"], hospitals["id"])) if len(hospitals) else {}
    hosp_choice = st.selectbox("เลือกโรงพยาบาลเพื่อเพิ่มวอร์ด", [""] + hospitals["name"].tolist())
    with st.form("add_ward_form", clear_on_submit=True):
        ward_name = st.text_input("ชื่อวอร์ด")
        submitted = st.form_submit_button("เพิ่มวอร์ด")
        if submitted:
            if not hosp_choice:
                st.error("กรุณาเลือกโรงพยาบาล")
            elif not ward_name.strip():
                st.error("กรุณากรอกชื่อวอร์ด")
            else:
                try:
                    execute("INSERT INTO wards(hospital_id, name) VALUES (?,?)", (hosp_map[hosp_choice], ward_name.strip()))
                    st.success("เพิ่มวอร์ดแล้ว")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("วอร์ดนี้มีอยู่แล้วในโรงพยาบาลนี้")

    wards_all = get_wards()
    if len(wards_all):
        st.markdown("**รายชื่อวอร์ดทั้งหมด**")
        for _, r in wards_all.iterrows():
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"`#{int(r['id'])}` — **{r['name']}** (_{r['hospital']}_)")
            with c2:
                if st.button("🗑️ ลบ", key=f"del_ward_{int(r['id'])}"):
                    cnt = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE ward_id=?", (int(r["id"]),))["c"][0]
                    if cnt > 0:
                        st.error("ลบไม่ได้: ยังมีผู้ป่วยอยู่ในวอร์ดนี้")
                    else:
                        execute("DELETE FROM wards WHERE id=?", (int(r["id"]),))
                        st.success("ลบวอร์ดเรียบร้อย")
                        st.rerun()
    else:
        st.info("ยังไม่มีวอร์ด")

    st.subheader("Chemo templates (อ่านอย่างเดียวในเวอร์ชันนี้)")
    tmpl_df = get_chemo_templates_df()
    if len(tmpl_df):
        for _, r in tmpl_df.iterrows():
            st.markdown(f"**{r['name']}**")
            try:
                payload = json.loads(r["payload"])
            except Exception:
                payload = []
            if payload:
                df_t = pd.DataFrame(payload)
                st.dataframe(df_t, use_container_width=True, hide_index=True)
            st.divider()
    else:
        st.info("ยังไม่มี chemo templates")

    st.subheader("🔔 ส่งแจ้งเตือน Missed Rounds ตอนนี้ (manual)")
    miss_df = fetch_df(
        """SELECT p.id, p.patient_name, h.name AS hospital, COALESCE(w.name,'') AS ward, p.last_rounded_at
            FROM patients p
            LEFT JOIN hospitals h ON p.hospital_id=h.id
            LEFT JOIN wards w ON p.ward_id=w.id
            WHERE p.status='Admitted'"""
    )
    missed = []
    for _, r in miss_df.iterrows():
        is_missed = True
        if r["last_rounded_at"]:
            try:
                if datetime.fromisoformat(r["last_rounded_at"]).date() == date.today():
                    is_missed = False
            except Exception:
                pass
        if is_missed:
            missed.append(f"{r['patient_name']} ({r['hospital']} / {r['ward']})")

    if missed:
        st.warning("ยังไม่มีบันทึกราวนด์วันนี้สำหรับ:\n- " + "\n- ".join(missed))
        c1, c2 = st.columns(2)
        with c1:
            if st.button("ส่ง LINE Notify ตอนนี้"):
                token = get_setting("line_token", "")
                if token:
                    ok = notify_line(token, "ยังไม่มีบันทึกราวนด์วันนี้สำหรับ:\n" + "\n".join(missed))
                    st.success("ส่งแล้ว" if ok else "ส่งไม่สำเร็จ (ตรวจ token/เน็ต)")
                else:
                    st.error("ยังไม่ได้ตั้งค่า LINE Token")
        with c2:
            st.info("Email แจ้งเตือนยังไม่ได้ตั้งค่าในเวอร์ชันนี้ (สามารถเพิ่มภายหลังได้)")
    else:
        st.info("วันนี้ครบทุกเคสแล้ว 🎉")


# ---------------- ADD PATIENT ----------------
with TabAdd:
    st.subheader("เพิ่มผู้ป่วย")
    hospitals = get_hospitals()
    hosp_ids = dict(zip(hospitals["name"], hospitals["id"])) if len(hospitals) else {}

    with st.form("add_patient_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            patient_name = st.text_input("ชื่อผู้ป่วย *")
            mrn = st.text_input("HN/MRN")
            age = st.number_input("อายุ", min_value=0, max_value=120, step=1)
        with c2:
            sex = st.selectbox("เพศ", ["", "M", "F", "Other"])
            hosp = st.selectbox("โรงพยาบาล *", [""] + hospitals["name"].tolist())
            ward_id = None
            if hosp:
                wards_df = get_wards(hosp_ids[hosp])
                ward_options = wards_df["name"].tolist()
                ward = st.selectbox("วอร์ด", [""] + ward_options)
                ward_id = dict(zip(ward_options, wards_df["id"])).get(ward)
            else:
                st.info("เลือกโรงพยาบาลก่อนเพื่อเลือกวอร์ด")
        with c3:
            status = st.selectbox("สถานะ", STATUSES, index=0)
            priority = st.selectbox("ลำดับความสำคัญ", PRIORITIES, index=1)
            precautions = st.selectbox("Infection Precautions", PRECAUTIONS, index=0)

        c4, c5, c6 = st.columns(3)
        with c4:
            planned_date = st.date_input("Planned Admit Date", value=date.today())
            admit_date = st.date_input("Admit Date (ถ้ามี)", value=None)
        with c5:
            bed = st.text_input("เตียง (ถ้ามี)")
            diagnosis = st.text_input("Diagnosis")
        with c6:
            responsible_md = st.text_input("Responsible MD")
            notes = st.text_area("Notes", height=80)

        submitted = st.form_submit_button("บันทึก")
        if submitted:
            if not patient_name.strip():
                st.error("กรุณากรอกชื่อผู้ป่วย")
            elif not hosp:
                st.error("กรุณาเลือกโรงพยาบาล")
            else:
                execute(
                    """INSERT INTO patients(
                        patient_name, mrn, age, sex, hospital_id, ward_id,
                        status, planned_admit_date, admit_date, bed, diagnosis,
                        responsible_md, priority, precautions, notes, last_rounded_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        patient_name.strip(),
                        mrn.strip() or None,
                        int(age) if age else None,
                        sex or None,
                        hosp_ids.get(hosp),
                        ward_id,
                        status,
                        planned_date.isoformat() if planned_date else None,
                        admit_date.isoformat() if admit_date else None,
                        bed or None,
                        diagnosis or None,
                        responsible_md or None,
                        priority,
                        precautions,
                        notes or None,
                        None,
                    ),
                )
                st.success("เพิ่มผู้ป่วยเรียบร้อย")


# ---------------- PLANNER ----------------
with TabPlanner:
    st.subheader("รายการวางแผน Admit (Planned)")
    hospitals = get_hospitals()
    hosp_filter = st.selectbox("โรงพยาบาล", ["ทั้งหมด"] + hospitals["name"].tolist(), index=0)
    ward_id_filter = None
    if hosp_filter != "ทั้งหมด":
        wards_df = get_wards(dict(zip(hospitals["name"], hospitals["id"]))[hosp_filter])
        ward_choice = st.selectbox("วอร์ด", ["ทั้งหมด"] + wards_df["name"].tolist(), index=0)
        if ward_choice != "ทั้งหมด":
            ward_id_filter = dict(zip(wards_df["name"], wards_df["id"]))[ward_choice]

    d1, d2 = st.columns(2)
    with d1:
        start = st.date_input("เริ่มวันที่", value=date.today())
    with d2:
        end = st.date_input("ถึงวันที่", value=date.today() + timedelta(days=14))

    filters = {"planned_only": True, "date_start": start.isoformat(), "date_end": end.isoformat()}
    if hosp_filter != "ทั้งหมด":
        filters["hospital_id"] = dict(zip(hospitals["name"], hospitals["id"]))[hosp_filter]
    if ward_id_filter:
        filters["ward_id"] = ward_id_filter

    df_plan = get_patients(filters)
    st.dataframe(df_plan, use_container_width=True, hide_index=True)


# ---------------- DASHBOARD ----------------
with TabDashboard:
    st.subheader("สรุปภาพรวม")
    tot_planned = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE status='Planned'")["c"][0]
    tot_admitted = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE status='Admitted'")["c"][0]
    tot_discharged = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE status='Discharged'")["c"][0]
    planned_7d = fetch_df(
        "SELECT COUNT(*) AS c FROM patients WHERE status='Planned' AND planned_admit_date BETWEEN date('now','localtime') AND date('now','localtime','+7 day')"
    )["c"][0]
    admitted_today = fetch_df(
        "SELECT COUNT(*) AS c FROM patients WHERE status='Admitted' AND admit_date = date('now','localtime')"
    )["c"][0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Planned (ทั้งหมด)", tot_planned)
    c2.metric("Admitted (ทั้งหมด)", tot_admitted)
    c3.metric("Discharged (ทั้งหมด)", tot_discharged)
    c4.metric("Planned (7 วันถัดไป)", planned_7d)
    c5.metric("Admitted วันนี้", admitted_today)

    st.markdown("#### แยกตามโรงพยาบาล")
    hosp_df2 = get_hospitals()
    rows = []
    for _, r in hosp_df2.iterrows():
        hid = r["id"]
        rows.append(
            {
                "Hospital": r["name"],
                "Planned": fetch_df("SELECT COUNT(*) AS c FROM patients WHERE hospital_id=? AND status='Planned'", (hid,))["c"][0],
                "Admitted": fetch_df("SELECT COUNT(*) AS c FROM patients WHERE hospital_id=? AND status='Admitted'", (hid,))["c"][0],
                "Discharged": fetch_df("SELECT COUNT(*) AS c FROM patients WHERE hospital_id=? AND status='Discharged'", (hid,))["c"][0],
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---------------- PATIENT DETAIL + CHEMO ----------------
with TabPatient:
    st.subheader("รายละเอียดผู้ป่วย / Rounds / รูป / โยกย้าย / Chemo")
    mini = fetch_df(
        """SELECT p.id, p.patient_name AS name, COALESCE(p.mrn,'') AS mrn,
                   h.name AS hosp, COALESCE(w.name,'') AS ward
            FROM patients p
            LEFT JOIN hospitals h ON p.hospital_id=h.id
            LEFT JOIN wards w ON p.ward_id=w.id
            WHERE p.status IN ('Planned','Admitted')
            ORDER BY p.id DESC"""
    )

    if len(mini) == 0:
        st.info("ยังไม่มีผู้ป่วย (หรือทุกคนจำหน่ายแล้ว)")
    else:
        label_map = {
            f"{r['name']} | {r['mrn']} | {r['hosp']} | {r['ward']}": int(r["id"])
            for _, r in mini.iterrows()
        }
        choice = st.selectbox("เลือกผู้ป่วย", list(label_map.keys()))
        pid = label_map[choice]
        data = get_patient_by_id(pid)

        # basic info
        st.markdown(
            f"**ชื่อ:** {data['patient_name']}  |  **HN/MRN:** {data.get('mrn','') or ''}  |  **สถานะ:** {data.get('status','')}"
        )
        hosp_name = fetch_df("SELECT name FROM hospitals WHERE id=?", (data["hospital_id"],)).squeeze()
        ward_name = (
            fetch_df("SELECT name FROM wards WHERE id=?", (data["ward_id"],)).squeeze()
            if data.get("ward_id")
            else "-"
        )
        st.markdown(f"**โรงพยาบาล/วอร์ด:** {hosp_name} / {ward_name}")
        st.markdown(
            f"**เตียง:** {data.get('bed') or '-'}  |  **DX:** {data.get('diagnosis') or '-'}  |  **แพทย์:** {data.get('responsible_md') or '-'}"
        )
        st.markdown(f"**Last rounded:** {data.get('last_rounded_at') or '-'}")

        # sub-tabs inside patient
        T_Round, T_Photo, T_Transfer, T_Chemo = st.tabs([
            "📝 Rounds notes",
            "🖼️ Photos",
            "🔁 โยกย้ายวอร์ด",
            "💉 Chemo",
        ])

        # ----- Rounds -----
        with T_Round:
            st.markdown("เพิ่มบันทึกราวนด์ (จะอัปเดต 'Last rounded' อัตโนมัติ)")
            with st.form("form_rounds_note", clear_on_submit=True):
                author = st.text_input("ผู้บันทึก", value="")
                note = st.text_area("บันทึกราวนด์", height=140)
                if st.form_submit_button("บันทึกบันทึกราวนด์"):
                    if not note.strip():
                        st.error("กรุณากรอกบันทึก")
                    else:
                        execute(
                            "INSERT INTO rounds_logs(patient_id, author, note) VALUES (?,?,?)",
                            (pid, author or None, note.strip()),
                        )
                        execute("UPDATE patients SET last_rounded_at=datetime('now','localtime') WHERE id=?", (pid,))
                        st.success("บันทึกแล้ว")
                        st.rerun()
            logs = fetch_df(
                "SELECT created_at, author, note FROM rounds_logs WHERE patient_id=? ORDER BY id DESC",
                (pid,),
            )
            st.dataframe(logs, use_container_width=True, hide_index=True)

        # ----- Photos -----
        with T_Photo:
            st.markdown("อัปโหลดรูปภาพที่เกี่ยวข้อง")
            file = st.file_uploader("เลือกรูป", type=["png", "jpg", "jpeg", "gif", "webp"])
            caption = st.text_input("คำอธิบายรูป (ถ้ามี)")
            if st.button("อัปโหลดรูป"):
                if file is None:
                    st.error("กรุณาเลือกรูป")
                else:
                    ensure_media_dir()
                    ext = os.path.splitext(file.name)[1].lower()
                    safe_name = f"p{pid}_{int(datetime.now().timestamp())}{ext}"
                    save_path = os.path.join(MEDIA_DIR, safe_name)
                    with open(save_path, "wb") as f:
                        f.write(file.read())
                    execute(
                        "INSERT INTO patient_photos(patient_id, file_path, caption) VALUES (?,?,?)",
                        (pid, save_path, caption.strip() or None),
                    )
                    st.success("อัปโหลดแล้ว")
                    st.rerun()
            gal = fetch_df(
                "SELECT id, file_path, caption, uploaded_at FROM patient_photos WHERE patient_id=? ORDER BY id DESC",
                (pid,),
            )
            if len(gal):
                for _, r in gal.iterrows():
                    st.image(
                        r["file_path"],
                        caption=f"{r['caption'] or ''} (อัปโหลด {r['uploaded_at']})",
                        use_column_width=True,
                    )

        # ----- Transfer -----
        with T_Transfer:
            st.markdown("ย้ายโรงพยาบาล/วอร์ด พร้อมบันทึกประวัติ")
            hospitals_all = get_hospitals()
            hosp_ids2 = dict(zip(hospitals_all["name"], hospitals_all["id"])) if len(hospitals_all) else {}
            new_hosp = st.selectbox("ย้ายไปโรงพยาบาล", hospitals_all["name"].tolist(), index=0)
            wards_df2 = get_wards(hosp_ids2[new_hosp]) if new_hosp else pd.DataFrame()
            new_ward = st.selectbox("ย้ายไปวอร์ด", [""] + wards_df2["name"].tolist(), index=0)
            reason = st.text_input("เหตุผล/หมายเหตุการย้าย", value="")
            if st.button("ย้ายตอนนี้"):
                to_hid = hosp_ids2[new_hosp]
                to_wid = (
                    dict(zip(wards_df2["name"], wards_df2["id"])).get(new_ward)
                    if new_ward
                    else None
                )
                execute(
                    "INSERT INTO transfers(patient_id, from_hospital_id, from_ward_id, to_hospital_id, to_ward_id, reason) VALUES (?,?,?,?,?,?)",
                    (pid, data["hospital_id"], data["ward_id"], to_hid, to_wid, reason or None),
                )
                execute("UPDATE patients SET hospital_id=?, ward_id=? WHERE id=?", (to_hid, to_wid, pid))
                st.success("ย้ายเรียบร้อย")
                st.rerun()

            hist = fetch_df(
                """SELECT t.moved_at AS Date,
                           h1.name AS From_hosp,
                           COALESCE(w1.name,'') AS From_ward,
                           h2.name AS To_hosp,
                           COALESCE(w2.name,'') AS To_ward,
                           t.reason AS Reason
                    FROM transfers t
                    LEFT JOIN hospitals h1 ON t.from_hospital_id=h1.id
                    LEFT JOIN wards w1 ON t.from_ward_id=w1.id
                    LEFT JOIN hospitals h2 ON t.to_hospital_id=h2.id
                    LEFT JOIN wards w2 ON t.to_ward_id=w2.id
                    WHERE t.patient_id=?
                    ORDER BY t.id DESC""",
                (pid,),
            )
            st.markdown("**ประวัติการย้าย**")
            st.dataframe(hist, use_container_width=True, hide_index=True)

        # ----- Chemo -----
        with T_Chemo:
            st.markdown("### ข้อมูลร่างกายและแผน Chemo")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                weight_kg = st.number_input(
                    "น้ำหนัก (kg)",
                    min_value=0.0,
                    max_value=300.0,
                    value=float(data.get("weight_kg") or 0.0),
                    step=0.1,
                )
            with c2:
                height_cm = st.number_input(
                    "ส่วนสูง (cm)",
                    min_value=0.0,
                    max_value=250.0,
                    value=float(data.get("height_cm") or 0.0),
                    step=0.5,
                )
            with c3:
                current_bsa = calc_bsa(weight_kg, height_cm)
                st.metric("BSA (m²)", f"{current_bsa:.2f}" if current_bsa else "-")
            with c4:
                if st.button("บันทึกข้อมูลร่างกาย"):
                    execute(
                        "UPDATE patients SET weight_kg=?, height_cm=?, bsa=? WHERE id=?",
                        (weight_kg or None, height_cm or None, current_bsa or None, pid),
                    )
                    st.success("บันทึกแล้ว")
                    st.rerun()

            tmpl_df2 = get_chemo_templates_df()
            tmpl_names = tmpl_df2["name"].tolist()
            st.markdown("---")
            st.markdown("### แผน Regimen สำหรับผู้ป่วยรายนี้")
            c5, c6, c7 = st.columns(3)
            with c5:
                regimen_default = data.get("chemo_regimen") or (tmpl_names[0] if tmpl_names else "")
                regimen_name = st.selectbox(
                    "เลือก regimen",
                    tmpl_names,
                    index=(tmpl_names.index(regimen_default) if regimen_default in tmpl_names else 0)
                    if tmpl_names
                    else 0,
                )
            with c6:
                total_cycles = st.number_input(
                    "จำนวน cycle ทั้งหมดที่วางแผน",
                    min_value=0,
                    max_value=100,
                    value=int(data.get("chemo_total_cycles") or 0),
                    step=1,
                )
            with c7:
                interval_days = st.number_input(
                    "ช่วงห่างระหว่าง cycle (วัน)",
                    min_value=0,
                    max_value=60,
                    value=int(data.get("chemo_interval_days") or 21),
                    step=1,
                )

            if st.button("บันทึกแผน Chemo สำหรับคนไข้รายนี้"):
                execute(
                    "UPDATE patients SET chemo_regimen=?, chemo_total_cycles=?, chemo_interval_days=? WHERE id=?",
                    (regimen_name, total_cycles or None, interval_days or None, pid),
                )
                st.success("บันทึกแผน Chemo แล้ว")
                st.rerun()

            st.markdown("---")
            st.markdown("### ประวัติการให้ Chemo")
            chemo_df = get_chemo_courses(pid)
            if len(chemo_df):
                st.dataframe(chemo_df, use_container_width=True, hide_index=True)
            else:
                st.info("ยังไม่มีประวัติการให้ Chemo สำหรับผู้ป่วยรายนี้")

            st.markdown("#### เพิ่ม cycle ใหม่")
            # infer next cycle number
            if len(chemo_df):
                max_cycle = int(chemo_df["Cycle"].max())
            else:
                max_cycle = 0
            next_cycle = max_cycle + 1

            c8, c9, c10 = st.columns(3)
            with c8:
                cycle_no = st.number_input("Cycle no.", min_value=1, max_value=999, value=next_cycle, step=1)
            with c9:
                given_date = st.date_input("วันที่ให้ยา", value=date.today())
            with c10:
                dose_factor = st.slider(
                    "ปรับ % dose (เช่น 0.75 = 75%)",
                    min_value=0.25,
                    max_value=1.5,
                    value=1.0,
                    step=0.05,
                )

            if st.button("คำนวณ dose และบันทึก cycle นี้"):
                if not regimen_name:
                    st.error("ยังไม่ได้ตั้ง regimen ให้คนไข้รายนี้")
                elif not weight_kg and not height_cm:
                    st.error("กรุณากรอกน้ำหนัก/ส่วนสูงอย่างน้อย 1 ค่า เพื่อคำนวณ dose")
                else:
                    rows, bsa_val = compute_doses_for_template(regimen_name, weight_kg, height_cm)
                    if not rows:
                        st.error("ไม่พบ template สำหรับ regimen นี้")
                    else:
                        for row in rows:
                            base_dose = row["dose_mg"]
                            final_dose = base_dose * dose_factor if base_dose is not None else None
                            execute(
                                """INSERT INTO chemo_courses(
                                        patient_id, cycle_no, given_date, regimen_name,
                                        drug_name, mode, dose_per_m2, dose_per_kg, fixed_dose_mg,
                                        dose_mg, dose_factor, notes
                                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    pid,
                                    int(cycle_no),
                                    given_date.isoformat(),
                                    regimen_name,
                                    row["drug_name"],
                                    row["mode"],
                                    row["dose_per_m2"],
                                    row["dose_per_kg"],
                                    row["fixed_dose_mg"],
                                    float(final_dose) if final_dose is not None else None,
                                    float(dose_factor),
                                    None,
                                ),
                            )
                        st.success("บันทึก cycle นี้เรียบร้อย")
                        st.rerun()

            st.markdown("---")
            st.markdown("### การประเมินผล (CT / PET / BM)")
            assess_df = get_chemo_assessments(pid)
            if len(assess_df):
                st.dataframe(assess_df, use_container_width=True, hide_index=True)
            else:
                st.info("ยังไม่มีการบันทึกผล CT/PET/BM")

            with st.form("add_assess_form", clear_on_submit=True):
                c11, c12, c13 = st.columns(3)
                with c11:
                    assess_cycle = st.number_input("หลัง cycle ที่", min_value=0, max_value=999, value=0, step=1)
                with c12:
                    assess_date = st.date_input("วันที่ตรวจ", value=date.today())
                with c13:
                    assess_type = st.text_input("ชนิดการตรวจ (CT, PET/CT, BM ฯลฯ)")
                response = st.text_input("Response (CR/PR/SD/PD ฯลฯ)")
                result_summary = st.text_area("สรุปผลตรวจ")
                submitted_assess = st.form_submit_button("บันทึกผลการประเมิน")
                if submitted_assess:
                    execute(
                        """INSERT INTO chemo_assessments(
                                patient_id, cycle_no, assess_date, assess_type, result_summary, response
                            ) VALUES (?,?,?,?,?,?)""",
                        (
                            pid,
                            int(assess_cycle) if assess_cycle else None,
                            assess_date.isoformat(),
                            assess_type or None,
                            result_summary or None,
                            response or None,
                        ),
                    )
                    st.success("บันทึกผลการประเมินแล้ว")
                    st.rerun()

            st.markdown("---")
            st.markdown("### Export ประวัติ Chemo")
            csv_bytes = export_chemo_csv(pid, data["patient_name"])
            st.download_button(
                "⬇️ ดาวน์โหลด Chemo history (CSV)",
                data=csv_bytes,
                file_name=f"chemo_history_{data['patient_name'].replace(' ', '_')}.csv",
                mime="text/csv",
            )


# ---------------- Sidebar: backup/restore ----------------
st.sidebar.header("💾 Backup/Restore")
if os.path.exists(DB_PATH):
    with open(DB_PATH, "rb") as f:
        st.sidebar.download_button(
            "ดาวน์โหลดฐานข้อมูล (.db)",
            data=f.read(),
            file_name="admit_planner.db",
            mime="application/octet-stream",
        )

up = st.sidebar.file_uploader("อัปโหลดฐานข้อมูล (.db) เพื่อกู้คืน", type=["db"])
if up is not None:
    with open(DB_PATH, "wb") as f:
        f.write(up.read())
    st.sidebar.success("กู้คืนฐานข้อมูลแล้ว — กด R เพื่อ refresh หน้า")
