
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session, send_from_directory
import sqlite3, os, re, json, shutil, zipfile, tempfile, pandas as pd
from datetime import date, datetime
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Profile 1 keeps the original database name so existing data is preserved.
LEGACY_DB_PATH = os.path.join(BASE_DIR, "mandi.db")
PROFILE_REGISTRY_DB = os.path.join(BASE_DIR, "profiles.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_ATTACHMENT_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "pdf"}

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

def registry_db():
    con = sqlite3.connect(PROFILE_REGISTRY_DB)
    con.row_factory = sqlite3.Row
    return con

def init_profile_registry():
    con = registry_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            db_filename TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    row = con.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()
    if row is None:
        con.execute("INSERT INTO profiles (name, db_filename) VALUES (?, ?)", ("Mandi Account 1", "mandi.db"))
    else:
        # Always make sure the first/original profile continues to use mandi.db.
        con.execute("UPDATE profiles SET db_filename=? WHERE id=?", ("mandi.db", row["id"]))
    con.commit()
    con.close()

def get_profiles():
    con = registry_db()
    rows = con.execute("SELECT * FROM profiles ORDER BY id").fetchall()
    con.close()
    return rows

def get_profile(profile_id):
    con = registry_db()
    row = con.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
    con.close()
    return row

def current_profile():
    profiles = get_profiles()
    if not profiles:
        return None
    pid = session.get("profile_id")
    profile = get_profile(pid) if pid else None
    if profile is None:
        profile = profiles[0]
        session["profile_id"] = profile["id"]
    return profile

def db_path_for_profile(profile):
    return os.path.join(BASE_DIR, profile["db_filename"])

def db():
    profile = current_profile()
    path = db_path_for_profile(profile) if profile else LEGACY_DB_PATH
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con

def init_db(path=LEGACY_DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL DEFAULT '',
            mandi TEXT,
            grain TEXT NOT NULL,
            variety TEXT,
            seller TEXT,
            quantity_quintal REAL NOT NULL DEFAULT 0,
            rate REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            quality TEXT,
            notes TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row INTEGER,
            mandi_fee REAL NOT NULL DEFAULT 0,
            nirashrit_fee REAL NOT NULL DEFAULT 0,
            transport_cost REAL NOT NULL DEFAULT 0,
            labour_cost REAL NOT NULL DEFAULT 0,
            other_cost REAL NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS purchase_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE
        )
    """)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_date TEXT NOT NULL DEFAULT '',
            grain TEXT NOT NULL,
            variety TEXT,
            buyer TEXT,
            quantity_quintal REAL NOT NULL DEFAULT 0,
            rate REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Upgrade databases from the original version without deleting existing data.
    cols = {r["name"] for r in con.execute("PRAGMA table_info(purchases)").fetchall()}
    upgrades = {
        "source_sheet": "ALTER TABLE purchases ADD COLUMN source_sheet TEXT",
        "source_row": "ALTER TABLE purchases ADD COLUMN source_row INTEGER",
        "mandi_fee": "ALTER TABLE purchases ADD COLUMN mandi_fee REAL NOT NULL DEFAULT 0",
        "nirashrit_fee": "ALTER TABLE purchases ADD COLUMN nirashrit_fee REAL NOT NULL DEFAULT 0",
        "transport_cost": "ALTER TABLE purchases ADD COLUMN transport_cost REAL NOT NULL DEFAULT 0",
        "labour_cost": "ALTER TABLE purchases ADD COLUMN labour_cost REAL NOT NULL DEFAULT 0",
        "other_cost": "ALTER TABLE purchases ADD COLUMN other_cost REAL NOT NULL DEFAULT 0",
    }
    for col, sql in upgrades.items():
        if col not in cols:
            con.execute(sql)
    con.commit()
    con.close()

def norm(v):
    s = "" if v is None else str(v)
    s = s.replace("\n", " ").replace("\r", " ").strip().lower()
    s = re.sub(r"[\s_\-–—/\\()\[\]{}.,:;]+", "", s)
    return s

COLUMN_ALIASES = {
    "date": [
        "भुगतान-पत्रक दिनाँक","भुगतान-पत्रक दिनांक","भुगतान पत्रक दिनाँक",
        "भुगतान पत्रक दिनांक","भुगतान-दिनाँक","दिनाँक","दिनांक","तारीख",
        "date","purchase date","payment date"
    ],
    "mandi": ["मंडी","मण्डी","mandi","market"],
    "grain": [
        "कृषि उपज","उपज","फसल","जिंस","अनाज","commodity","crop","grain","product"
    ],
    "variety": ["किस्म","वैरायटी","प्रकार","variety","grade"],
    "seller": ["किसान","विक्रेता","seller","farmer","vendor","party"],
    "quantity_quintal": [
        "वास्तविक वजन","वास्तविकवजन","कुल वजन","वजन","मात्रा","quantity",
        "qty","quintal","quintals","qtl","क्विंटल","actual weight"
    ],
    "rate": [
        "नीलामी दर","नीलामीदर","दर","भाव","रेट","rate","auction rate",
        "price","price per quintal","rate per quintal"
    ],
    "amount": [
        "कुल मूल्य","कुलमूल्य","कुल रकम","कुलरकम","रकम","total","total value",
        "total amount","amount","value"
    ],
    "quality": ["गुणवत्ता","quality"],
    "notes": ["टिप्पणी","remarks","remark","notes"],
    "mandi_fee": ["मंडी फीस(रु.में)","मंडी फीस","मंडीफीस","mandi fee","mandi_fee"],
    "nirashrit_fee": [
        "निराश्रित फीस(रु.में)","निराश्रित फीस","निराश्रितफीस",
        "nirashrit fee","nirashrit_fee"
    ],
}

def find_header_row(raw):
    """Find the actual table header even when report/title rows are above it."""
    best_row, best_score = None, -1
    aliases = {norm(a) for vals in COLUMN_ALIASES.values() for a in vals}
    for i in range(min(len(raw), 50)):
        vals = [norm(x) for x in raw.iloc[i].tolist()]
        score = 0
        for v in vals:
            if not v:
                continue
            if v in aliases:
                score += 3
            elif any(a and (a in v or v in a) for a in aliases):
                score += 1
        if score > best_score:
            best_score, best_row = score, i
    return best_row if best_score >= 3 else None

def find_column(columns, aliases):
    # Exact normalized match first.
    for col in columns:
        if norm(col) in {norm(a) for a in aliases}:
            return col
    # Then substring match.
    for col in columns:
        nc = norm(col)
        if any(norm(a) and (norm(a) in nc or nc in norm(a)) for a in aliases):
            return col
    return None

def to_number(value, default=0.0):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if pd.isna(value):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else default

def parse_date(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return ""
    try:
        return pd.to_datetime(value, dayfirst=True, errors="raise").strftime("%Y-%m-%d")
    except Exception:
        return str(value).strip()

def clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()

def is_footer(grain):
    g = norm(grain)
    return (not g or g in {"nan","none"} or
            "total" in g or "योग" in str(grain) or "कुल" in str(grain))

def row_exists(con, source_file, source_sheet, purchase_date, grain, quantity, rate):
    row = con.execute("""
        SELECT id FROM purchases
        WHERE source_file=? AND source_sheet=?
          AND purchase_date=? AND grain=?
          AND ABS(quantity_quintal-?) < 0.000001
          AND ABS(rate-?) < 0.000001
        LIMIT 1
    """, (source_file, source_sheet, purchase_date, grain, quantity, rate)).fetchone()
    return row is not None

def import_excel(path, source_name):
    xls = pd.ExcelFile(path)
    inserted = 0
    duplicates = 0
    skipped = 0
    con = db()

    for sheet in xls.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
        if raw.empty:
            continue

        header_row = find_header_row(raw)
        if header_row is None:
            skipped += max(0, len(raw)-1)
            continue

        headers = [clean_text(x) for x in raw.iloc[header_row].tolist()]
        # Make duplicate/blank headers safe and unique.
        seen = {}
        safe_headers = []
        for idx, h in enumerate(headers):
            base = h or f"Unnamed_{idx}"
            n = seen.get(base, 0)
            seen[base] = n + 1
            safe_headers.append(base if n == 0 else f"{base}.{n}")
        df = raw.iloc[header_row + 1:].copy()
        df.columns = safe_headers
        df = df.dropna(how="all")

        mapped = {}
        for target, aliases in COLUMN_ALIASES.items():
            col = find_column(df.columns, aliases)
            if col:
                mapped[target] = col

        required = ["grain", "quantity_quintal", "rate"]
        if not all(mapped.get(k) for k in required):
            skipped += len(df)
            continue

        for excel_row_num, (_, r) in enumerate(df.iterrows(), start=header_row + 2):
            grain = clean_text(r.get(mapped["grain"], ""))
            if is_footer(grain):
                skipped += 1
                continue

            q = to_number(r.get(mapped["quantity_quintal"], 0))
            rate = to_number(r.get(mapped["rate"], 0))
            if q <= 0 or rate <= 0:
                skipped += 1
                continue

            purchase_date = parse_date(r.get(mapped.get("date"), "")) if mapped.get("date") else ""
            amount = to_number(r.get(mapped.get("amount"), 0)) if mapped.get("amount") else 0
            if amount <= 0:
                amount = q * rate

            mandi = clean_text(r.get(mapped.get("mandi"), "")) if mapped.get("mandi") else ""
            variety = clean_text(r.get(mapped.get("variety"), "")) if mapped.get("variety") else ""
            seller = clean_text(r.get(mapped.get("seller"), "")) if mapped.get("seller") else ""
            quality = clean_text(r.get(mapped.get("quality"), "")) if mapped.get("quality") else ""
            notes = clean_text(r.get(mapped.get("notes"), "")) if mapped.get("notes") else ""
            mandi_fee = to_number(r.get(mapped.get("mandi_fee"), 0)) if mapped.get("mandi_fee") else 0
            nirashrit_fee = to_number(r.get(mapped.get("nirashrit_fee"), 0)) if mapped.get("nirashrit_fee") else 0

            if row_exists(con, source_name, sheet, purchase_date, grain, q, rate):
                duplicates += 1
                continue

            con.execute("""
                INSERT INTO purchases
                (purchase_date, mandi, grain, variety, seller, quantity_quintal,
                 rate, amount, quality, notes, source_file, source_sheet, source_row,
                 mandi_fee, nirashrit_fee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                purchase_date, mandi, grain, variety, seller, q, rate, amount,
                quality, notes, source_name, sheet, excel_row_num, mandi_fee, nirashrit_fee
            ))
            inserted += 1

    con.commit()
    con.close()
    return inserted, duplicates, skipped

def commodity_kind(grain, variety=''):
    text=(str(grain or '')+' '+str(variety or '')).lower()
    if any(x in text for x in ['wheat','gehu','gehun','गेहूं','गेहूँ','गेहु']): return 'Wheat'
    if any(x in text for x in ['mustard','sarso','sarson','सरसों','सरसो']): return 'Mustard'
    if any(x in text for x in ['paddy','dhan','धान']): return 'Paddy'
    if any(x in text for x in ['methi','fenugreek','मेथी']): return 'Methi Seeds'
    if any(x in text for x in ['moong','mung','मूंग']): return 'Moong'
    return grain or 'Other'

def settings_path(): return os.path.join(BASE_DIR,'settings.json')
def load_settings():
    try:
        with open(settings_path(),'r',encoding='utf-8') as f: return json.load(f)
    except Exception: return {'auto_sync':False,'sync_folder':'','login_enabled':False}
def save_settings(v):
    with open(settings_path(),'w',encoding='utf-8') as f: json.dump(v,f,ensure_ascii=False,indent=2)

def sync_folder(folder):
    if not folder or not os.path.isdir(folder): return (0,0,0,[])
    totals=[0,0,0]; errors=[]
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(('.xlsx','.xls','.xlsm')): continue
        path=os.path.join(folder,name)
        try:
            ins,dup,skip=import_excel(path,name)
            totals[0]+=ins; totals[1]+=dup; totals[2]+=skip
        except Exception as e: errors.append(f'{name}: {e}')
    return (*totals,errors)

@app.route("/")
def dashboard():
    con = db()
    totals = con.execute("""
        SELECT COALESCE(SUM(quantity_quintal),0) qty,
               COALESCE(SUM(amount),0) amount,
               COUNT(*) purchases
        FROM purchases
    """).fetchone()
    grain_rows = con.execute("""
        SELECT grain, SUM(quantity_quintal) qty, SUM(amount) amount,
               CASE WHEN SUM(quantity_quintal)>0
                    THEN SUM(amount)/SUM(quantity_quintal) ELSE 0 END avg_rate
        FROM purchases GROUP BY grain ORDER BY qty DESC
    """).fetchall()

    # Overall Wheat average: combine ALL wheat varieties into one weighted average.
    wheat_rows = con.execute("""
        SELECT quantity_quintal, amount
        FROM purchases
        WHERE (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
           OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
           OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
           OR COALESCE(grain,'') LIKE '%गेहूं%'
           OR COALESCE(grain,'') LIKE '%गेहूँ%'
           OR COALESCE(grain,'') LIKE '%गेहु%'
           OR LOWER(COALESCE(variety,'')) LIKE '%wheat%'
           OR LOWER(COALESCE(variety,'')) LIKE '%gehu%'
           OR LOWER(COALESCE(variety,'')) LIKE '%gehun%'
           OR COALESCE(variety,'') LIKE '%गेहूं%'
           OR COALESCE(variety,'') LIKE '%गेहूँ%'
           OR COALESCE(variety,'') LIKE '%गेहु%')
    """).fetchall()
    wheat_total_qty = sum(float(r['quantity_quintal'] or 0) for r in wheat_rows)
    wheat_total_amount = sum(float(r['amount'] or 0) for r in wheat_rows)
    wheat_total_avg = wheat_total_amount / wheat_total_qty if wheat_total_qty else 0

    # Overall Mustard average: combine all common English/Hindi mustard names.
    mustard_rows = con.execute("""
        SELECT quantity_quintal, amount
        FROM purchases
        WHERE (LOWER(COALESCE(grain,'')) LIKE '%mustard%'
           OR LOWER(COALESCE(grain,'')) LIKE '%sarso%'
           OR LOWER(COALESCE(grain,'')) LIKE '%sarson%'
           OR COALESCE(grain,'') LIKE '%सरसों%'
           OR COALESCE(grain,'') LIKE '%सरसो%'
           OR LOWER(COALESCE(variety,'')) LIKE '%mustard%'
           OR LOWER(COALESCE(variety,'')) LIKE '%sarso%'
           OR LOWER(COALESCE(variety,'')) LIKE '%sarson%'
           OR COALESCE(variety,'') LIKE '%सरसों%'
           OR COALESCE(variety,'') LIKE '%सरसो%')
    """).fetchall()
    mustard_total_qty = sum(float(r['quantity_quintal'] or 0) for r in mustard_rows)
    mustard_total_amount = sum(float(r['amount'] or 0) for r in mustard_rows)
    mustard_total_avg = mustard_total_amount / mustard_total_qty if mustard_total_qty else 0

    # Overall Paddy average: combine all common English/Hindi paddy/dhan names and varieties.
    paddy_rows = con.execute("""
        SELECT quantity_quintal, amount
        FROM purchases
        WHERE (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
           OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
           OR COALESCE(grain,'') LIKE '%धान%'
           OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
           OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
           OR COALESCE(variety,'') LIKE '%धान%')
    """).fetchall()
    paddy_total_qty = sum(float(r['quantity_quintal'] or 0) for r in paddy_rows)
    paddy_total_amount = sum(float(r['amount'] or 0) for r in paddy_rows)
    paddy_total_avg = paddy_total_amount / paddy_total_qty if paddy_total_qty else 0

    # Overall Methi Seeds average: combine common English/Hindi methi names.
    methi_rows = con.execute("""
        SELECT quantity_quintal, amount
        FROM purchases
        WHERE (LOWER(COALESCE(grain,'')) LIKE '%methi%'
           OR LOWER(COALESCE(grain,'')) LIKE '%fenugreek%'
           OR COALESCE(grain,'') LIKE '%मेथी%'
           OR LOWER(COALESCE(variety,'')) LIKE '%methi%'
           OR LOWER(COALESCE(variety,'')) LIKE '%fenugreek%'
           OR COALESCE(variety,'') LIKE '%मेथी%')
    """).fetchall()
    methi_total_qty = sum(float(r['quantity_quintal'] or 0) for r in methi_rows)
    methi_total_amount = sum(float(r['amount'] or 0) for r in methi_rows)
    methi_total_avg = methi_total_amount / methi_total_qty if methi_total_qty else 0
    recent = con.execute("""
        SELECT * FROM purchases ORDER BY purchase_date DESC, id DESC LIMIT 10
    """).fetchall()

    # Daily weighted-average analysis. Each day gets an overall average
    # for all grains, plus Moong averages split at ₹3,000/qtl.
    daily_rows = con.execute("""
        SELECT purchase_date AS day,
               SUM(quantity_quintal) AS qty,
               SUM(amount) AS amount,
               CASE WHEN SUM(quantity_quintal) > 0
                    THEN SUM(amount) / SUM(quantity_quintal) ELSE 0 END AS avg_rate
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
        GROUP BY purchase_date
        ORDER BY purchase_date DESC
    """).fetchall()

    daily_moong_rows = con.execute("""
        SELECT purchase_date AS day, quantity_quintal AS qty, rate, amount
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%moong%'
           OR LOWER(COALESCE(grain,'')) LIKE '%mung%'
           OR COALESCE(grain,'') LIKE '%मूंग%')
        ORDER BY purchase_date DESC, id DESC
    """).fetchall()

    # Wheat daily weighted-average analysis. Match common English/Hindi names.
    daily_wheat_rows = con.execute("""
        SELECT purchase_date AS day, quantity_quintal AS qty, rate, amount
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
           OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
           OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
           OR COALESCE(grain,'') LIKE '%गेहूं%'
           OR COALESCE(grain,'') LIKE '%गेहूँ%')
        ORDER BY purchase_date DESC, id DESC
    """).fetchall()

    daily_mustard_rows = con.execute("""
        SELECT purchase_date AS day, quantity_quintal AS qty, rate, amount
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%mustard%'
           OR LOWER(COALESCE(grain,'')) LIKE '%sarso%'
           OR LOWER(COALESCE(grain,'')) LIKE '%sarson%'
           OR COALESCE(grain,'') LIKE '%सरसों%'
           OR COALESCE(grain,'') LIKE '%सरसो%'
           OR LOWER(COALESCE(variety,'')) LIKE '%mustard%'
           OR LOWER(COALESCE(variety,'')) LIKE '%sarso%'
           OR LOWER(COALESCE(variety,'')) LIKE '%sarson%'
           OR COALESCE(variety,'') LIKE '%सरसों%'
           OR COALESCE(variety,'') LIKE '%सरसो%')
        ORDER BY purchase_date DESC, id DESC
    """).fetchall()

    daily_paddy_rows = con.execute("""
        SELECT purchase_date AS day, quantity_quintal AS qty, rate, amount
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
           OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
           OR COALESCE(grain,'') LIKE '%धान%'
           OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
           OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
           OR COALESCE(variety,'') LIKE '%धान%')
        ORDER BY purchase_date DESC, id DESC
    """).fetchall()

    daily_methi_rows = con.execute("""
        SELECT purchase_date AS day, quantity_quintal AS qty, rate, amount
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%methi%'
           OR LOWER(COALESCE(grain,'')) LIKE '%fenugreek%'
           OR COALESCE(grain,'') LIKE '%मेथी%'
           OR LOWER(COALESCE(variety,'')) LIKE '%methi%'
           OR LOWER(COALESCE(variety,'')) LIKE '%fenugreek%'
           OR COALESCE(variety,'') LIKE '%मेथी%')
        ORDER BY purchase_date DESC, id DESC
    """).fetchall()

    daily_map = {}
    for r in daily_rows:
        day = r['day']
        daily_map[day] = {
            'day': day,
            'qty': float(r['qty'] or 0),
            'amount': float(r['amount'] or 0),
            'avg_rate': float(r['avg_rate'] or 0),
            'moong_qty': 0.0, 'moong_amount': 0.0,
            'moong_high_qty': 0.0, 'moong_high_amount': 0.0,
            'moong_low_qty': 0.0, 'moong_low_amount': 0.0,
            'wheat_qty': 0.0, 'wheat_amount': 0.0,
            'mustard_qty': 0.0, 'mustard_amount': 0.0,
            'paddy_qty': 0.0, 'paddy_amount': 0.0,
            'methi_qty': 0.0, 'methi_amount': 0.0
        }

    for r in daily_moong_rows:
        day = r['day']
        if day not in daily_map:
            continue
        q = float(r['qty'] or 0)
        rate = float(r['rate'] or 0)
        amount = float(r['amount'] or 0)
        d = daily_map[day]
        d['moong_qty'] += q
        d['moong_amount'] += amount
        if rate >= 3000:
            d['moong_high_qty'] += q
            d['moong_high_amount'] += amount
        elif rate > 0:
            d['moong_low_qty'] += q
            d['moong_low_amount'] += amount

    for r in daily_wheat_rows:
        day = r['day']
        if day not in daily_map:
            continue
        q = float(r['qty'] or 0)
        amount = float(r['amount'] or 0)
        d = daily_map[day]
        d['wheat_qty'] += q
        d['wheat_amount'] += amount

    for r in daily_mustard_rows:
        day = r['day']
        if day not in daily_map:
            continue
        q = float(r['qty'] or 0)
        amount = float(r['amount'] or 0)
        d = daily_map[day]
        d['mustard_qty'] += q
        d['mustard_amount'] += amount

    for r in daily_paddy_rows:
        day = r['day']
        if day not in daily_map:
            continue
        q = float(r['qty'] or 0)
        amount = float(r['amount'] or 0)
        d = daily_map[day]
        d['paddy_qty'] += q
        d['paddy_amount'] += amount

    for r in daily_methi_rows:
        day = r['day']
        if day not in daily_map:
            continue
        q = float(r['qty'] or 0)
        amount = float(r['amount'] or 0)
        d = daily_map[day]
        d['methi_qty'] += q
        d['methi_amount'] += amount

    for d in daily_map.values():
        d['moong_avg'] = d['moong_amount'] / d['moong_qty'] if d['moong_qty'] else 0
        d['moong_high_avg'] = d['moong_high_amount'] / d['moong_high_qty'] if d['moong_high_qty'] else 0
        d['moong_low_avg'] = d['moong_low_amount'] / d['moong_low_qty'] if d['moong_low_qty'] else 0
        d['wheat_avg'] = d['wheat_amount'] / d['wheat_qty'] if d['wheat_qty'] else 0
        d['mustard_avg'] = d['mustard_amount'] / d['mustard_qty'] if d['mustard_qty'] else 0
        d['paddy_avg'] = d['paddy_amount'] / d['paddy_qty'] if d['paddy_qty'] else 0
        d['methi_avg'] = d['methi_amount'] / d['methi_qty'] if d['methi_qty'] else 0

    daily_analysis = list(daily_map.values())

    # Daily grain-wise purchase analysis: one row for every date + grain,
    # with total quantity, total amount and weighted average purchase rate.
    daily_grain_rows = con.execute("""
        SELECT purchase_date AS day, grain, COALESCE(NULLIF(TRIM(variety), ''), '') AS variety,
               SUM(quantity_quintal) AS qty,
               SUM(amount) AS amount,
               CASE WHEN SUM(quantity_quintal) > 0
                    THEN SUM(amount) / SUM(quantity_quintal) ELSE 0 END AS avg_rate
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND TRIM(COALESCE(grain,'')) <> ''
        GROUP BY purchase_date, grain, COALESCE(NULLIF(TRIM(variety), ''), '')
        ORDER BY purchase_date DESC, grain ASC, variety ASC
    """).fetchall()

    daily_grain_analysis = [
        {
            'day': r['day'],
            'grain': r['grain'],
            'variety': r['variety'] or '',
            'qty': float(r['qty'] or 0),
            'amount': float(r['amount'] or 0),
            'avg_rate': float(r['avg_rate'] or 0)
        }
        for r in daily_grain_rows
    ]

    # Moong averages: split at ₹3,000/quintal.
    # Match common Hindi/English names and names containing "moong"/"मूंग".
    moong_rows = con.execute("""
        SELECT quantity_quintal, rate, amount, grain
        FROM purchases
        WHERE LOWER(COALESCE(grain,'')) LIKE '%moong%'
           OR LOWER(COALESCE(grain,'')) LIKE '%mung%'
           OR COALESCE(grain,'') LIKE '%मूंग%'
    """).fetchall()

    moong_total_qty = moong_high_qty = moong_low_qty = 0.0
    moong_total_amount = moong_high_amount = moong_low_amount = 0.0

    for r in moong_rows:
        q = float(r["quantity_quintal"] or 0)
        rate = float(r["rate"] or 0)
        amount = float(r["amount"] or 0)
        moong_total_qty += q
        moong_total_amount += amount
        if rate >= 3000:
            moong_high_qty += q
            moong_high_amount += amount
        elif rate > 0:
            moong_low_qty += q
            moong_low_amount += amount

    moong_total_avg = moong_total_amount / moong_total_qty if moong_total_qty else 0
    moong_high_avg = moong_high_amount / moong_high_qty if moong_high_qty else 0
    moong_low_avg = moong_low_amount / moong_low_qty if moong_low_qty else 0

    today_s = date.today().isoformat()
    month_s = date.today().strftime('%Y-%m')
    today_stats = con.execute("SELECT COALESCE(SUM(quantity_quintal),0) qty, COALESCE(SUM(amount),0) amount, COUNT(*) cnt FROM purchases WHERE purchase_date=?", (today_s,)).fetchone()
    month_stats = con.execute("SELECT COALESCE(SUM(quantity_quintal),0) qty, COALESCE(SUM(amount),0) amount, COUNT(*) cnt FROM purchases WHERE substr(purchase_date,1,7)=?", (month_s,)).fetchone()
    alert_rows=[]
    for r in con.execute("SELECT grain, SUM(quantity_quintal) qty, SUM(amount)/NULLIF(SUM(quantity_quintal),0) avg_rate FROM purchases GROUP BY grain HAVING qty>0 ORDER BY grain").fetchall():
        if r['avg_rate'] and r['avg_rate']>0: alert_rows.append(dict(r))
    settings=load_settings()
    if settings.get('auto_sync') and settings.get('sync_folder'):
        try: sync_folder(settings['sync_folder'])
        except Exception: pass
    con.close()
    return render_template(
        "dashboard.html",
        totals=totals,
        grain_rows=grain_rows,
        recent=recent,
        moong_total_qty=moong_total_qty,
        moong_total_avg=moong_total_avg,
        moong_high_qty=moong_high_qty,
        moong_high_avg=moong_high_avg,
        moong_low_qty=moong_low_qty,
        moong_low_avg=moong_low_avg,
        wheat_total_qty=wheat_total_qty,
        wheat_total_avg=wheat_total_avg,
        mustard_total_qty=mustard_total_qty,
        mustard_total_avg=mustard_total_avg,
        paddy_total_qty=paddy_total_qty,
        paddy_total_avg=paddy_total_avg,
        methi_total_qty=methi_total_qty,
        methi_total_avg=methi_total_avg,
        daily_analysis=daily_analysis,
        daily_grain_analysis=daily_grain_analysis,
        today_stats=today_stats, month_stats=month_stats, alert_rows=alert_rows
    )

@app.route("/daily-purchase-avg")
def daily_purchase_avg():
    con = db()
    # Daily report: Wheat and Paddy are consolidated across ALL varieties.
    # Other grains remain separated by their recorded variety.
    rows = con.execute("""
        SELECT purchase_date AS day,
               CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                       OR COALESCE(grain,'') LIKE '%गेहूं%'
                       OR COALESCE(grain,'') LIKE '%गेहूँ%')
                    THEN 'Wheat'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                       OR COALESCE(grain,'') LIKE '%धान%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                       OR COALESCE(variety,'') LIKE '%धान%')
                    THEN 'Paddy'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%methi%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%fenugreek%'
                       OR COALESCE(grain,'') LIKE '%मेथी%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%methi%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%fenugreek%'
                       OR COALESCE(variety,'') LIKE '%मेथी%')
                    THEN 'Methi Seeds' ELSE grain END AS grain,
               CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                       OR COALESCE(grain,'') LIKE '%गेहूं%'
                       OR COALESCE(grain,'') LIKE '%गेहूँ%')
                    THEN 'All Varieties'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                       OR COALESCE(grain,'') LIKE '%धान%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                       OR COALESCE(variety,'') LIKE '%धान%')
                    THEN 'All Varieties'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%methi%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%fenugreek%'
                       OR COALESCE(grain,'') LIKE '%मेथी%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%methi%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%fenugreek%'
                       OR COALESCE(variety,'') LIKE '%मेथी%')
                    THEN 'All Varieties' ELSE COALESCE(NULLIF(TRIM(variety), ''), '') END AS variety,
               SUM(quantity_quintal) AS qty,
               SUM(amount) AS amount,
               CASE WHEN SUM(quantity_quintal) > 0
                    THEN SUM(amount) / SUM(quantity_quintal) ELSE 0 END AS avg_rate
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND TRIM(COALESCE(grain,'')) <> ''
        GROUP BY purchase_date,
                 CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                         OR COALESCE(grain,'') LIKE '%गेहूं%'
                         OR COALESCE(grain,'') LIKE '%गेहूँ%')
                      THEN 'Wheat'
                      WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                         OR COALESCE(grain,'') LIKE '%धान%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                         OR COALESCE(variety,'') LIKE '%धान%')
                      THEN 'Paddy'
                      WHEN (LOWER(COALESCE(grain,'')) LIKE '%methi%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%fenugreek%'
                         OR COALESCE(grain,'') LIKE '%मेथी%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%methi%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%fenugreek%'
                         OR COALESCE(variety,'') LIKE '%मेथी%')
                      THEN 'Methi Seeds' ELSE grain END,
                 CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                         OR COALESCE(grain,'') LIKE '%गेहूं%'
                         OR COALESCE(grain,'') LIKE '%गेहूँ%')
                      THEN 'All Varieties'
                      WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                         OR COALESCE(grain,'') LIKE '%धान%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                         OR COALESCE(variety,'') LIKE '%धान%')
                      THEN 'All Varieties'
                      WHEN (LOWER(COALESCE(grain,'')) LIKE '%methi%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%fenugreek%'
                         OR COALESCE(grain,'') LIKE '%मेथी%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%methi%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%fenugreek%'
                         OR COALESCE(variety,'') LIKE '%मेथी%')
                      THEN 'All Varieties' ELSE COALESCE(NULLIF(TRIM(variety), ''), '') END
        ORDER BY purchase_date DESC, grain ASC, variety ASC
    """).fetchall()

    # Build a compact day summary for the top cards.
    day_summary_rows = con.execute("""
        SELECT purchase_date AS day,
               SUM(quantity_quintal) AS qty,
               SUM(amount) AS amount,
               CASE WHEN SUM(quantity_quintal) > 0
                    THEN SUM(amount) / SUM(quantity_quintal) ELSE 0 END AS avg_rate,
               COUNT(*) AS entries
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
        GROUP BY purchase_date
        ORDER BY purchase_date DESC
    """).fetchall()
    # Separate daily Moong analysis: total, ₹3,000+ and below ₹3,000.
    # Keep the database connection open until all report queries are complete.
    moong_daily_rows = con.execute("""
        SELECT purchase_date AS day,
               SUM(quantity_quintal) AS total_qty,
               SUM(amount) AS total_amount,
               SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END) AS high_qty,
               SUM(CASE WHEN rate >= 3000 THEN amount ELSE 0 END) AS high_amount,
               SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END) AS low_qty,
               SUM(CASE WHEN rate > 0 AND rate < 3000 THEN amount ELSE 0 END) AS low_amount
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%moong%'
            OR LOWER(COALESCE(grain,'')) LIKE '%mung%'
            OR COALESCE(grain,'') LIKE '%मूंग%')
        GROUP BY purchase_date
        ORDER BY purchase_date DESC
    """).fetchall()
    moong_daily_analysis = []
    for r in moong_daily_rows:
        x = dict(r)
        x['total_avg'] = (x['total_amount'] / x['total_qty']) if x['total_qty'] else 0
        x['high_avg'] = (x['high_amount'] / x['high_qty']) if x['high_qty'] else 0
        x['low_avg'] = (x['low_amount'] / x['low_qty']) if x['low_qty'] else 0
        moong_daily_analysis.append(x)
    con.close()

    daily_grain_analysis = [dict(r) for r in rows]
    day_summaries = [dict(r) for r in day_summary_rows]
    return render_template(
        "daily_purchase_avg.html",
        daily_grain_analysis=daily_grain_analysis,
        day_summaries=day_summaries,
        moong_daily_analysis=moong_daily_analysis
    )

@app.route("/purchases")
def purchases():
    grain = request.args.get("grain", "")
    mandi = request.args.get("mandi", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    search = request.args.get("search", "")
    min_rate = request.args.get("min_rate", "")
    max_rate = request.args.get("max_rate", "")
    sort = request.args.get("sort", "purchase_date")
    direction = "DESC" if request.args.get("direction", "desc").lower() == "desc" else "ASC"
    allowed = {
        "purchase_date":"purchase_date", "grain":"grain", "mandi":"mandi",
        "quantity":"quantity_quintal", "rate":"rate", "amount":"amount"
    }
    order_col = allowed.get(sort, "purchase_date")
    con = db()
    rows = con.execute(f"""
        SELECT * FROM purchases
        WHERE (?='' OR grain LIKE ?)
          AND (?='' OR mandi LIKE ?)
          AND (?='' OR purchase_date>=?)
          AND (?='' OR purchase_date<=?)
          AND (?='' OR grain LIKE ? OR variety LIKE ? OR seller LIKE ? OR notes LIKE ?)
          AND (?='' OR rate>=?) AND (?='' OR rate<=?)
        ORDER BY {order_col} {direction}, id DESC
    """, (grain,f"%{grain}%",mandi,f"%{mandi}%",date_from,date_from,date_to,date_to,search,f"%{search}%",f"%{search}%",f"%{search}%",f"%{search}%",min_rate,min_rate,max_rate,max_rate)).fetchall()
    grains = con.execute("SELECT DISTINCT grain FROM purchases ORDER BY grain").fetchall()
    mandis = con.execute("SELECT DISTINCT mandi FROM purchases WHERE mandi<>'' ORDER BY mandi").fetchall()
    attachments = {}
    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        for a in con.execute(f"SELECT * FROM purchase_attachments WHERE purchase_id IN ({placeholders}) ORDER BY id", ids).fetchall():
            attachments.setdefault(a["purchase_id"], []).append(a)
    con.close()
    return render_template("purchases.html", rows=rows, grains=grains, mandis=mandis, attachments=attachments,
                           grain=grain, mandi=mandi, date_from=date_from, date_to=date_to,
                           search=search, min_rate=min_rate, max_rate=max_rate, sort=sort, direction=direction)

@app.route("/add", methods=["POST"])
def add_purchase():
    data = request.form
    q, rate = to_number(data.get("quantity_quintal")), to_number(data.get("rate"))
    if not data.get("purchase_date") or not data.get("grain") or q <= 0 or rate <= 0:
        flash("Please enter date, grain, quantity and rate correctly.", "error")
        return redirect(url_for("purchases"))
    con = db()
    cur = con.execute("""
        INSERT INTO purchases
        (purchase_date, mandi, grain, variety, seller, quantity_quintal, rate, amount, quality, notes, mandi_fee, nirashrit_fee, transport_cost, labour_cost, other_cost)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("purchase_date",""), data.get("mandi",""), data.get("grain",""),
        data.get("variety",""), data.get("seller",""), q, rate, q*rate,
        data.get("quality",""), data.get("notes",""), to_number(data.get("mandi_fee")), to_number(data.get("nirashrit_fee")), to_number(data.get("transport_cost")), to_number(data.get("labour_cost")), to_number(data.get("other_cost"))
    ))
    purchase_id = cur.lastrowid
    profile_dir = os.path.join(UPLOAD_DIR, str(current_profile()["id"]))
    os.makedirs(profile_dir, exist_ok=True)
    for uploaded in request.files.getlist("purchase_documents"):
        if not uploaded or not uploaded.filename:
            continue
        ext = os.path.splitext(uploaded.filename)[1].lower().lstrip(".")
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            continue
        safe = secure_filename(uploaded.filename) or f"document.{ext}"
        stored = f"{purchase_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe}"
        uploaded.save(os.path.join(profile_dir, stored))
        con.execute("INSERT INTO purchase_attachments (purchase_id, filename, stored_name) VALUES (?,?,?)", (purchase_id, safe, stored))
    con.commit(); con.close()
    flash("Purchase added successfully.", "success")
    return redirect(url_for("purchases"))

@app.route("/purchase-attachment/<int:attachment_id>")
def purchase_attachment(attachment_id):
    con = db()
    row = con.execute("SELECT * FROM purchase_attachments WHERE id=?", (attachment_id,)).fetchone()
    con.close()
    if not row:
        return "Attachment not found", 404
    profile_dir = os.path.join(UPLOAD_DIR, str(current_profile()["id"]))
    return send_from_directory(profile_dir, row["stored_name"], as_attachment=False)

@app.route("/api/mobile-sync", methods=["POST"])
def mobile_sync():
    payload = request.get_json(silent=True) or {}
    items = payload.get("purchases", [])
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "Invalid purchases payload"}), 400
    created = 0
    con = db()
    for data in items[:100]:
        q, rate = to_number(data.get("quantity_quintal")), to_number(data.get("rate"))
        if not data.get("purchase_date") or not data.get("grain") or q <= 0 or rate <= 0:
            continue
        con.execute("""INSERT INTO purchases
            (purchase_date, mandi, grain, variety, seller, quantity_quintal, rate, amount, quality, notes, mandi_fee, nirashrit_fee, transport_cost, labour_cost, other_cost)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            data.get("purchase_date",""), data.get("mandi",""), data.get("grain",""), data.get("variety",""), data.get("seller",""), q, rate, q*rate, data.get("quality",""), data.get("notes",""), to_number(data.get("mandi_fee")), to_number(data.get("nirashrit_fee")), to_number(data.get("transport_cost")), to_number(data.get("labour_cost")), to_number(data.get("other_cost"))))
        created += 1
    con.commit(); con.close()
    return jsonify({"ok": True, "created": created})

@app.route("/delete/<int:purchase_id>", methods=["POST"])
def delete_purchase(purchase_id):
    con=db(); con.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))
    con.commit(); con.close()
    flash("Purchase deleted.", "success")
    return redirect(request.referrer or url_for("purchases"))


@app.route("/delete-selected", methods=["POST"])
def delete_selected():
    ids = request.form.getlist("purchase_ids")
    valid_ids = []
    for x in ids:
        try:
            valid_ids.append(int(x))
        except (TypeError, ValueError):
            pass

    if not valid_ids:
        flash("Please select at least one entry to delete.", "error")
        return redirect(request.referrer or url_for("purchases"))

    con = db()
    placeholders = ",".join("?" for _ in valid_ids)
    cur = con.execute(f"DELETE FROM purchases WHERE id IN ({placeholders})", valid_ids)
    deleted = cur.rowcount
    con.commit()
    con.close()

    flash(f"{deleted} selected purchase entr{'y was' if deleted == 1 else 'ies were'} deleted.", "success")
    return redirect(request.referrer or url_for("purchases"))

@app.route("/import", methods=["GET","POST"])
def import_page():
    if request.method == "POST":
        files = request.files.getlist("excel_files")
        files = [f for f in files if f and f.filename]
        if not files:
            flash("Please choose one or more Excel files.", "error")
            return redirect(url_for("import_page"))

        total_inserted = total_dupes = total_skipped = 0
        errors = []
        for f in files:
            if not f.filename.lower().endswith((".xlsx",".xls",".xlsm")):
                errors.append(f"{f.filename}: unsupported file type")
                continue
            name = secure_filename(f.filename)
            path = os.path.join(UPLOAD_DIR, name)
            # Keep the latest uploaded copy for future reference.
            f.save(path)
            try:
                ins, dup, skip = import_excel(path, name)
                total_inserted += ins; total_dupes += dup; total_skipped += skip
            except Exception as e:
                errors.append(f"{f.filename}: {e}")

        flash(f"Imported {total_inserted} new rows. Skipped {total_dupes} duplicate rows and {total_skipped} invalid/footer rows.", "success")
        for e in errors:
            flash(e, "error")
        return redirect(url_for("purchases"))
    return render_template("import.html")

@app.route("/api/summary")
def api_summary():
    con=db()
    rows=con.execute("""
        SELECT grain, ROUND(SUM(quantity_quintal),2) qty,
               ROUND(SUM(amount),2) amount,
               ROUND(SUM(amount)/NULLIF(SUM(quantity_quintal),0),2) avg_rate
        FROM purchases GROUP BY grain ORDER BY qty DESC
    """).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/export-daily-moong")
def export_daily_moong():
    con = db()
    rows = con.execute("""
        SELECT purchase_date AS Date,
               SUM(quantity_quintal) AS Total_Quantity_Qtl,
               SUM(amount) AS Total_Amount,
               CASE WHEN SUM(quantity_quintal)>0 THEN SUM(amount)/SUM(quantity_quintal) ELSE 0 END AS Moong_Total_Avg,
               SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END) AS Qty_3000_Plus_Qtl,
               SUM(CASE WHEN rate >= 3000 THEN amount ELSE 0 END) AS Amount_3000_Plus,
               CASE WHEN SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END)>0 THEN SUM(CASE WHEN rate >= 3000 THEN amount ELSE 0 END)/SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END) ELSE 0 END AS Moong_3000_Plus_Avg,
               SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END) AS Qty_Below_3000_Qtl,
               SUM(CASE WHEN rate > 0 AND rate < 3000 THEN amount ELSE 0 END) AS Amount_Below_3000,
               CASE WHEN SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END)>0 THEN SUM(CASE WHEN rate > 0 AND rate < 3000 THEN amount ELSE 0 END)/SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END) ELSE 0 END AS Moong_Below_3000_Avg
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND (LOWER(COALESCE(grain,'')) LIKE '%moong%' OR LOWER(COALESCE(grain,'')) LIKE '%mung%' OR COALESCE(grain,'') LIKE '%मूंग%')
        GROUP BY purchase_date
        ORDER BY purchase_date DESC
    """).fetchall()
    con.close()
    df = pd.DataFrame([dict(r) for r in rows])
    out = os.path.join(BASE_DIR, "daily_moong_average_analysis.xlsx")
    df.to_excel(out, index=False)
    return send_file(out, as_attachment=True, download_name="daily_moong_average_analysis.xlsx")

@app.route("/api/export-daily-moong/<path:day>")
def export_daily_moong_day(day):
    con = db()
    row = con.execute("""
        SELECT purchase_date AS Date,
               SUM(quantity_quintal) AS Total_Quantity_Qtl,
               SUM(amount) AS Total_Amount,
               CASE WHEN SUM(quantity_quintal)>0 THEN SUM(amount)/SUM(quantity_quintal) ELSE 0 END AS Moong_Total_Avg,
               SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END) AS Qty_3000_Plus_Qtl,
               SUM(CASE WHEN rate >= 3000 THEN amount ELSE 0 END) AS Amount_3000_Plus,
               CASE WHEN SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END)>0 THEN SUM(CASE WHEN rate >= 3000 THEN amount ELSE 0 END)/SUM(CASE WHEN rate >= 3000 THEN quantity_quintal ELSE 0 END) ELSE 0 END AS Moong_3000_Plus_Avg,
               SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END) AS Qty_Below_3000_Qtl,
               SUM(CASE WHEN rate > 0 AND rate < 3000 THEN amount ELSE 0 END) AS Amount_Below_3000,
               CASE WHEN SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END)>0 THEN SUM(CASE WHEN rate > 0 AND rate < 3000 THEN amount ELSE 0 END)/SUM(CASE WHEN rate > 0 AND rate < 3000 THEN quantity_quintal ELSE 0 END) ELSE 0 END AS Moong_Below_3000_Avg
        FROM purchases
        WHERE purchase_date = ?
          AND (LOWER(COALESCE(grain,'')) LIKE '%moong%' OR LOWER(COALESCE(grain,'')) LIKE '%mung%' OR COALESCE(grain,'') LIKE '%मूंग%')
        GROUP BY purchase_date
    """, (day,)).fetchall()
    con.close()
    df = pd.DataFrame([dict(row)] if row else [])
    safe_day = re.sub(r"[^0-9A-Za-z_-]+", "_", day) or "day"
    out = os.path.join(BASE_DIR, f"daily_moong_report_{safe_day}.xlsx")
    df.to_excel(out, index=False)
    return send_file(out, as_attachment=True, download_name=f"daily_moong_report_{safe_day}.xlsx")

@app.route("/api/export-daily-grain")
def export_daily_grain():
    con = db()
    df = pd.read_sql_query("""
        SELECT purchase_date AS Date,
               CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                       OR COALESCE(grain,'') LIKE '%गेहूं%'
                       OR COALESCE(grain,'') LIKE '%गेहूँ%')
                    THEN 'Wheat'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                       OR COALESCE(grain,'') LIKE '%धान%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                       OR COALESCE(variety,'') LIKE '%धान%')
                    THEN 'Paddy' ELSE grain END AS Grain,
               CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                       OR COALESCE(grain,'') LIKE '%गेहूं%'
                       OR COALESCE(grain,'') LIKE '%गेहूँ%')
                    THEN 'All Varieties'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                       OR COALESCE(grain,'') LIKE '%धान%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                       OR COALESCE(variety,'') LIKE '%धान%')
                    THEN 'All Varieties' ELSE COALESCE(NULLIF(TRIM(variety), ''), '') END AS Variety,
               SUM(quantity_quintal) AS Quantity_Qtl,
               SUM(amount) AS Total_Amount,
               CASE WHEN SUM(quantity_quintal) > 0
                    THEN SUM(amount) / SUM(quantity_quintal) ELSE 0 END AS Weighted_Avg_Rate
        FROM purchases
        WHERE TRIM(COALESCE(purchase_date,'')) <> ''
          AND TRIM(COALESCE(grain,'')) <> ''
        GROUP BY purchase_date,
                 CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                         OR COALESCE(grain,'') LIKE '%गेहूं%'
                         OR COALESCE(grain,'') LIKE '%गेहूँ%') THEN 'Wheat'
                 WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                         OR COALESCE(grain,'') LIKE '%धान%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                         OR COALESCE(variety,'') LIKE '%धान%') THEN 'Paddy' ELSE grain END,
                 CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                         OR COALESCE(grain,'') LIKE '%गेहूं%'
                         OR COALESCE(grain,'') LIKE '%गेहूँ%') THEN 'All Varieties'
                 WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                         OR COALESCE(grain,'') LIKE '%धान%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                         OR COALESCE(variety,'') LIKE '%धान%') THEN 'All Varieties' ELSE COALESCE(NULLIF(TRIM(variety), ''), '') END
        ORDER BY purchase_date DESC, Grain ASC, Variety ASC
    """, con)
    con.close()
    out = os.path.join(BASE_DIR, "daily_grain_variety_purchase_average.xlsx")
    df.to_excel(out, index=False)
    return send_file(out, as_attachment=True, download_name="daily_grain_variety_purchase_average.xlsx")

@app.route("/api/export-daily-grain/<path:day>")
def export_daily_grain_day(day):
    con = db()
    df = pd.read_sql_query("""
        SELECT purchase_date AS Date,
               CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                       OR COALESCE(grain,'') LIKE '%गेहूं%'
                       OR COALESCE(grain,'') LIKE '%गेहूँ%')
                    THEN 'Wheat'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                       OR COALESCE(grain,'') LIKE '%धान%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                       OR COALESCE(variety,'') LIKE '%धान%')
                    THEN 'Paddy' ELSE grain END AS Grain,
               CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                       OR COALESCE(grain,'') LIKE '%गेहूं%'
                       OR COALESCE(grain,'') LIKE '%गेहूँ%')
                    THEN 'All Varieties'
                    WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                       OR COALESCE(grain,'') LIKE '%धान%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                       OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                       OR COALESCE(variety,'') LIKE '%धान%')
                    THEN 'All Varieties' ELSE COALESCE(NULLIF(TRIM(variety), ''), '') END AS Variety,
               SUM(quantity_quintal) AS Quantity_Qtl,
               SUM(amount) AS Total_Amount,
               CASE WHEN SUM(quantity_quintal) > 0
                    THEN SUM(amount) / SUM(quantity_quintal) ELSE 0 END AS Weighted_Avg_Rate
        FROM purchases
        WHERE purchase_date = ?
          AND TRIM(COALESCE(grain,'')) <> ''
        GROUP BY purchase_date,
                 CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                         OR COALESCE(grain,'') LIKE '%गेहूं%'
                         OR COALESCE(grain,'') LIKE '%गेहूँ%') THEN 'Wheat'
                 WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                         OR COALESCE(grain,'') LIKE '%धान%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                         OR COALESCE(variety,'') LIKE '%धान%') THEN 'Paddy' ELSE grain END,
                 CASE WHEN (LOWER(COALESCE(grain,'')) LIKE '%wheat%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehu%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%gehun%'
                         OR COALESCE(grain,'') LIKE '%गेहूं%'
                         OR COALESCE(grain,'') LIKE '%गेहूँ%') THEN 'All Varieties'
                 WHEN (LOWER(COALESCE(grain,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(grain,'')) LIKE '%dhan%'
                         OR COALESCE(grain,'') LIKE '%धान%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%paddy%'
                         OR LOWER(COALESCE(variety,'')) LIKE '%dhan%'
                         OR COALESCE(variety,'') LIKE '%धान%') THEN 'All Varieties' ELSE COALESCE(NULLIF(TRIM(variety), ''), '') END
        ORDER BY Grain ASC, Variety ASC
    """, con, params=(day,))
    con.close()
    safe_day = re.sub(r"[^0-9A-Za-z_-]+", "_", day) or "day"
    out = os.path.join(BASE_DIR, f"daily_report_{safe_day}.xlsx")
    df.to_excel(out, index=False)
    return send_file(out, as_attachment=True, download_name=f"daily_report_{safe_day}.xlsx")

@app.route("/api/export-selected", methods=["POST"])
def api_export_selected():
    ids = []
    for x in request.form.getlist("purchase_ids"):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    if not ids:
        flash("Please select at least one entry to export.", "error")
        return redirect(request.referrer or url_for("purchases"))

    con = db()
    placeholders = ",".join("?" for _ in ids)
    df = pd.read_sql_query(
        f"SELECT * FROM purchases WHERE id IN ({placeholders}) ORDER BY purchase_date, id", con, params=ids
    )
    con.close()
    out = os.path.join(BASE_DIR, "mandi_selected_export.xlsx")
    df.to_excel(out, index=False)
    return send_file(out, as_attachment=True, download_name="mandi_selected_purchases.xlsx")

@app.route("/api/export")
def api_export():
    con=db()
    df=pd.read_sql_query("SELECT * FROM purchases ORDER BY purchase_date,id",con)
    con.close()
    out=os.path.join(BASE_DIR,"mandi_purchase_export.xlsx")
    df.to_excel(out,index=False)
    return redirect(url_for("download_export"))

@app.route("/download-export")
def download_export():
    return send_file(os.path.join(BASE_DIR,"mandi_purchase_export.xlsx"), as_attachment=True)


@app.route('/analytics')
def analytics():
    con=db(); grain=request.args.get('grain',''); date_from=request.args.get('date_from',''); date_to=request.args.get('date_to','')
    where="WHERE 1=1"; params=[]
    if grain: where += ' AND grain=?'; params.append(grain)
    if date_from: where += ' AND purchase_date>=?'; params.append(date_from)
    if date_to: where += ' AND purchase_date<=?'; params.append(date_to)
    daily=con.execute(f"SELECT purchase_date day,SUM(quantity_quintal) qty,SUM(amount) amount,SUM(amount)/NULLIF(SUM(quantity_quintal),0) avg_rate FROM purchases {where} GROUP BY purchase_date ORDER BY purchase_date",params).fetchall()
    grains=con.execute(f"SELECT grain,SUM(quantity_quintal) qty,SUM(amount) amount,SUM(amount)/NULLIF(SUM(quantity_quintal),0) avg_rate FROM purchases {where} GROUP BY grain ORDER BY qty DESC",params).fetchall()
    mandis=con.execute(f"SELECT COALESCE(NULLIF(mandi,''),'Not specified') mandi,SUM(quantity_quintal) qty,SUM(amount) amount,SUM(amount)/NULLIF(SUM(quantity_quintal),0) avg_rate FROM purchases {where} GROUP BY mandi ORDER BY qty DESC",params).fetchall()
    con.close(); return render_template('analytics.html',daily=[dict(x) for x in daily],grains=[dict(x) for x in grains],mandis=[dict(x) for x in mandis],grain=grain,date_from=date_from,date_to=date_to,grain_options=[r['grain'] for r in grains])

@app.route('/sales', methods=['GET','POST'])
def sales():
    con=db()
    if request.method=='POST':
        q=to_number(request.form.get('quantity_quintal')); rate=to_number(request.form.get('rate'))
        if not request.form.get('sale_date') or not request.form.get('grain') or q<=0 or rate<=0:
            flash('Please enter sale date, grain, quantity and rate correctly.','error')
        else:
            con.execute('INSERT INTO sales (sale_date,grain,variety,buyer,quantity_quintal,rate,amount,notes) VALUES (?,?,?,?,?,?,?,?)',(request.form.get('sale_date'),request.form.get('grain'),request.form.get('variety',''),request.form.get('buyer',''),q,rate,q*rate,request.form.get('notes',''))); con.commit(); flash('Sale recorded.','success')
        con.close(); return redirect(url_for('sales'))
    rows=con.execute('SELECT * FROM sales ORDER BY sale_date DESC,id DESC').fetchall(); con.close(); return render_template('sales.html',rows=rows)

@app.route('/sales/delete/<int:sale_id>',methods=['POST'])
def delete_sale(sale_id):
    con=db(); con.execute('DELETE FROM sales WHERE id=?',(sale_id,)); con.commit(); con.close(); flash('Sale deleted.','success'); return redirect(url_for('sales'))

@app.route('/inventory')
def inventory():
    con=db()
    purchases=con.execute('SELECT grain,SUM(quantity_quintal) qty,SUM(amount) amount FROM purchases GROUP BY grain').fetchall()
    sales_rows=con.execute('SELECT grain,SUM(quantity_quintal) qty,SUM(amount) amount FROM sales GROUP BY grain').fetchall()
    sm={r['grain']:dict(r) for r in sales_rows}; out=[]
    for r in purchases:
        sold=sm.get(r['grain'],{}).get('qty',0) or 0; out.append({'grain':r['grain'],'purchased_qty':r['qty'] or 0,'purchase_amount':r['amount'] or 0,'sold_qty':sold,'sale_amount':sm.get(r['grain'],{}).get('amount',0) or 0,'stock_qty':(r['qty'] or 0)-sold})
    for k,r in sm.items():
        if not any(x['grain']==k for x in out): out.append({'grain':k,'purchased_qty':0,'purchase_amount':0,'sold_qty':r['qty'] or 0,'sale_amount':r['amount'] or 0,'stock_qty':-(r['qty'] or 0)})
    # estimated P/L on sold quantity using grain weighted purchase average
    for x in out:
        pq=x['purchased_qty']; pa=x['purchase_amount']; x['cost_sold']=x['sold_qty']*(pa/pq if pq else 0); x['profit']=x['sale_amount']-x['cost_sold']
    con.close(); return render_template('inventory.html',rows=out)

@app.route('/costs')
def costs():
    con=db(); rows=con.execute("SELECT grain,SUM(quantity_quintal) qty,SUM(amount) amount,SUM(COALESCE(mandi_fee,0)+COALESCE(nirashrit_fee,0)+COALESCE(transport_cost,0)+COALESCE(labour_cost,0)+COALESCE(other_cost,0)) extra FROM purchases GROUP BY grain ORDER BY qty DESC").fetchall(); con.close()
    out=[]
    for r in rows:
        qty=float(r['qty'] or 0); amount=float(r['amount'] or 0); extra=float(r['extra'] or 0); total=amount+extra
        out.append({'grain':r['grain'],'qty':qty,'amount':amount,'extra':extra,'total':total,'cost_per_qtl':total/qty if qty else 0})
    return render_template('costs.html',rows=out)

@app.route('/reports')
def reports(): return render_template('reports.html')

@app.route('/reports/export-pdf')
def export_pdf():
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    con=db(); rows=con.execute('SELECT purchase_date,grain,mandi,quantity_quintal,rate,amount FROM purchases ORDER BY purchase_date,id').fetchall(); con.close()
    out=os.path.join(BASE_DIR,'mandi_purchase_report.pdf'); doc=SimpleDocTemplate(out,pagesize=landscape(A4)); st=getSampleStyleSheet(); story=[Paragraph('Mandi Purchase Report',st['Title']),Spacer(1,12)]
    data=[['Date','Grain','Mandi','Qty (Qtl)','Rate','Amount']]+[[r['purchase_date'],r['grain'],r['mandi'] or '',f"{r['quantity_quintal']:.2f}",f"₹{r['rate']:.2f}",f"₹{r['amount']:.2f}"] for r in rows]
    t=Table(data,repeatRows=1); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#0b7f5b')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),0.25,colors.grey),('FONTSIZE',(0,0),(-1,-1),8),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f4f8f6')])]))
    story.append(t); doc.build(story); return send_file(out,as_attachment=True,download_name='mandi_purchase_report.pdf')

@app.route('/backup')
def backup():
    profile=current_profile(); con=db(); dbfile=db_path_for_profile(profile); con.close()
    out=os.path.join(BASE_DIR,f"mandi_backup_profile_{profile['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(dbfile): z.write(dbfile,os.path.basename(dbfile))
        if os.path.exists(PROFILE_REGISTRY_DB): z.write(PROFILE_REGISTRY_DB,os.path.basename(PROFILE_REGISTRY_DB))
    return send_file(out,as_attachment=True,download_name=os.path.basename(out))

@app.route('/restore',methods=['POST'])
def restore_backup():
    f=request.files.get('backup_file')
    if not f or not f.filename.lower().endswith('.zip'): flash('Please choose a ZIP backup.','error'); return redirect(url_for('reports'))
    profile=current_profile(); target=db_path_for_profile(profile)
    try:
        with zipfile.ZipFile(f) as z:
            candidates=[n for n in z.namelist() if n.endswith('.db') and os.path.basename(target)==os.path.basename(n)]
            if not candidates: flash('This backup does not contain the active profile database.','error'); return redirect(url_for('reports'))
            tmp=target+'.restore.tmp';
            with z.open(candidates[0]) as src, open(tmp,'wb') as dst: shutil.copyfileobj(src,dst)
        # Validate SQLite file before replacing current database.
        test=sqlite3.connect(tmp); test.execute('PRAGMA integrity_check').fetchone(); test.close(); os.replace(tmp,target); init_db(target); flash('Active profile restored successfully.','success')
    except Exception as e: flash(f'Restore failed: {e}','error')
    return redirect(url_for('reports'))

@app.route('/settings',methods=['GET','POST'])
def settings_page():
    st=load_settings()
    if request.method=='POST':
        st['sync_folder']=(request.form.get('sync_folder') or '').strip(); st['auto_sync']=bool(request.form.get('auto_sync')); st['login_enabled']=bool(request.form.get('login_enabled')); save_settings(st); flash('Settings saved.','success'); return redirect(url_for('settings_page'))
    return render_template('settings.html',settings=st)

@app.route('/sync-now',methods=['POST'])
def sync_now():
    st=load_settings(); ins,dup,skip,errors=sync_folder(st.get('sync_folder','')); flash(f'Sync complete: {ins} new rows, {dup} duplicates, {skip} skipped.','success')
    for e in errors: flash(e,'error')
    return redirect(url_for('settings_page'))

@app.route('/eanugya')
def eanugya(): return render_template('eanugya.html')

@app.route('/manifest.webmanifest')
def manifest():
    return send_from_directory(BASE_DIR, 'manifest.webmanifest', mimetype='application/manifest+json')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(BASE_DIR, 'service-worker.js', mimetype='application/javascript')

@app.route('/api/chart-data')
def chart_data():
    con=db(); rows=con.execute('SELECT purchase_date day,SUM(amount)/NULLIF(SUM(quantity_quintal),0) avg_rate,SUM(quantity_quintal) qty,SUM(amount) amount FROM purchases WHERE purchase_date<>\'\' GROUP BY purchase_date ORDER BY purchase_date').fetchall(); con.close(); return jsonify([dict(r) for r in rows])

# Create the profile registry and initialize every profile database.
init_profile_registry()
for _profile in get_profiles():
    init_db(db_path_for_profile(_profile))

@app.context_processor
def inject_profile_context():
    return {"active_profile": current_profile(), "all_profiles": get_profiles()}

@app.route("/profiles")
def profiles():
    return render_template("profiles.html")

@app.route("/switch-profile/<int:profile_id>")
def switch_profile(profile_id):
    profile = get_profile(profile_id)
    if profile is None:
        flash("Profile not found.", "error")
        return redirect(url_for("profiles"))
    session["profile_id"] = profile["id"]
    init_db(db_path_for_profile(profile))
    flash(f"Switched to {profile['name']}. Its data is completely separate from other profiles.", "success")
    return redirect(url_for("dashboard"))

@app.route("/profiles/create", methods=["POST"])
def create_profile():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Please enter a profile name.", "error")
        return redirect(url_for("profiles"))
    con = registry_db()
    try:
        cur = con.execute("INSERT INTO profiles (name, db_filename) VALUES (?, ?)", (name, "__TEMP__"))
        profile_id = cur.lastrowid
        db_filename = f"mandi_profile_{profile_id}.db"
        con.execute("UPDATE profiles SET db_filename=? WHERE id=?", (db_filename, profile_id))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        con.close()
        flash("A profile with this name already exists. Please choose another name.", "error")
        return redirect(url_for("profiles"))
    con.close()
    profile = get_profile(profile_id)
    init_db(db_path_for_profile(profile))
    session["profile_id"] = profile_id
    flash(f"{name} created. You are now working inside this separate database.", "success")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
