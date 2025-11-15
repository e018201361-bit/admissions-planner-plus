import sqlite3
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

import pandas as pd
import io
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
    CREATE TABLE IF NOT EXISTS chemo_drugs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        regimen_day TEXT NOT NULL,
        drug_name TEXT NOT NULL,
        dose_mg REAL,
        dose_factor REAL DEFAULT 1.0,
        notes TEXT,
        FOREIGN KEY(course_id) REFERENCES chemo_courses(id)
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
    """
    ดึงประวัติ chemo ของผู้ป่วยจาก chemo_courses
    คืนคอลัมน์ที่ show_chemo_tab ใช้:
      cycle, d1_date, regimen, day_label, drug, dose_mg, note
    """
    sql = """
    SELECT
        cycle               AS cycle,
        date                AS d1_date,   -- วันที่ D1
        regimen             AS regimen,
        'D1'                AS day_label, -- ตอนนี้มีแต่ D1 ก่อน
        drug                AS drug,
        dose_mg             AS dose_mg,
        notes               AS note
    FROM chemo_courses
    WHERE patient_id = ?
    ORDER BY cycle, date, id
    """
    return fetch_df(sql, (pid,))
    
    # ตรวจว่าตาราง chemo_cycles มีคอลัมน์อะไรบ้าง
    table_info = fetch_df("PRAGMA table_info(chemo_cycles)")
    existing_cols = set(table_info["name"].tolist())

    # เลือกเฉพาะคอลัมน์ที่มีอยู่จริง
    available = [c for c in required_cols if c in existing_cols]

    col_str = ", ".join(available)

    return fetch_df(
        f"""
        SELECT {col_str}
        FROM chemo_cycles
        WHERE patient_id = ?
        ORDER BY cycle, id
        """,
        (pid,),
    )


def add_chemo_from_df(
    pid: int,
    df: pd.DataFrame,
    cycle_no: int,
    given_date: date,
    regimen_name: str,
) -> None:
    """
    บันทึกยาเคมีบำบัด 1 cycle
    - 1 แถวใน chemo_courses = 1 drug
    - คำนวณ dose จาก mg + เปอร์เซ็นต์ แล้วเก็บทั้ง dose_mg (จริง) และ dose_factor
    """
    conn = get_conn()
    c = conn.cursor()

    for _, r in df.iterrows():
        # ---- ชื่อยา ----
        drug = str(r.get("Drug") or "").strip()
        if not drug:
            # ถ้าไม่ได้กรอกชื่อยา ข้ามแถวนี้ไป
            continue

        # ---- ขนาดยา base เป็น mg ----
        base_dose = r.get("Dose_mg")
        try:
            base_dose = float(base_dose) if base_dose not in (None, "") else None
        except (TypeError, ValueError):
            base_dose = None

        # ---- เปอร์เซ็นต์ขนาดยา (เช่น 80 = 80%) ----
        dose_pct = r.get("Dose_%")
        try:
            dose_pct = float(dose_pct) if dose_pct not in (None, "") else 100.0
        except (TypeError, ValueError):
            dose_pct = 100.0

        # คำนวณ dose จริง และ factor
        final_dose = None
        dose_factor = None
        if base_dose is not None:
            dose_factor = dose_pct / 100.0
            final_dose = base_dose * dose_factor

        # ---- note ----
        notes = str(r.get("Notes") or "").strip()

        # ---- บันทึกลง chemo_courses ----
        c.execute(
            """
            INSERT INTO chemo_courses(
                patient_id,
                cycle,
                date,
                regimen,
                drug,
                dose_mg,
                dose_factor,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                int(cycle_no),
                given_date.isoformat(),
                regimen_name or "",
                drug,
                final_dose,
                dose_factor,
                notes or None,
            ),
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
    st.header("เพิ่มผู้ป่วย (เคสที่ admit อยู่แล้ว)")

    # -------- เลือกรพ.ก่อน (นอกฟอร์ม) --------
    hospitals = fetch_df("SELECT id, name FROM hospitals ORDER BY name")
    hosp_map = {row["name"]: row["id"] for _, row in hospitals.iterrows()} if not hospitals.empty else {}
    hosp_name = st.selectbox("โรงพยาบาล *", list(hosp_map.keys()) or [""])
    hospital_id = hosp_map.get(hosp_name)

    wards = fetch_df(
        "SELECT id, name FROM wards WHERE hospital_id=? ORDER BY name",
        (hospital_id,),
    ) if hospital_id else pd.DataFrame()

    with st.form("add_patient_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        # -------- คอลัมน์ซ้าย: ข้อมูลคนไข้พื้นฐาน --------
        with col1:
            name = st.text_input("ชื่อผู้ป่วย *")
            mrn = st.text_input("HN/MRN")
            age = st.number_input("อายุ", min_value=0, max_value=120, value=60)
            sex = st.selectbox("เพศ", ["", "M", "F"])

        # -------- คอลัมน์ขวา: รพ., วอร์ด, priority, precautions --------
        with col2:
            # โหลด ward ตาม hospital_id ที่เลือกจากด้านบน
            wards = fetch_df(
                "SELECT id, name FROM wards WHERE hospital_id=? ORDER BY name",
                (hospital_id,),
            ) if hospital_id else pd.DataFrame()

            if not wards.empty:
                ward_key = f"ward_for_{hospital_id or 'none'}"
                ward_name = st.selectbox(
                    "วอร์ด",
                    [""] + wards["name"].tolist(),
                    key=ward_key,
                )
                if ward_name:
                    ward_id = int(wards.set_index("name").loc[ward_name, "id"])
                else:
                    ward_id = None
            else:
                ward_name = st.selectbox("วอร์ด", ["(ยังไม่มีวอร์ดของ รพ. นี้)"])
                ward_id = None

            # Priority & Infection precautions
            priority = st.selectbox(
                "ลำดับความสำคัญ",
                ["Low", "Medium", "High"],
                index=1,
            )
            precautions = st.selectbox(
                "Infection Precautions",
                ["None", "Droplet", "Airborne", "Contact"],
                index=0,
            )

        # -------- ส่วนล่างของฟอร์ม (เต็มหน้าจอ) --------
        bed = st.text_input("เตียง (ถ้ามี)")

        admit_date = st.date_input(
            "วันที่จะเริ่มนอน รพ. (ใช้เป็นวันวางแผน admit)",
            value=date.today(),
        )

        diagnosis = st.text_area("Diagnosis")
        responsible_md = st.text_input("Responsible MD")
        notes = st.text_area("Notes")

        # ปุ่ม 2 อัน
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            submitted = st.form_submit_button("บันทึก (Admit เลย)")
        with col_btn2:
            plan_admit = st.form_submit_button("วางแผน Admit (ยังไม่ Admit)")

    # -------- Logic ตอนกดปุ่ม นอก with st.form --------
    if submitted:
        # Admit ทันที
        if not name or not hospital_id:
            st.error("กรุณากรอกชื่อผู้ป่วยและเลือกโรงพยาบาล")
        else:
            execute(
                """
                INSERT INTO patients(
                    patient_name, mrn, age, sex,
                    hospital_id, ward_id,
                    status, planned_admit_date, admit_date, bed,
                    diagnosis, responsible_md,
                    priority, precautions, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    mrn or None,
                    int(age) if age else None,
                    sex or None,
                    hospital_id,
                    ward_id,
                    "Admitted",                 # ✅ Admit แล้ว
                    None,                       # ไม่ใช้ planned_admit_date
                    admit_date.isoformat(),     # วันที่ admit จริง
                    bed or None,
                    diagnosis or None,
                    responsible_md or None,
                    priority,
                    precautions,
                    notes or None,
                ),
            )
            st.success("บันทึกผู้ป่วย (Admitted) เรียบร้อยแล้ว")
            st.rerun()

    elif plan_admit:
        # แค่ Plan admit เฉย ๆ (ไปโผล่หน้า แผน Admit)
        if not name or not hospital_id:
            st.error("กรุณากรอกชื่อผู้ป่วยและเลือกโรงพยาบาล")
        else:
            execute(
                """
                INSERT INTO patients(
                    patient_name, mrn, age, sex,
                    hospital_id, ward_id,
                    status, planned_admit_date, admit_date, bed,
                    diagnosis, responsible_md,
                    priority, precautions, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    mrn or None,
                    int(age) if age else None,
                    sex or None,
                    hospital_id,
                    None,                       # ❗ ยังไม่กำหนด ward
                    "Planned",                  # ❗ สถานะ Planned
                    admit_date.isoformat(),     # ใช้เป็น planned_admit_date
                    None,                       # ❗ ยังไม่ Admit จริง
                    None,                       # ยังไม่ต้องระบุเตียง
                    diagnosis or None,
                    responsible_md or None,
                    priority,
                    precautions,
                    notes or None,
                ),
            )
            st.success("บันทึก 'แผน Admit' เรียบร้อยแล้ว")
            st.rerun()


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
    st.header("Dashboard")

    # ---------- ตารางบน: รายชื่อผู้ป่วยแบบละเอียด ----------
    df_detail = fetch_df("""
        SELECT
            COALESCE(h.name, '-') AS hospital,
            p.patient_name,
            COALESCE(w.name, '-') AS ward,
            p.status
        FROM patients p
        LEFT JOIN hospitals h ON p.hospital_id = h.id
        LEFT JOIN wards w     ON p.ward_id     = w.id
        ORDER BY
            CASE 
                WHEN p.status = 'Admitted' THEN 1
                WHEN p.status = 'Discharged' THEN 2
                ELSE 3
            END,
            h.name,
            p.patient_name
    """)

    if df_detail.empty:
        st.info("ยังไม่มีข้อมูลผู้ป่วย")
        return

    # ตารางแรก
    st.dataframe(df_detail, use_container_width=True)

    # ---------- ตารางล่าง: Pivot ----------
    st.subheader("สรุปตามโรงพยาบาล (Pivot)")

    df_summary = (
        df_detail
        .groupby(["hospital", "status"])
        .size()
        .reset_index(name="n")
    )

    pivot = (
        df_summary
        .pivot(index="hospital", columns="status", values="n")
        .fillna(0)
        .astype(int)
    )

    st.dataframe(pivot, use_container_width=True)
    
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

def export_patient_selector() -> int:
    """
    ใช้สำหรับหน้า Export – เลือกผู้ป่วยได้ทั้ง Admitted / Discharged
    """
    df = fetch_df(
        """
        SELECT p.id,
               p.patient_name,
               p.mrn,
               p.status,
               h.name AS hospital,
               w.name AS ward
        FROM patients p
        LEFT JOIN hospitals h ON p.hospital_id = h.id
        LEFT JOIN wards w ON p.ward_id = w.id
        WHERE p.status IN ('Admitted', 'Discharged')
        ORDER BY p.patient_name
        """
    )

    if df.empty:
        st.info("ยังไม่มีผู้ป่วยในระบบ")
        return 0

    options: dict[str, int] = {}
    for _, row in df.iterrows():
        label = (
            f"{row['patient_name']} | "
            f"{row['mrn'] or '-'} | "
            f"{row['hospital'] or ''} {row['ward'] or ''} | "
            f"{row['status']}"
        )
        options[label] = int(row["id"])

    label = st.selectbox("เลือกผู้ป่วย (ทุกสถานะ)", list(options.keys()))
    return options[label]

# ------------------------ helper: convert hospital/ward id to names ------------------------
def get_hosp_ward_names(hospital_id: int | None, ward_id: int | None) -> tuple[str, str]:
    hosp_name = "-"
    ward_name = "-"

    if hospital_id:
        df_h = fetch_df("SELECT name FROM hospitals WHERE id=?", (hospital_id,))
        if not df_h.empty:
            hosp_name = df_h.loc[0]["name"]

    if ward_id:
        df_w = fetch_df("SELECT name FROM wards WHERE id=?", (ward_id,))
        if not df_w.empty:
            ward_name = df_w.loc[0]["name"]

    return hosp_name, ward_name
# ------------------------------------------------------------------------------------------

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
    # แปลง hospital_id / ward_id → ชื่อจริง
    hosp_name, ward_name = get_hosp_ward_names(
        data.get("hospital_id"),
        data.get("ward_id"),
    )

    st.markdown(
        f"**โรงพยาบาล/วอร์ด:** {hosp_name} / {ward_name} "
        f"| **เตียง:** {data.get('bed') or '-'}"
    )
    
    st.markdown(f"**DX:** {data.get('diagnosis') or '-'} | **แพทย์:** {data.get('responsible_md') or '-'}")

    # ===== ย้ายวอร์ด / เปลี่ยนเตียง =====
    with st.expander("ย้ายวอร์ด / เปลี่ยนเตียง"):
        pid = int(data["id"])
        hosp_id = data["hospital_id"]
        current_ward_id = data.get("ward_id")

        wards = fetch_df(
            """
            SELECT id, name
            FROM wards
            WHERE hospital_id = ?
            ORDER BY name
            """,
            (hosp_id,),
        )

        if wards.empty:
            st.info("รพ.นี้ยังไม่มีข้อมูลวอร์ดในฐานข้อมูล")
        else:
            ward_map = {row["name"]: row["id"] for _, row in wards.iterrows()}
            ward_names = list(ward_map.keys())

            # default index
            default_index = 0
            if current_ward_id:
                try:
                    current_name = wards.set_index("id").loc[current_ward_id, "name"]
                    default_index = ward_names.index(current_name)
                except:
                    pass

            new_ward_name = st.selectbox("วอร์ดใหม่", ward_names, index=default_index)
            new_bed = st.text_input("เตียงใหม่", value=data.get("bed") or "")

            if st.button("บันทึกการย้ายวอร์ด / เตียง", key=f"btn_move_ward_{pid}"):
                new_ward_id = int(ward_map[new_ward_name])
                execute(
                    """
                    UPDATE patients
                    SET ward_id = ?, bed = ?
                    WHERE id = ?
                    """,
                    (new_ward_id, new_bed or None, pid),
                )
                st.success("บันทึกการย้ายวอร์ด / เตียงเรียบร้อยแล้ว")
                st.rerun()

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


    # ------------------ ประวัติการให้ยาเคมีบำบัด ------------------
    st.markdown("### ยาเคมีบำบัด (ประวัติการให้)")

    chemo_df = get_chemo_courses(pid)

    if chemo_df.empty:
        st.info("ยังไม่มีประวัติการให้เคมีบำบัด")
    else:
        # เรียงลำดับให้อ่านง่าย
        chemo_df = chemo_df.sort_values(
            ["cycle", "d1_date", "day_label", "drug"],
            kind="stable",
        )

        # ทำตารางหลักสำหรับแสดง + ดาวน์โหลด (ใช้ชื่อคอลัมน์ภาษาอังกฤษไว้ก่อน)
        df_display = chemo_df.copy()

        wanted_cols = [
            "cycle",
            "d1_date",
            "regimen",
            "day_label",
            "drug",
            "dose_mg",
            "note",
        ]
        existing = [c for c in wanted_cols if c in df_display.columns]
        df_display = df_display[existing]

        # เปลี่ยนชื่อ column ให้สวย (เวอร์ชันภาษาอังกฤษ)
        rename_map = {
            "cycle": "Cycle",
            "d1_date": "D1 date",
            "regimen": "Regimen",
            "day_label": "Day",
            "drug": "Drug",
            "dose_mg": "Dose (mg)",
            "note": "Notes",
        }
        df_display = df_display.rename(columns=rename_map)

        # -------- timeline แบบ Accordion: 1 accordion ต่อ 1 cycle --------
        max_cycle = int(chemo_df["cycle"].max())

        for (cycle, d1, reg), group in chemo_df.groupby(["cycle", "d1_date", "regimen"]):
            header = f"Cycle {int(cycle)} – D1: {d1 or '-'} – Regimen: {reg or '-'}"

            # ให้ cycle ล่าสุดขยายอยู่แล้ว ที่เหลือพับ
            expanded = (int(cycle) == max_cycle)

            with st.expander(header, expanded=expanded):
                st.dataframe(group[["day_label", "drug", "dose_mg", "note"]])

        # timeline รวมทุก cycle (option)
        with st.expander("ดูแบบ Timeline รวมทุก cycle", expanded=False):
            timeline = chemo_df[["cycle", "d1_date", "day_label", "drug", "dose_mg", "note"]].copy()
            timeline = timeline.rename(columns={
                "cycle": "Cycle",
                "d1_date": "D1 date",
                "day_label": "Day",
                "drug": "Drug",
                "dose_mg": "Dose (mg)",
                "note": "Notes",
            })
            st.dataframe(timeline, use_container_width=True)

        # เปลี่ยนชื่อหัวตารางเวอร์ชันภาษาไทยสำหรับตารางดาวน์โหลด
        rename_map = {
            "Cycle": "Cycle",
            "D1 date": "วันที่ D1",
            "Regimen": "Regimen",
            "Day": "Day",
            "Drug": "Drug",
            "Dose (mg)": "Dose (mg)",
            "Notes": "Note",
        }
        df_display = df_display.rename(columns=rename_map)

        st.dataframe(df_display, use_container_width=True)

        # ปุ่มโหลด CSV เก็บ backup / ส่งออกภายนอก
        csv_bytes = df_display.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 ดาวน์โหลดประวัติเคมีบำบัด (CSV)",
            data=csv_bytes,
            file_name=f"chemo_history_{pid}.csv",
        )

    # -----------------------------------------------------------------
    # -------------------------------
    # เพิ่ม cycle ใหม่ (บันทึกยา chemo)
    # -------------------------------
    st.markdown("### เพิ่ม cycle ใหม่ (บันทึกยาเคมีบำบัด)")

    # หาว่าเคยให้ถึง cycle ไหนแล้ว และดึงชื่อ regimen ล่าสุดมาเป็นค่าเริ่มต้น
    if not chemo_df.empty:
        max_cycle = int(chemo_df["cycle"].max())
        if chemo_df["regimen"].notna().any():
            last_regimen = str(chemo_df["regimen"].dropna().iloc[-1])
        else:
            last_regimen = ""
    else:
        max_cycle = 0
        last_regimen = ""

    next_cycle = max_cycle + 1

    col1, col2, col3 = st.columns(3)
    with col1:
        cycle_no = st.number_input(
            "Cycle no.",
            min_value=1,
            max_value=999,
            value=next_cycle,
            step=1,
            key=f"cycle_no_{pid}",
        )
    with col2:
        given_date = st.date_input(
            "วันที่ให้ยา",
            value=date.today(),
            key=f"chemo_date_{pid}",
        )
    with col3:
        regimen = st.text_input(
            "ชื่อ regimen สำหรับ cycle นี้",
            value=last_regimen,
            key=f"chemo_regimen_{pid}",
        )

    st.caption("ใส่ขนาดยาเป็น mg เอง แล้วถ้าต้องการลด/เพิ่ม % ให้กรอกที่คอลัมน์ Dose_% (เช่น 80 = 80%)")

    # ตารางให้หมอกรอกยาเอง (ต่อ 1 cycle)
    default_rows = [
        {"Drug": "", "Dose_mg": 0.0, "Dose_%": 100.0, "Notes": ""},
    ]

    manual_df = pd.DataFrame(default_rows)

    manual_df = st.data_editor(
        manual_df,
        num_rows="dynamic",
        key=f"editor_cycle_{pid}",
        use_container_width=True,
        column_config={
            "Drug": st.column_config.TextColumn("Drug"),
            "Dose_mg": st.column_config.NumberColumn(
                "Base dose (mg)",
                min_value=0.0,
                step=10.0,
            ),
            "Dose_%": st.column_config.NumberColumn(
                "Dose_% (เช่น 80 = 80%)",
                min_value=0.0,
                max_value=200.0,
                step=5.0,
            ),
            "Notes": st.column_config.TextColumn("Notes"),
        },
    )

    # คำนวน dose หลังปรับ % เพื่อให้หมอดู
    calc_df = manual_df.copy()
    # แปลง Dose_% เป็นตัวเลข ถ้าว่างให้ถือเป็น 100%
    calc_df["Dose_%"] = pd.to_numeric(calc_df["Dose_%"], errors="coerce").fillna(100.0)
    calc_df["Final_dose_mg"] = calc_df["Dose_mg"] * (calc_df["Dose_%"] / 100.0)

    st.markdown("#### Preview ขนาดยาหลังปรับ %")
    st.dataframe(calc_df, use_container_width=True)

    if st.button("บันทึก chemo cycle นี้", key=f"btn_save_cycle_{pid}"):
        # ต้องมีชื่อยาอย่างน้อย 1 ตัว
        if calc_df["Drug"].astype(str).str.strip().eq("").all():
            st.error("กรุณากรอกชื่อยาอย่างน้อย 1 drug")
        else:
            import sqlite3  # ถ้ายังไม่ได้ import ด้านบนไฟล์

            conn = get_conn()
            c = conn.cursor()
            try:
                for _, row in calc_df.iterrows():
                    drug_name = str(row["Drug"]).strip()
                    if not drug_name:
                        continue  # ข้ามแถวที่ไม่กรอกชื่อยา

                    dose_percent = float(row["Dose_%"]) if pd.notnull(row["Dose_%"]) else 100.0
                    final_dose_mg = (
                        float(row["Final_dose_mg"])
                        if pd.notnull(row["Final_dose_mg"])
                        else 0.0          # กันไม่ให้เป็น None เผื่อ dose_mg NOT NULL
                    )
                    note_text = (
                        str(row["Notes"]).strip()
                        if isinstance(row["Notes"], str) and row["Notes"]
                        else None
                    )

                    # แปลง % เป็น factor (เช่น 80% -> 0.8) เพื่อเก็บลง dose_factor
                    dose_factor = dose_percent / 100.0

                    c.execute(
                        """
                        INSERT INTO chemo_courses
                            (patient_id, cycle, date, regimen, drug, dose_mg, dose_factor, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(pid),
                            int(cycle_no),
                            given_date.isoformat(),   # ไม่ควรเป็น None
                            regimen or None,
                            drug_name,
                            final_dose_mg,
                            float(dose_factor),
                            note_text,
                        ),
                    )

                conn.commit()
                st.success("บันทึก chemo cycle นี้เรียบร้อยแล้ว")
                st.rerun()

            except sqlite3.IntegrityError as e:
                conn.rollback()
                # ตรงนี้จะเห็นข้อความจริงของ error แล้ว
                st.error(f"บันทึก chemo ไม่สำเร็จ (IntegrityError): {e}")

            finally:
                conn.close()

    # -----------------------------------------------------------------

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

def page_export_history():
    """หน้า Export ประวัติการรักษา (เน้น Chemo + ข้อมูลคนไข้)"""
    st.header("Export ประวัติการรักษา")

    # 1) เลือกผู้ป่วย (ใช้ selector ที่ดึงทั้ง Admitted + Discharged)
    pid = export_patient_selector()
    if not pid:
        return

    # 2) ดึงข้อมูลคนไข้
    data = get_patient(pid)
    if not data:
        st.error("ไม่พบข้อมูลผู้ป่วย")
        return

    # 3) แสดงข้อมูลสรุปคนไข้ด้านบน
    st.markdown(
        f"**ชื่อ:** {data['patient_name']}  "
        f"| **HN:** {data.get('mrn') or '-'}  "
        f"| **สถานะ:** {data.get('status') or '-'}"
    )

    # 4) ดึงประวัติ chemo ทั้งหมด
    chemo_df = get_chemo_courses(pid)

    if chemo_df.empty:
        st.info("ยังไม่มีประวัติให้เคมีบำบัด")
    else:
        st.subheader("ตัวอย่างประวัติการให้เคมีบำบัด")
        st.dataframe(chemo_df, use_container_width=True)

    # 5) เตรียมข้อมูลสำหรับ export เป็น Excel
    # แปลง dict ของ patient ให้เป็น DataFrame 1 แถว
    patient_df = pd.DataFrame([data])

    # ----- รวมข้อมูลเป็นตารางเดียว พร้อม column 'section' แยกส่วน -----
    export_parts = []

    # 5.1 ข้อมูลผู้ป่วย -> ทำเป็น field / value อ่านง่าย
    if not patient_df.empty:
        info = patient_df.T.reset_index()
        info.columns = ["field", "value"]   # index = ชื่อ field, ค่า = value
        info.insert(0, "section", "patient_info")
        export_parts.append(info)

    # 5.2 ประวัติ chemo (ถ้ามี)
    if not chemo_df.empty:
        df = chemo_df.copy()
        df.insert(0, "section", "chemo")
        export_parts.append(df)

    # 5.3 รวมทั้งหมดแล้ว export เป็น CSV
    if export_parts:
        export_df = pd.concat(export_parts, ignore_index=True)
        csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            "⬇️ ดาวน์โหลดแฟ้มประวัติการรักษา (CSV)",
            data=csv_bytes,
            file_name=f"treatment_history_{data['patient_name']}.csv",
            mime="text/csv",
        )

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
            "Export ประวัติการรักษา",
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
    elif page == "Export ประวัติการรักษา":
        page_export_history()
    elif page == "Settings / Reminders":
        page_settings()


if __name__ == "__main__":
    main()
