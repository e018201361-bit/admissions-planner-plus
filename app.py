# app.py — Admissions Planner PLUS (with delete buttons for hospitals/wards)
# Notes:
# - Adds 🗑️ delete buttons in Settings/Reminders tab to remove hospitals/wards.
# - Safety: prevents deleting if there are patients in that hospital/ward.
# - If no patients, deleting a hospital will also delete all its wards.
# - Everything else is the same as the prior "full" version.

import os
import sqlite3
import smtplib
import ssl
from email.message import EmailMessage
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
import requests

DB_PATH = "admit_planner.db"
MEDIA_DIR = "media"

# ---------------- DB helpers ----------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    # master tables
    c.execute("""CREATE TABLE IF NOT EXISTS hospitals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS wards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hospital_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        UNIQUE(hospital_id, name)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS patients (
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
    )""")
    # logs / transfers / photos / settings
    c.execute("""CREATE TABLE IF NOT EXISTS rounds_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        author TEXT,
        note TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        from_hospital_id INTEGER,
        from_ward_id INTEGER,
        to_hospital_id INTEGER NOT NULL,
        to_ward_id INTEGER,
        moved_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        reason TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS patient_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        caption TEXT,
        uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()
    # seeds
    for name in ("Hospital 1", "Hospital 2", "Hospital 3"):
        c.execute("INSERT OR IGNORE INTO hospitals(name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


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


# ---------------- Query helpers ----------------
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
        """
        SELECT w.id, w.name, h.name AS hospital
        FROM wards w JOIN hospitals h ON w.hospital_id=h.id
        ORDER BY h.name, w.name
        """
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

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    q = f"""
    SELECT p.id, p.created_at AS "Date Created",
           p.patient_name AS "Patient Name", p.mrn AS "HN/MRN",
           p.age AS "Age", p.sex AS "Sex",
           h.name AS "Hospital", w.name AS "Ward",
           p.status AS "Status",
           p.planned_admit_date AS "Planned Admit Date",
           p.admit_date AS "Admit Date",
           p.bed AS "Bed", p.diagnosis AS "Diagnosis",
           p.responsible_md AS "Responsible MD",
           p.priority AS "Priority", p.precautions AS "Infection Precautions",
           p.notes AS "Notes", p.last_rounded_at AS "Last Rounded"
    FROM patients p
    LEFT JOIN hospitals h ON p.hospital_id=h.id
    LEFT JOIN wards w ON p.ward_id=w.id
    {where_clause}
    ORDER BY CASE WHEN p.status='Planned' THEN 0 ELSE 1 END,
             COALESCE(p.planned_admit_date, p.admit_date) ASC,
             p.id DESC
    """
    return fetch_df(q, tuple(params))


def get_patient_by_id(pid):
    df = fetch_df("SELECT * FROM patients WHERE id=?", (pid,))
    return df.iloc[0].to_dict() if len(df) else None


# ---------------- Notifications ----------------

def notify_line(token, message):
    try:
        resp = requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {token}"},
            data={"message": message},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def notify_email(smtp_host, smtp_port, smtp_user, smtp_pass, to_email, subject, body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, int(smtp_port), context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception:
        return False


# ---------------- UI ----------------

st.set_page_config(page_title="Admissions Planner PLUS", layout="wide")
init_db()
ensure_media_dir()

st.title("🗂️ Admissions Planner — PLUS")
st.caption("ฟีเจอร์: 🔔 แจ้งเตือน (manual), 📝 Rounds notes, 🖼️ รูปผู้ป่วย, 🔁 โยกย้ายวอร์ด/รพ., 💾 Backup/Restore DB")

tab_add, tab_planner, tab_dashboard, tab_patient, tab_settings = st.tabs(
    ["➕ เพิ่มผู้ป่วย", "📅 แผน Admit", "📊 Dashboard", "👤 รายละเอียดผู้ป่วย", "⚙️ Settings / Reminders"]
)

# ---------------- SETTINGS (incl. delete buttons) ----------------
with tab_settings:
    st.subheader("การแจ้งเตือน (กดส่งเมื่อพร้อม)")
    with st.expander("LINE Notify"):
        line_token = st.text_input("LINE Notify Token", value=get_setting("line_token", ""), type="password")
        if st.button("บันทึก Token LINE"):
            set_setting("line_token", line_token.strip())
            st.success("บันทึกแล้ว")
        st.markdown("วิธีได้ Token: https://notify-bot.line.me/my/")

    with st.expander("Email (SMTP)"):
        smtp_host = st.text_input("SMTP Host", value=get_setting("smtp_host", ""))
        smtp_port = st.text_input("SMTP Port (เช่น 465)", value=get_setting("smtp_port", "465"))
        smtp_user = st.text_input("Email ผู้ส่ง (username)", value=get_setting("smtp_user", ""))
        smtp_pass = st.text_input("รหัสผ่าน/แอปพาสเวิร์ด", value=get_setting("smtp_pass", ""), type="password")
        to_email = st.text_input("Email ผู้รับ", value=get_setting("to_email", ""))
        if st.button("บันทึกค่า Email"):
            for k, v in [("smtp_host", smtp_host), ("smtp_port", smtp_port), ("smtp_user", smtp_user), ("smtp_pass", smtp_pass), ("to_email", to_email)]:
                set_setting(k, v.strip())
            st.success("บันทึกแล้ว")

    st.subheader("ช่วงเวลา Rounds (ใช้ตรวจ Missed)")
    c1, c2 = st.columns(2)
    with c1:
        round_start = st.time_input("เริ่ม", value=pd.to_datetime(get_setting("round_start", "08:00")).time())
    with c2:
        round_end = st.time_input("สิ้นสุด", value=pd.to_datetime(get_setting("round_end", "12:00")).time())
    if st.button("บันทึกช่วงเวลา"):
        set_setting("round_start", round_start.strftime("%H:%M"))
        set_setting("round_end", round_end.strftime("%H:%M"))
        st.success("บันทึกแล้ว")

    st.subheader("โรงพยาบาลและวอร์ด")
    # ---- add hospital
    with st.form("add_hospital_form", clear_on_submit=True):
        new_hosp = st.text_input("เพิ่มชื่อโรงพยาบาล")
        submitted = st.form_submit_button("เพิ่มโรงพยาบาล")
        if submitted and new_hosp.strip():
            try:
                execute("INSERT INTO hospitals(name) VALUES (?)", (new_hosp.strip(),))
                st.success("เพิ่มโรงพยาบาลแล้ว")
            except sqlite3.IntegrityError:
                st.warning("มีโรงพยาบาลชื่อนี้อยู่แล้ว")

    # ---- list hospitals with delete buttons
    hosp_df = get_hospitals()
    if len(hosp_df):
        st.markdown("**รายชื่อโรงพยาบาล**")
        for _, r in hosp_df.iterrows():
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"`#{int(r['id'])}` — **{r['name']}**")
            with col2:
                if st.button("🗑️ ลบ", key=f"del_hosp_{int(r['id'])}"):
                    # block if patients exist under this hospital
                    cnt = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE hospital_id=?", (int(r['id']),))['c'][0]
                    if cnt > 0:
                        st.error("ลบไม่ได้: ยังมีผู้ป่วยในโรงพยาบาลนี้")
                    else:
                        # delete wards first, then hospital
                        execute("DELETE FROM wards WHERE hospital_id=?", (int(r['id']),))
                        execute("DELETE FROM hospitals WHERE id=?", (int(r['id']),))
                        st.success("ลบโรงพยาบาลเรียบร้อย — กด R เพื่อรีเฟรชหน้า")
        st.divider()
    else:
        st.info("ยังไม่มีโรงพยาบาล")

    # ---- add ward
    st.subheader("เพิ่มวอร์ด")
    hospitals = get_hospitals()
    hosp_map = dict(zip(hospitals["name"], hospitals["id"]))
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
                except sqlite3.IntegrityError:
                    st.warning("วอร์ดนี้มีอยู่แล้วในโรงพยาบาลนี้")

    # ---- list wards with delete buttons
    wards_all = get_wards()
    if len(wards_all):
        st.markdown("**รายชื่อวอร์ดทั้งหมด**")
        for _, r in wards_all.iterrows():
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"`#{int(r['id'])}` — **{r['name']}** (_{r['hospital']}_)")
            with col2:
                if st.button("🗑️ ลบ", key=f"del_ward_{int(r['id'])}"):
                    cnt = fetch_df("SELECT COUNT(*) AS c FROM patients WHERE ward_id=?", (int(r['id']),))['c'][0]
                    if cnt > 0:
                        st.error("ลบไม่ได้: ยังมีผู้ป่วยอยู่ในวอร์ดนี้")
                    else:
                        execute("DELETE FROM wards WHERE id=?", (int(r['id']),))
                        st.success("ลบวอร์ดเรียบร้อย — กด R เพื่อรีเฟรชหน้า")
    else:
        st.info("ยังไม่มีวอร์ด")

    st.subheader("🔔 ส่งแจ้งเตือน Missed Rounds ตอนนี้ (manual)")
    miss_df = fetch_df(
        """
        SELECT p.id, p.patient_name, h.name AS hospital, COALESCE(w.name,'') AS ward, p.last_rounded_at
        FROM patients p
        LEFT JOIN hospitals h ON p.hospital_id=h.id
        LEFT JOIN wards w ON p.ward_id=w.id
        WHERE p.status='Admitted'
        """
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
        col1, col2 = st.columns(2)
        with col1:
            if st.button("ส่ง LINE Notify ตอนนี้"):
                token = get_setting("line_token", "")
                if token:
                    ok = notify_line(token, "ยังไม่มีบันทึกราวนด์วันนี้สำหรับ:\n" + "\n".join(missed))
                    st.success("ส่งแล้ว" if ok else "ส่งไม่สำเร็จ (ตรวจ token/เน็ต)")
                else:
                    st.error("ยังไม่ได้ตั้งค่า LINE Token")
        with col2:
            if st.button("ส่ง Email ตอนนี้"):
                smtp_host = get_setting("smtp_host", ""); smtp_port = get_setting("smtp_port", "465")
                smtp_user = get_setting("smtp_user", ""); smtp_pass = get_setting("smtp_pass", "")
                to_email = get_setting("to_email", "")
                if all([smtp_host, smtp_port, smtp_user, smtp_pass, to_email]):
                    ok = notify_email(
                        smtp_host, smtp_port, smtp_user, smtp_pass, to_email,
                        "Missed Rounds Alert",
                        "ยังไม่มีบันทึกราวนด์วันนี้สำหรับ:\n" + "\n".join(missed),
                    )
                    st.success("ส่งแล้ว" if ok else "ส่งไม่สำเร็จ (ตรวจ SMTP)")
                else:
                    st.error("ตั้งค่า Email ไม่ครบ")
    else:
        st.info("วันนี้ครบทุกเคสแล้ว 🎉")

# ---------------- ADD PATIENT ----------------
with tab_add:
    st.subheader("เพิ่มผู้ป่วย")
    hospitals = get_hospitals(); hosp_ids = dict(zip(hospitals["name"], hospitals["id"]))
    with st.form("add_patient_form", clear_on_submit=True):
        cols1 = st.columns(3)
        with cols1[0]:
            patient_name = st.text_input("ชื่อผู้ป่วย *")
            mrn = st.text_input("HN/MRN")
            age = st.number_input("อายุ", min_value=0, max_value=120, step=1)
        with cols1[1]:
            sex = st.selectbox("เพศ", ["", "M", "F", "Other"])
            hosp = st.selectbox("โรงพยาบาล *", [""] + hospitals["name"].tolist())
            ward_id = None
            if hosp:
                wards_df = get_wards(hosp_ids[hosp]); ward_options = wards_df["name"].tolist()
                ward = st.selectbox("วอร์ด", [""] + ward_options)
                ward_id = dict(zip(ward_options, wards_df["id"])).get(ward)
            else:
                st.info("เลือกโรงพยาบาลก่อนเพื่อเลือกวอร์ด")
        with cols1[2]:
            status = st.selectbox("สถานะ", STATUSES, index=0)
            priority = st.selectbox("ลำดับความสำคัญ", PRIORITIES, index=1)
            precautions = st.selectbox("Infection Precautions", PRECAUTIONS, index=0)

        cols2 = st.columns(3)
        with cols2[0]:
            planned_date = st.date_input("Planned Admit Date", value=date.today())
            admit_date = st.date_input("Admit Date (ถ้ามี)", value=None)
        with cols2[1]:
            bed = st.text_input("เตียง (ถ้ามี)")
            diagnosis = st.text_input("Diagnosis")
        with cols2[2]:
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
                    """
                    INSERT INTO patients(
                        patient_name, mrn, age, sex, hospital_id, ward_id,
                        status, planned_admit_date, admit_date, bed, diagnosis,
                        responsible_md, priority, precautions, notes, last_rounded_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        patient_name.strip(), mrn.strip() or None, int(age) if age else None, (sex or None) if sex else None,
                        hosp_ids[hosp], ward_id, status,
                        planned_date.isoformat() if planned_date else None,
                        admit_date.isoformat() if admit_date else None,
                        bed or None, diagnosis or None, responsible_md or None,
                        priority, precautions, notes or None, None,
                    ),
                )
                st.success("เพิ่มผู้ป่วยเรียบร้อย")

# ---------------- PLANNER ----------------
with tab_planner:
    st.subheader("รายการวางแผน Admit (Planned)")
    hospitals = get_hospitals()
    hosp_filter = st.selectbox("โรงพยาบาล", ["ทั้งหมด"] + hospitals["name"].tolist(), index=0)
    ward_id_filter = None
    if hosp_filter != "ทั้งหมด":
        wards_df = get_wards(dict(zip(hospitals["name"], hospitals["id"]))[hosp_filter])
        ward_choice = st.selectbox("วอร์ด", ["ทั้งหมด"] + wards_df["name"].tolist(), index=0)
        if ward_choice != "ทั้งหมด":
            ward_id_filter = dict(zip(wards_df["name"], wards_df["id"]))[ward_choice]

    dstart, dend = st.columns(2)
    with dstart:
        start = st.date_input("เริ่มวันที่", value=date.today())
    with dend:
        end = st.date_input("ถึงวันที่", value=date.today() + timedelta(days=14))

    filters = {"planned_only": True, "date_start": start.isoformat(), "date_end": end.isoformat()}
    if hosp_filter != "ทั้งหมด":
        filters["hospital_id"] = dict(zip(hospitals["name"], hospitals["id"]))[hosp_filter]
    if ward_id_filter:
        filters["ward_id"] = ward_id_filter

    df = get_patients(filters)
    st.dataframe(df, use_container_width=True, hide_index=True)

# ---------------- DASHBOARD ----------------
with tab_dashboard:
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

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Planned (ทั้งหมด)", tot_planned)
    m2.metric("Admitted (ทั้งหมด)", tot_admitted)
    m3.metric("Discharged (ทั้งหมด)", tot_discharged)
    m4.metric("Planned (7 วันถัดไป)", planned_7d)
    m5.metric("Admitted วันนี้", admitted_today)

    st.markdown("#### แยกตามโรงพยาบาล")
    hosp_df = get_hospitals()
    rows = []
    for _, r in hosp_df.iterrows():
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

# ---------------- PATIENT DETAIL ----------------
with tab_patient:
    st.subheader("รายละเอียดผู้ป่วย / Rounds / รูป / โยกย้ายวอร์ด")
    mini = fetch_df(
        """
        SELECT p.id, p.patient_name AS name, COALESCE(p.mrn,'') AS mrn, h.name AS hosp, COALESCE(w.name,'') AS ward
        FROM patients p
        LEFT JOIN hospitals h ON p.hospital_id=h.id
        LEFT JOIN wards w ON p.ward_id=w.id
        WHERE p.status IN ('Planned','Admitted')
        ORDER BY p.id DESC
        """
    )
    if len(mini) == 0:
        st.info("ยังไม่มีผู้ป่วย (หรือทุกคนจำหน่ายแล้ว)")
    else:
        label_map = {f"{r['name']} | {r['mrn']} | {r['hosp']} | {r['ward']}": int(r["id"]) for _, r in mini.iterrows()}
        choice = st.selectbox("เลือกผู้ป่วย", list(label_map.keys()))
        pid = label_map[choice]
        data = get_patient_by_id(pid)

        st.markdown(
            f"**ชื่อ:** {data['patient_name']}  |  **HN/MRN:** {data.get('mrn','') or ''}  |  **สถานะ:** {data.get('status','')}"
        )
        st.markdown(
            f"**โรงพยาบาล/วอร์ด:** {fetch_df('SELECT name FROM hospitals WHERE id=?',(data['hospital_id'],)).squeeze()} / "
            f"{fetch_df('SELECT name FROM wards WHERE id=?',(data['ward_id'],)).squeeze() if data['ward_id'] else '-'}"
        )
        st.markdown(
            f"**เตียง:** {data.get('bed') or '-'}  |  **DX:** {data.get('diagnosis') or '-'}  |  **แพทย์:** {data.get('responsible_md') or '-'}"
        )
        st.markdown(f"**Last rounded:** {data.get('last_rounded_at') or '-'}")

        sect1, sect2, sect3 = st.tabs(["📝 Rounds notes", "🖼️ Photos", "🔁 โยกย้ายวอร์ด"])

        # Rounds notes
        with sect1:
            st.markdown("เพิ่มบันทึกราวนด์ (จะอัปเดต 'Last rounded' อัตโนมัติ)")
            with st.form("form_rounds_note", clear_on_submit=True):
                author = st.text_input("ผู้บันทึก", value="")
                note = st.text_area("บันทึกราวนด์", height=140)
                if st.form_submit_button("บันทึกบันทึกราวนด์"):
                    if not note.strip():
                        st.error("กรุณากรอกบันทึก")
                    else:
                        execute("INSERT INTO rounds_logs(patient_id, author, note) VALUES (?,?,?)", (pid, author or None, note.strip()))
                        execute("UPDATE patients SET last_rounded_at=datetime('now','localtime') WHERE id=?", (pid,))
                        st.success("บันทึกแล้ว")
            logs = fetch_df("SELECT created_at, author, note FROM rounds_logs WHERE patient_id=? ORDER BY id DESC", (pid,))
            st.dataframe(logs, use_container_width=True, hide_index=True)

        # Photos
        with sect2:
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
            gal = fetch_df(
                "SELECT id, file_path, caption, uploaded_at FROM patient_photos WHERE patient_id=? ORDER BY id DESC",
                (pid,),
            )
            if len(gal):
                for _, r in gal.iterrows():
                    st.image(r["file_path"], caption=f"{r['caption'] or ''} (อัปโหลด {r['uploaded_at']})", use_column_width=True)

        # Transfer
        with sect3:
            st.markdown("ย้ายโรงพยาบาล/วอร์ด พร้อมบันทึกประวัติ")
            hospitals = get_hospitals()
            hosp_ids = dict(zip(hospitals["name"], hospitals["id"]))
            new_hosp = st.selectbox("ย้ายไปโรงพยาบาล", hospitals["name"].tolist(), index=0)
            wards_df = get_wards(hosp_ids[new_hosp])
            new_ward = st.selectbox("ย้ายไปวอร์ด", [""] + wards_df["name"].tolist(), index=0)
            reason = st.text_input("เหตุผล/หมายเหตุการย้าย", value="")
            if st.button("ย้ายตอนนี้"):
                to_hid = hosp_ids[new_hosp]
                to_wid = dict(zip(wards_df["name"], wards_df["id"]).items())
                to_wid = dict(zip(wards_df["name"], wards_df["id"])) .get(new_ward) if new_ward else None
                execute(
                    "INSERT INTO transfers(patient_id, from_hospital_id, from_ward_id, to_hospital_id, to_ward_id, reason) VALUES (?,?,?,?,?,?)",
                    (pid, data["hospital_id"], data["ward_id"], to_hid, to_wid, reason or None),
                )
                execute("UPDATE patients SET hospital_id=?, ward_id=? WHERE id=?", (to_hid, to_wid, pid))
                st.success("ย้ายเรียบร้อย")
            hist = fetch_df(
                """
                SELECT t.moved_at, h1.name AS from_hosp, COALESCE(w1.name,'') AS from_ward,
                       h2.name AS to_hosp, COALESCE(w2.name,'') AS to_ward, t.reason
                FROM transfers t
                LEFT JOIN hospitals h1 ON t.from_hospital_id=h1.id
                LEFT JOIN wards w1 ON t.from_ward_id=w1.id
                LEFT JOIN hospitals h2 ON t.to_hospital_id=h2.id
                LEFT JOIN wards w2 ON t.to_ward_id=w2.id
                WHERE t.patient_id=?
                ORDER BY t.id DESC
                """,
                (pid,),
            )
            st.markdown("**ประวัติการย้าย**")
            st.dataframe(hist, use_container_width=True, hide_index=True)

# ---------------- Sidebar: backup/restore ----------------
st.sidebar.header("💾 Backup/Restore")
if os.path.exists(DB_PATH):
    with open(DB_PATH, "rb") as f:
        st.sidebar.download_button(
            "ดาวน์โหลดฐานข้อมูล (.db)", data=f.read(), file_name="admit_planner.db", mime="application/octet-stream"
        )
up = st.sidebar.file_uploader("อัปโหลดฐานข้อมูล (.db) เพื่อกู้คืน", type=["db"])
if up is not None:
    with open(DB_PATH, "wb") as f:
        f.write(up.read())
    st.sidebar.success("กู้คืนฐานข้อมูลแล้ว — กด R เพื่อ refresh หน้า")

