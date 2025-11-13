import sqlite3
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

import pandas as pd
import streamlit as st

DB_PATH = "admissions_planner_plus_v2.db"

CHEMO_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "CHOP": [
        {"drug": "Cyclophosphamide", "per_kg": None, "per_m2": 750, "notes": "Day 1"},
        {"drug": "Doxorubicin", "per_kg": None, "per_m2": 50, "notes": "Day 1"},
        {"drug": "Vincristine", "per_kg": None, "per_m2": 1.4, "notes": "Day 1 (max 2 mg)"},
        {"drug": "Prednisolone", "per_kg": 1, "per_m2": None, "notes": "Day 1–5"},
    ],
    "R-CHOP": [
        {"drug": "Rituximab", "per_kg": 375, "per_m2": None, "notes": "Day 1"},
    ],
    "BV-AVD": [
        {"drug": "Brentuximab vedotin", "per_kg": None, "per_m2": 1.2, "notes": "Day 1,15"},
        {"drug": "Doxorubicin", "per_kg": None, "per_m2": 25, "notes": "Day 1,15"},
        {"drug": "Vinblastine", "per_kg": None, "per_m2": 6, "notes": "Day 1,15"},
        {"drug": "Dacarbazine", "per_kg": None, "per_m2": 375, "notes": "Day 1,15"},
    ],
    "Pola-R-CHP": [
        {"drug": "Polatuzumab vedotin", "per_kg": 1.8, "per_m2": None, "notes": "Day 1"},
        {"drug": "Rituximab", "per_kg": 375, "per_m2": None, "notes": "Day 1"},
        {"drug": "Cyclophosphamide", "per_kg": None, "per_m2": 750, "notes": "Day 1"},
        {"drug": "Doxorubicin", "per_kg": None, "per_m2": 50, "notes": "Day 1"},
    ],
    "ICE": [
        {"drug": "Ifosfamide", "per_kg": None, "per_m2": 5000, "notes": "Total course"},
        {"drug": "Carboplatin", "per_kg": None, "per_m2": None, "notes": "AUC 5"},
        {"drug": "Etoposide", "per_kg": None, "per_m2": 100, "notes": "Day 1–3"},
    ],
    "DA-EPOCH-R": [
        {"drug": "Etoposide", "per_kg": None, "per_m2": 50, "notes": "Day 1–4 (cont)"},
        {"drug": "Doxorubicin", "per_kg": None, "per_m2": 10, "notes": "Day 1–4 (cont)"},
        {"drug": "Vincristine", "per_kg": None, "per_m2": 0.4, "notes": "Day 1–4 (cont)"},
        {"drug": "Cyclophosphamide", "per_kg": None, "per_m2": 750, "notes": "Day 5"},
        {"drug": "Prednisolone", "per_kg": 0.5, "per_m2": None, "notes": "Day 1–5"},
        {"drug": "Rituximab", "per_kg": 375, "per_m2": None, "notes": "Day 1 or 5"},
    ],
    "HyperCVAD": [
        {"drug": "Cyclophosphamide", "per_kg": None, "per_m2": 300, "notes": "q12h x6"},
        {"drug": "Vincristine", "per_kg": None, "per_m2": 2, "notes": "Day 4,11"},
        {"drug": "Doxorubicin", "per_kg": None, "per_m2": 50, "notes": "Day 4"},
        {"drug": "Dexamethasone", "per_kg": None, "per_m2": None, "notes": "40 mg D1–4,11–14"},
    ],
    "Daratumumab IV": [
        {"drug": "Daratumumab IV", "per_kg": 16, "per_m2": None, "notes": "fixed per kg"},
    ],
    "Daratumumab SC": [
        {"drug": "Daratumumab SC", "per_kg": None, "per_m2": None, "notes": "1800 mg fixed"},
    ],
}


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS hospitals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS wards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hospital_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        FOREIGN KEY(hospital_id) REFERENCES hospitals(id)
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS patients(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        last_rounded_at TEXT,
        weight_kg REAL,
        height_cm REAL,
        bsa REAL,
        chemo_regimen TEXT,
        chemo_total_cycles INTEGER,
        chemo_interval_days INTEGER,
        FOREIGN KEY(hospital_id) REFERENCES hospitals(id),
        FOREIGN KEY(ward_id) REFERENCES wards(id)
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS rounds(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        round_time TEXT NOT NULL,
        recorder TEXT,
        notes TEXT,
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS transfers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        from_hospital_id INTEGER,
        from_ward_id INTEGER,
        to_hospital_id INTEGER,
        to_ward_id INTEGER,
        transfer_time TEXT NOT NULL,
        notes TEXT,
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS chemo_courses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        cycle INTEGER NOT NULL,
        date TEXT NOT NULL,
        regimen TEXT NOT NULL,
        drug TEXT NOT NULL,
        dose_mg REAL,
        dose_factor REAL,
        notes TEXT,
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS chemo_assessments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        cycle_no INTEGER,
        assess_date TEXT NOT NULL,
        assess_type TEXT,
        result_summary TEXT,
        response TEXT,
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    )
    """
    )
    conn.commit()
    # seed default hospital if none
    c.execute("SELECT COUNT(*) FROM hospitals")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO hospitals(name) VALUES (?)", ("ศิริราช",))
        conn.commit()
    conn.close()


from typing import Any   # ถ้ายังไม่มี import นี้ อยู่บน ๆ ไฟล์เพิ่มบรรทัดนี้ด้วย

def fetch_df(sql: str, params: Any = None) -> pd.DataFrame:
    """
    อ่านข้อมูลจาก SQLite แบบกันตาย:
    - ถ้าฐานข้อมูลยังไม่มี table / column หรือโครงสร้างไม่ตรง -> คืน DataFrame ว่าง
    - โชว์ warning บนหน้าเว็บ แต่ไม่ทำให้แอปล่ม
    """
    conn = get_conn()
    try:
        df = pd.read_sql_query(sql, conn, params=params)
        return df
    except Exception as e:
        st.warning(f"⚠️ Database error (fetch_df): {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> None:
    conn = get_conn()
    c = conn.cursor()
    c.execute(sql, params)
    conn.commit()
    conn.close()


def calc_bsa(weight_kg: float, height_cm: float) -> float:
    if not weight_kg or not height_cm:
        return 0.0
    return (weight_kg * height_cm / 3600) ** 0.5


def get_patient(pid: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM patients WHERE id=?", (pid,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_chemo_courses(pid: int) -> pd.DataFrame:
    return fetch_df(
        "SELECT cycle, date, regimen, drug, dose_mg, dose_factor, notes "
        "FROM chemo_courses WHERE patient_id=? ORDER BY cycle, id",
        (pid,),
    )


def add_chemo_from_df(pid: int, df: pd.DataFrame, cycle_no: int, given_date: date, regimen_name: str):
    conn = get_conn()
    c = conn.cursor()
    for _, r in df.iterrows():
        drug = str(r.get("Drug") or "").strip()
        if not drug:
            continue
        dose_mg = float(r.get("Dose_mg") or 0)
        dose_factor = float(r.get("Dose_factor") or 1)
        notes = str(r.get("Notes") or "")
        c.execute(
            """
            INSERT INTO chemo_courses(patient_id, cycle, date, regimen, drug, dose_mg, dose_factor, notes)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (pid, cycle_no, given_date.isoformat(), regimen_name, drug, dose_mg, dose_factor, notes),
        )
    conn.commit()
    conn.close()


def export_chemo_csv(pid: int, patient_name: str) -> bytes:
    df = get_chemo_courses(pid)
    if df.empty:
        return b""
    df.insert(0, "Patient", patient_name)
    return df.to_csv(index=False).encode("utf-8")


# -------------- Streamlit UI ----------------


def page_add_patient():
    st.header("เพิ่มผู้ป่วย")
    with st.form("add_patient_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("ชื่อผู้ป่วย *")
            mrn = st.text_input("HN/MRN")
            age = st.number_input("อายุ", min_value=0, max_value=120, value=60)
            sex = st.selectbox("เพศ", ["", "M", "F"])
        with col2:
            hospitals = fetch_df("SELECT id, name FROM hospitals ORDER BY name")
            hosp_map = {row["name"]: row["id"] for _, row in hospitals.iterrows()}
            hosp_name = st.selectbox("โรงพยาบาล *", list(hosp_map.keys()) or [""])
            hospital_id = hosp_map.get(hosp_name)

            wards = fetch_df("SELECT id, name FROM wards WHERE hospital_id=? ORDER BY name", (hospital_id,)) if hospital_id else pd.DataFrame()
            ward_name = st.selectbox("วอร์ด", [""] + wards["name"].tolist()) if not wards.empty else st.selectbox("วอร์ด", [""])
            ward_id = None
            if not wards.empty and ward_name:
                ward_id = int(wards.set_index("name").loc[ward_name, "id"])

            priority = st.selectbox("ลำดับความสำคัญ", ["Low", "Medium", "High"], index=1)
            precautions = st.selectbox("Infection Precautions", ["None", "Droplet", "Airborne", "Contact"], index=0)

        bed = st.text_input("เตียง (ถ้ามี)")
        planned_date = st.date_input("Planned Admit Date", value=date.today())
        diagnosis = st.text_area("Diagnosis")
        responsible_md = st.text_input("Responsible MD")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("บันทึก")
        if submitted:
            if not name or not hospital_id:
                st.error("กรุณากรอกชื่อผู้ป่วยและเลือกโรงพยาบาล")
            else:
                execute(
                    """
                    INSERT INTO patients(
                        patient_name, mrn, age, sex,
                        hospital_id, ward_id,
                        status, planned_admit_date, bed,
                        diagnosis, responsible_md,
                        priority, precautions, notes
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        name, mrn or None, int(age) if age else None, sex or None,
                        hospital_id, ward_id,
                        "Planned", planned_date.isoformat(), bed or None,
                        diagnosis or None, responsible_md or None,
                        priority, precautions, notes or None,
                    ),
                )
                st.success("บันทึกผู้ป่วยเรียบร้อยแล้ว")


def page_plan_admit():
    st.header("แผน Admit")
    df = fetch_df(
        """
        SELECT p.id, p.patient_name, p.mrn,
               p.planned_admit_date, p.hospital_id, p.ward_id, p.status,
               h.name AS hospital, w.name AS ward
        FROM patients p
        LEFT JOIN hospitals h ON p.hospital_id=h.id
        LEFT JOIN wards w ON p.ward_id=w.id
        WHERE p.status='Planned'
        ORDER BY p.planned_admit_date, p.patient_name
        """
    )
    if df.empty:
        st.info("ยังไม่มีผู้ป่วยที่วางแผน admit")
        return
    for _, row in df.iterrows():
        with st.expander(f"{row['planned_admit_date']} — {row['patient_name']} ({row.get('hospital') or ''} {row.get('ward') or ''})"):
            st.write(f"HN: {row['mrn'] or '-'}")
            if st.button("Admit แล้ววันนี้", key=f"btn_admit_{row['id']}"):
                execute(
                    "UPDATE patients SET status='Admitted', admit_date=?, planned_admit_date=NULL WHERE id=?",
                    (date.today().isoformat(), int(row["id"])),
                )
                st.success("อัปเดตเป็น Admitted แล้ว")
                st.rerun()


def sidebar_backup():
    st.sidebar.markdown("### 💾 Backup/Restore")
    import os

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


def page_dashboard():
    st.header("Dashboard (อย่างง่าย)")
    df = fetch_df("SELECT status, COUNT(*) as n FROM patients GROUP BY status")
    if df.empty:
        st.info("ยังไม่มีข้อมูลผู้ป่วย")
        return
    st.dataframe(df, use_container_width=True)


def patient_selector() -> int:
    df = fetch_df(
        """
        SELECT p.id, patient_name, mrn, status, h.name AS hospital, w.name AS ward
        FROM patients p
        LEFT JOIN hospitals h ON p.hospital_id=h.id
        LEFT JOIN wards w ON p.ward_id=w.id
        WHERE p.status = 'Admitted'
        ORDER BY w.name, patient_name
        """
    )
    if df.empty:
        st.info("ยังไม่มีผู้ป่วย")
        return 0
    options = {
        f"{row['patient_name']} | {row['mrn'] or '-'} | {row['hospital'] or ''} {row['ward'] or ''} | {row['status']}": int(row["id"])
        for _, row in df.iterrows()
    }
    label = st.selectbox("เลือกผู้ป่วย", list(options.keys()))
    return options[label]


def page_patient_detail():
    st.header("รายละเอียดผู้ป่วย / Rounds / Chemo / D/C")
    pid = patient_selector()
    if not pid:
        return
    data = get_patient(pid)
    if not data:
        st.error("ไม่พบข้อมูลผู้ป่วย")
        return

    st.markdown(
        f"**ชื่อ:** {data['patient_name']}  |  **HN:** {data.get('mrn') or '-'}  "
        f"|  **สถานะ:** {data.get('status') or '-'}"
    )
    st.markdown(
        f"**โรงพยาบาล/วอร์ด:** {data.get('hospital_id') or '-'} / {data.get('ward_id') or '-'}  "
        f"| **เตียง:** {data.get('bed') or '-'}"
    )
    st.markdown(f"**DX:** {data.get('diagnosis') or '-'} | **แพทย์:** {data.get('responsible_md') or '-'}")

    tabs = st.tabs(["Rounds notes", "Chemo", "D/C & Next plan"])
    with tabs[0]:
        show_rounds_tab(pid)
    with tabs[1]:
        show_chemo_tab(pid, data)
    with tabs[2]:
        show_dc_tab(pid, data)


def show_rounds_tab(pid: int):
    st.subheader("บันทึกการ round")
    df = fetch_df(
        "SELECT round_time, recorder, notes FROM rounds WHERE patient_id=? ORDER BY round_time DESC",
        (pid,),
    )
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    with st.form("add_round_form", clear_on_submit=True):
        recorder = st.text_input("ผู้บันทึก")
        notes = st.text_area("บันทึกรอบนี้ (จะอัปเดต 'Last rounded' อัตโนมัติ)")
        submitted = st.form_submit_button("บันทึกบันทึกการ round")
        if submitted:
            now = datetime.now().isoformat(timespec="seconds")
            execute(
                "INSERT INTO rounds(patient_id, round_time, recorder, notes) VALUES (?,?,?,?)",
                (pid, now, recorder or None, notes or None),
            )
            execute(
                "UPDATE patients SET last_rounded_at=? WHERE id=?",
                (now, pid),
            )
            st.success("บันทึก round แล้ว")
            st.rerun()


def show_chemo_tab(pid: int, data: dict):
    st.subheader("ข้อมูลร่างกาย (สำหรับคำนวณ dose)")
    col1, col2, col3 = st.columns(3)
    with col1:
        weight = st.number_input("น้ำหนัก (kg)", min_value=0.0, max_value=300.0, value=float(data.get("weight_kg") or 0))
    with col2:
        height = st.number_input("ส่วนสูง (cm)", min_value=0.0, max_value=250.0, value=float(data.get("height_cm") or 0))
    with col3:
        bsa = calc_bsa(weight, height)
        st.metric("BSA (m²)", f"{bsa:.2f}" if bsa else "-")
    if st.button("บันทึกข้อมูลร่างกาย", key="btn_save_body"):
        execute(
            "UPDATE patients SET weight_kg=?, height_cm=?, bsa=? WHERE id=?",
            (weight or None, height or None, bsa or None, pid),
        )
        st.success("บันทึกแล้ว")

    st.markdown("### แผน Regimen สำหรับผู้ป่วยรายนี้")
    regimen_options = ["<พิมพ์ชื่อเอง>"] + sorted(CHEMO_TEMPLATES.keys())
    regimen_sel = st.selectbox("เลือก regimen", regimen_options)
    if regimen_sel == "<พิมพ์ชื่อเอง>":
        regimen_name = st.text_input("พิมพ์ชื่อ regimen เอง", key="regimen_manual")
    else:
        regimen_name = regimen_sel

    total_cycles = st.number_input("จำนวน cycle ทั้งหมดที่วางแผน", min_value=1, max_value=40, value=int(data.get("chemo_total_cycles") or 6))
    interval_days = st.number_input("ช่วงห่างระหว่าง cycle (วัน)", min_value=1, max_value=365, value=int(data.get("chemo_interval_days") or 21))
    if st.button("บันทึกแผน Chemo สำหรับคนไข้รายนี้", key="btn_save_chemo_plan"):
        execute(
            "UPDATE patients SET chemo_regimen=?, chemo_total_cycles=?, chemo_interval_days=? WHERE id=?",
            (regimen_name or None, int(total_cycles), int(interval_days), pid),
        )
        st.success("บันทึกแผน chemo แล้ว")

    st.markdown("### ประวัติการให้ Chemo")
    chemo_df = get_chemo_courses(pid)
    if chemo_df.empty:
        st.info("ยังไม่มีประวัติการให้ chemo")
    else:
        st.dataframe(chemo_df, use_container_width=True)
        csv_bytes = export_chemo_csv(pid, data["patient_name"])
        st.download_button("📥 ดาวน์โหลด Chemo history (CSV)", data=csv_bytes, file_name=f"chemo_history_{data['patient_name'].replace(' ', '_')}.csv", mime="text/csv")

    st.markdown("### เพิ่ม cycle ใหม่ (Hybrid: template + ปรับ dose manual)")
    if not chemo_df.empty:
        max_cycle = int(chemo_df["cycle"].max())
    else:
        max_cycle = 0
    next_cycle = max_cycle + 1
    colc1, colc2, colc3 = st.columns(3)
    with colc1:
        cycle_no = st.number_input("Cycle no.", min_value=1, max_value=999, value=next_cycle, step=1)
    with colc2:
        given_date = st.date_input("วันที่ให้ยา", value=date.today(), key=f"given_date_{pid}")
    with colc3:
        reg_for_cycle = st.text_input("ชื่อ regimen สำหรับ cycle นี้", value=regimen_name or "", key=f"cycle_regimen_{pid}")

    st.markdown("#### เลือก template หรือสร้าง manual")
    mode = st.radio("โหมด", ["ใช้ template", "คัดลอกจาก cycle ก่อนหน้า", "Manual ไม่มี template"], horizontal=True)
    default_rows: List[Dict[str, Any]] = []

    if mode == "ใช้ template":
        tmpl_key = regimen_sel if regimen_sel != "<พิมพ์ชื่อเอง>" else None
        tmpl = CHEMO_TEMPLATES.get(tmpl_key or "", [])
        for row in tmpl:
            per_kg = row.get("per_kg")
            per_m2 = row.get("per_m2")
            if per_kg and weight:
                dose = per_kg * weight
            elif per_m2 and bsa:
                dose = per_m2 * bsa
            else:
                dose = 0
            default_rows.append(
                {
                    "Drug": row["drug"],
                    "Dose_mg": round(dose, 1) if dose else 0,
                    "Dose_factor": 1.0,
                    "Notes": row.get("notes", ""),
                }
            )
    elif mode == "คัดลอกจาก cycle ก่อนหน้า" and not chemo_df.empty:
        prev = chemo_df[chemo_df["cycle"] == max_cycle]
        for _, r in prev.iterrows():
            default_rows.append(
                {
                    "Drug": r["drug"],
                    "Dose_mg": r["dose_mg"],
                    "Dose_factor": 1.0,
                    "Notes": r.get("notes", ""),
                }
            )
    else:
        default_rows.append({"Drug": "", "Dose_mg": 0.0, "Dose_factor": 1.0, "Notes": ""})

    manual_df = pd.DataFrame(default_rows)
    manual_df = st.data_editor(manual_df, num_rows="dynamic", key=f"editor_cycle_{pid}", use_container_width=True)

    if st.button("บันทึก chemo cycle นี้", key=f"btn_save_cycle_{pid}"):
        if manual_df["Drug"].astype(str).str.strip().eq("").all():
            st.error("กรุณากรอกอย่างน้อย 1 drug")
        else:
            add_chemo_from_df(pid, manual_df, int(cycle_no), given_date, reg_for_cycle or regimen_name or "")
            st.success("บันทึก chemo cycle นี้เรียบร้อย (dose แต่ละตัวจะใช้เป็นฐานสำหรับ cycle ถัดไป)")
            st.rerun()


def show_dc_tab(pid: int, data: dict):
    st.subheader("แผนจัดการผู้ป่วย (D/C และรอบถัดไป)")
    st.info(f"สถานะปัจจุบัน: **{data.get('status','-')}**")

    dc_date = st.date_input("วันที่ D/C", value=date.today(), key=f"dc_date_{pid}")
    plan_type = st.radio("แผนต่อไปหลัง D/C", ["F/U OPD", "นัด admit รอบถัดไป"], horizontal=True)

    next_admit_date = None
    plan_opd_text = ""
    weeks_from_now = 0

    if plan_type == "F/U OPD":
        plan_opd_text = st.text_area("รายละเอียด F/U OPD (เช่น นัด OPD 3 เดือน, CBC q1m ฯลฯ)")
    else:
        mode = st.radio("เลือกวิธีคำนวณวันที่ admit รอบถัดไป", ["เลือกวันที่เอง", "ระบุจำนวนสัปดาห์จากวัน D/C"], horizontal=True)
        if mode == "เลือกวันที่เอง":
            next_admit_date = st.date_input("วันที่ admit รอบถัดไป", value=dc_date + timedelta(days=21), key=f"next_date_direct_{pid}")
        else:
            weeks_from_now = st.number_input("อีกกี่สัปดาห์จากวัน D/C", min_value=1, max_value=52, value=3, step=1, key=f"weeks_from_dc_{pid}")
            next_admit_date = dc_date + timedelta(weeks=int(weeks_from_now))
        st.write(f"วันที่ admit รอบถัดไป: **{next_admit_date}**")

    st.markdown("---")
    if plan_type == "F/U OPD":
        if st.button("บันทึก D/C และแผน F/U OPD", key=f"btn_dc_opd_{pid}"):
            extra_note = f"[D/C {dc_date.isoformat()}] F/U OPD: {plan_opd_text}\n"
            execute(
                """
                UPDATE patients
                SET status='Discharged',
                    notes = COALESCE(notes,'') || ?
                WHERE id=?
                """,
                (extra_note, pid),
            )
            st.success("บันทึก D/C และแผน F/U OPD แล้ว (เคสนี้จะไม่อยู่ในรายชื่อที่ต้อง round อีก)")
            st.rerun()
    else:
        if st.button("บันทึก D/C และสร้างแผน admit รอบถัดไป", key=f"btn_dc_next_{pid}"):
            if not next_admit_date:
                st.error("ยังไม่ได้กำหนดวันที่ admit รอบถัดไป")
            else:
                extra_note = (
                    f"[D/C {dc_date.isoformat()}] Planned readmit on {next_admit_date.isoformat()}\n"
                )
                execute(
                    """
                    UPDATE patients
                    SET status='Discharged',
                        notes = COALESCE(notes,'') || ?
                    WHERE id=?
                    """,
                    (extra_note, pid),
                )
                # create new planned admission with same info
                execute(
                    """
                    INSERT INTO patients(
                        patient_name, mrn, age, sex,
                        hospital_id, ward_id,
                        status, planned_admit_date, admit_date,
                        bed, diagnosis, responsible_md,
                        priority, precautions, notes,
                        weight_kg, height_cm, bsa,
                        chemo_regimen, chemo_total_cycles, chemo_interval_days
                    )
                    SELECT
                        patient_name, mrn, age, sex,
                        hospital_id, ward_id,
                        'Planned', ?, NULL,
                        bed, diagnosis, responsible_md,
                        priority, precautions,
                        COALESCE(notes,'') || '\n[Auto-planned readmit from id ' || id || ']',
                        weight_kg, height_cm, bsa,
                        chemo_regimen, chemo_total_cycles, chemo_interval_days
                    FROM patients WHERE id=?
                    """,
                    (next_admit_date.isoformat(), pid),
                )
                st.success("บันทึก D/C และสร้างรายการ Planned admit รอบถัดไปแล้ว")
                st.rerun()


def page_settings():
    st.header("Settings / Reminders")
    st.markdown("## จัดการโรงพยาบาลและวอร์ด")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### โรงพยาบาล")
        with st.form("add_hospital_form", clear_on_submit=True):
            name = st.text_input("ชื่อโรงพยาบาลใหม่")
            submitted = st.form_submit_button("เพิ่มโรงพยาบาล")
            if submitted and name:
                try:
                    execute("INSERT INTO hospitals(name) VALUES (?)", (name,))
                    st.success("เพิ่มโรงพยาบาลแล้ว")
                except sqlite3.IntegrityError:
                    st.error("มีโรงพยาบาลชื่อนี้อยู่แล้ว")
        hosp_df = fetch_df("SELECT id, name FROM hospitals ORDER BY name")
        st.dataframe(hosp_df, use_container_width=True)
    with col2:
        st.markdown("### วอร์ด")
        hosp_df = fetch_df("SELECT id, name FROM hospitals ORDER BY name")
        if hosp_df.empty:
            st.info("ยังไม่มีโรงพยาบาล")
        else:
            hosp_map = {row["name"]: row["id"] for _, row in hosp_df.iterrows()}
            hosp_name = st.selectbox("เลือกโรงพยาบาลเพื่อเพิ่มวอร์ด", list(hosp_map.keys()))
            hospital_id = hosp_map[hosp_name]
            with st.form("add_ward_form", clear_on_submit=True):
                ward_name = st.text_input("ชื่อวอร์ด")
                submitted = st.form_submit_button("เพิ่มวอร์ด")
                if submitted and ward_name:
                    execute("INSERT INTO wards(hospital_id, name) VALUES (?,?)", (hospital_id, ward_name))
                    st.success("เพิ่มวอร์ดแล้ว")
            ward_df = fetch_df(
                "SELECT w.id, w.name FROM wards w WHERE w.hospital_id=? ORDER BY w.name",
                (hospital_id,),
            )
            st.dataframe(ward_df, use_container_width=True)


def main():
    st.set_page_config(page_title="Admissions Planner — PLUS (Chemo Hybrid + D/C workflow)", layout="wide")
    init_db()
    sidebar_backup()

    st.title("Admissions Planner — PLUS (Chemo Hybrid + Discharge)")

    page = st.sidebar.radio(
        "ไปหน้า...",
        [
            "เพิ่มผู้ป่วย",
            "แผน Admit",
            "Dashboard",
            "รายละเอียดผู้ป่วย / Rounds / Chemo / D/C",
            "Settings / Reminders",
        ],
    )

    if page == "เพิ่มผู้ป่วย":
        page_add_patient()
    elif page == "แผน Admit":
        page_plan_admit()
    elif page == "Dashboard":
        page_dashboard()
    elif page == "รายละเอียดผู้ป่วย / Rounds / Chemo / D/C":
        page_patient_detail()
    elif page == "Settings / Reminders":
        page_settings()


if __name__ == "__main__":
    main()
