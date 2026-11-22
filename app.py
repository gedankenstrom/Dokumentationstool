from flask import Flask, Response, abort, redirect, render_template_string, request, session, url_for
from werkzeug.utils import secure_filename
import csv
import fcntl
import io
import json
import os
import re
import sqlite3
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

app = Flask(__name__)
DB_PATH = os.environ.get("DATABASE_PATH", "/data/inventory.db")

def load_secret_key():
    configured_key = os.environ.get("SECRET_KEY")
    if configured_key:
        return configured_key
    data_dir = os.path.dirname(DB_PATH)
    os.makedirs(data_dir, exist_ok=True)
    key_path = os.path.join(data_dir, "flask_secret_key")
    lock_path = os.path.join(data_dir, ".flask_secret_key.lock")
    with open(lock_path, "a+b") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            with open(key_path, "rb") as key_file:
                key = key_file.read()
                if key:
                    return key
        except FileNotFoundError:
            pass
        key = os.urandom(32)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(key)
            key_file.flush()
            os.fsync(key_file.fileno())
        return key

app.secret_key = load_secret_key()
FIELDS = [
    ("ap_model", "AP Modell"),
    ("serial_number", "Seriennummer"),
    ("mac_address", "MAC Adresse"),
    ("distribution", "Datenverteiler"),
    ("outlet_port", "Datendose / Port"),
    ("switch_name", "Switch"),
    ("switch_port", "Switch Port"),
    ("room", "Raum"),
]

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS access_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ap_model TEXT NOT NULL DEFAULT '', serial_number TEXT NOT NULL DEFAULT '',
        mac_address TEXT NOT NULL DEFAULT '', distribution TEXT NOT NULL DEFAULT '',
        outlet_port TEXT NOT NULL DEFAULT '', switch_name TEXT NOT NULL DEFAULT '',
        switch_port TEXT NOT NULL DEFAULT '', room TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        street TEXT NOT NULL,
        house_number TEXT NOT NULL,
        inventory_number TEXT NOT NULL DEFAULT '',
        default_ap_model TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    site_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sites)")}
    if "inventory_number" not in site_columns:
        conn.execute("ALTER TABLE sites ADD COLUMN inventory_number TEXT NOT NULL DEFAULT ''")
    if "default_ap_model" not in site_columns:
        conn.execute("ALTER TABLE sites ADD COLUMN default_ap_model TEXT NOT NULL DEFAULT ''")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(access_points)")}
    if "site_id" not in columns:
        conn.execute("ALTER TABLE access_points ADD COLUMN site_id INTEGER REFERENCES sites(id)")
        legacy_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_settings'").fetchone()
        old_site = conn.execute("SELECT street, house_number FROM site_settings WHERE id=1").fetchone() if legacy_table else None
        old_count = conn.execute("SELECT COUNT(*) FROM access_points").fetchone()[0]
        if old_site or old_count:
            street = old_site["street"] if old_site else ""
            house_number = old_site["house_number"] if old_site else ""
            cursor = conn.execute("INSERT INTO sites (street, house_number) VALUES (?, ?)", (street, house_number))
            conn.execute("UPDATE access_points SET site_id=? WHERE site_id IS NULL", (cursor.lastrowid,))
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(access_points)")}
    if "doc_type_id" not in columns:
        conn.execute("ALTER TABLE access_points ADD COLUMN doc_type_id INTEGER")
    if "custom_data" not in columns:
        conn.execute("ALTER TABLE access_points ADD COLUMN custom_data TEXT NOT NULL DEFAULT '{}'")
    conn.execute("CREATE TABLE IF NOT EXISTS documentation_types (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS documentation_fields (id INTEGER PRIMARY KEY AUTOINCREMENT, type_id INTEGER NOT NULL REFERENCES documentation_types(id) ON DELETE CASCADE, field_key TEXT NOT NULL, label TEXT NOT NULL, required INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0, is_custom INTEGER NOT NULL DEFAULT 0, remember_values INTEGER NOT NULL DEFAULT 0, UNIQUE(type_id, field_key))")
    conn.execute("CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY)")
    conn.execute("INSERT OR IGNORE INTO documentation_types (name) VALUES ('Access Point')")
    ap_type_id = conn.execute("SELECT id FROM documentation_types WHERE name='Access Point'").fetchone()[0]
    if not conn.execute("SELECT 1 FROM app_migrations WHERE name='access_point_fields_seeded'").fetchone():
        for position, (field_key, label) in enumerate(FIELDS):
            conn.execute("INSERT OR IGNORE INTO documentation_fields (type_id,field_key,label,required,enabled,position,is_custom) VALUES (?,?,?,?,?,?,0)", (ap_type_id,field_key,label,1 if field_key=='ap_model' else 0,1,position))
        conn.execute("INSERT INTO app_migrations (name) VALUES ('access_point_fields_seeded')")
    field_columns = {row["name"] for row in conn.execute("PRAGMA table_info(documentation_fields)")}
    if "remember_values" not in field_columns:
        conn.execute("ALTER TABLE documentation_fields ADD COLUMN remember_values INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE TABLE IF NOT EXISTS site_field_defaults (site_id INTEGER NOT NULL, type_id INTEGER NOT NULL, field_key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(site_id,type_id,field_key))")
    if not conn.execute("SELECT 1 FROM app_migrations WHERE name='ap_model_uses_managed_dropdown'").fetchone():
        conn.execute("UPDATE documentation_fields SET remember_values=1 WHERE type_id=? AND field_key='ap_model'", (ap_type_id,))
        conn.execute("INSERT INTO app_migrations (name) VALUES ('ap_model_uses_managed_dropdown')")
    conn.execute("UPDATE access_points SET doc_type_id=? WHERE doc_type_id IS NULL", (ap_type_id,))
    conn.commit()
    return conn


def get_types(conn):
    return conn.execute("SELECT * FROM documentation_types ORDER BY id").fetchall()


def get_site_documentation_counts(conn, site_id):
    if not site_id:
        return []
    return conn.execute(
        "SELECT types.name, COUNT(records.id) AS total "
        "FROM documentation_types AS types "
        "JOIN access_points AS records "
        "ON records.doc_type_id=types.id AND records.site_id=? "
        "GROUP BY types.id, types.name ORDER BY types.id",
        (site_id,),
    ).fetchall()


def get_fields(conn, type_id):
    rows = conn.execute("SELECT field_key,label,required,is_custom FROM documentation_fields WHERE type_id=? AND enabled=1 ORDER BY position,id", (type_id,)).fetchall()
    return [(row["field_key"], row["label"]) for row in rows], {row["field_key"] for row in rows if row["required"]}, {row["field_key"] for row in rows if row["is_custom"]}


def followup_field_role(field_key, label):
    normalized = re.sub(r"[^a-z0-9]", "", (field_key + " " + label).casefold())
    if re.search(r"rqnummer|rqnumber|hostname|hostnamen|hostname", normalized):
        return "rq_number"
    if re.search(r"vnummer|vnumber|benutzerkennung", normalized):
        return "v_number"
    return None


def get_remember_fields(conn, type_id):
    return {row["field_key"] for row in conn.execute("SELECT field_key FROM documentation_fields WHERE type_id=? AND enabled=1 AND remember_values=1", (type_id,))}

def get_site_field_defaults(conn, site_id, type_id, remember_keys, current_type=None, current_site=None):
    defaults = {}
    if site_id:
        rows = conn.execute("SELECT field_key,value FROM site_field_defaults WHERE site_id=? AND type_id=?", (site_id,type_id)).fetchall()
        defaults = {row["field_key"]: row["value"] for row in rows if row["field_key"] in remember_keys}
    if current_type and current_type["name"] == "Access Point" and current_site and "ap_model" in remember_keys:
        defaults.setdefault("ap_model", current_site["default_ap_model"] or "")
    return defaults

def get_field_suggestions(conn, type_id, keys):
    values = {key: set() for key in keys}
    if not keys:
        return {}
    rows = conn.execute("SELECT * FROM access_points WHERE doc_type_id=?", (type_id,)).fetchall()
    if "ap_model" in keys:
        ap_type = conn.execute("SELECT name FROM documentation_types WHERE id=?", (type_id,)).fetchone()
        if ap_type and ap_type["name"] == "Access Point":
            for site in conn.execute("SELECT default_ap_model FROM sites WHERE TRIM(default_ap_model)<>''"):
                values["ap_model"].add(site["default_ap_model"].strip())
    base_keys = {key for key, _ in FIELDS}
    for row in rows:
        data = dict(row)
        try:
            custom = json.loads(data.get("custom_data") or "{}")
        except (TypeError, ValueError):
            custom = {}
        for key in keys:
            value = data.get(key) if key in base_keys else custom.get(key)
            if value and str(value).strip():
                values[key].add(str(value).strip())
    return {key: sorted(items, key=str.casefold)[:100] for key, items in values.items()}

def flatten_row(row):
    data = dict(row)
    try:
        data.update(json.loads(data.get("custom_data") or "{}"))
    except (TypeError, ValueError):
        pass
    return data

PAGE = r'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#102b46"><meta name="format-detection" content="telephone=no"><title>Dokumentation</title>
<style>
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172b3a;background:#f2f6f9;font-synthesis:none}*{box-sizing:border-box}body{margin:0}.top{background:#102b46;color:white;padding:24px max(20px,calc((100% - 1100px)/2));}.top small{color:#a9c8df;letter-spacing:.12em;text-transform:uppercase;font-weight:700}.top h1{margin:6px 0 0;font-size:clamp(1.4rem,4vw,2rem)}main{max-width:1100px;margin:26px auto;padding:0 18px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}.button{border:0;border-radius:10px;padding:12px 16px;background:#087f8c;color:white;font-weight:700;text-decoration:none;cursor:pointer;font-size:.95rem}.button.secondary{background:white;color:#254357;border:1px solid #d6e1e8}.button.danger{background:#fff0ed;color:#a63f2d}.card{background:#fff;border:1px solid #e0e8ee;border-radius:16px;padding:20px;box-shadow:0 5px 18px #16324a0a;margin-bottom:18px}.card h2{margin:0 0 18px;font-size:1.1rem}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field label{display:block;font-size:.83rem;font-weight:700;color:#405a6b;margin:0 0 6px}.field input,.field select{width:100%;height:46px;padding:10px 12px;border:1px solid #cad8e1;border-radius:9px;font:inherit;background:#fff}.field input[hidden]{display:none}.field input:focus,.field select:focus,.search:focus{outline:3px solid #087f8c33;border-color:#087f8c}.form-foot{display:flex;gap:10px;margin-top:18px}.followup-note{padding:11px 13px;border:1px solid #c9e8e5;border-radius:10px;background:#e8f5f4;color:#176c70;font-size:.9rem}.followup-option{display:flex;align-items:center;gap:10px;margin-top:16px;padding:12px;border:1px solid #d6e1e8;border-radius:10px;background:#f7fafb;font-weight:650}.followup-option input{flex:none;width:20px;height:20px;margin:0;accent-color:#087f8c}.followup-type-picker{margin-top:10px;padding:12px;border:1px solid #d6e1e8;border-radius:10px;background:#fff}.followup-type-picker[hidden]{display:none}.followup-type-picker label{display:block;margin-bottom:6px;color:#405a6b;font-size:.85rem;font-weight:700}.followup-type-picker select{width:100%;height:44px;padding:9px 11px;border:1px solid #cad8e1;border-radius:9px;background:#fff;font:inherit}.searchrow{display:flex;gap:10px;margin-bottom:14px}.search{flex:1;min-width:0;height:44px;border:1px solid #d3dfe7;border-radius:9px;padding:10px 12px;font:inherit}.count{color:#617988;font-size:.9rem;margin:0 0 12px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:.9rem}th{text-align:left;color:#5b7180;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;padding:11px 10px;border-bottom:1px solid #e1e9ee}td{padding:13px 10px;border-bottom:1px solid #edf1f4;vertical-align:top}tbody tr:last-child td{border:0}.actions-cell{white-space:nowrap}.link{color:#087f8c;font-weight:700;text-decoration:none}.empty{text-align:center;padding:30px;color:#647987}.flash{padding:12px;border-radius:9px;background:#e7f5f2;color:#12695d;margin-bottom:15px}.mobile-list{display:none}.mobile-item{background:#f7f9fb;border:1px solid #e1e9ee;border-radius:12px;padding:13px}.device-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.device-title{min-width:0}.device-model{font-weight:750;font-size:1rem;line-height:1.25;overflow-wrap:anywhere}.device-room{font-size:.84rem;color:#617988;margin-top:4px;overflow-wrap:anywhere}.device-edit{flex:none;border-radius:8px;background:#e5f4f3;color:#087f8c;padding:8px 10px;text-decoration:none;font-size:.85rem;font-weight:700}.device-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:12px}.device-field{min-width:0;background:white;border:1px solid #e7edf1;border-radius:8px;padding:8px 9px}.device-label{display:block;color:#718491;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}.device-value{display:block;color:#253f50;font-size:.88rem;line-height:1.3;overflow-wrap:anywhere}.device-delete{margin:10px 0 0 auto;display:block;border:0;background:none;color:#a45143;font-size:.82rem;cursor:pointer;padding:5px}.site-delete-search{margin:10px 0 12px;padding:12px;border:1px solid #d6e5eb;border-radius:12px;background:#f5f9fb}.site-delete-search label{display:block;margin:0 0 7px;color:#405a6b;font-size:.82rem;font-weight:750}.site-delete-search input{height:44px;border-color:#bfd1db;background:#fff;box-shadow:0 1px 3px #102b460c}.site-admin-list{display:flex;flex-direction:column;gap:8px}.site-admin-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border:1px solid #e1e9ee;border-radius:11px;background:#f8fafb;font-size:.9rem}.site-admin-row[hidden]{display:none}.site-admin-info{min-width:0;display:flex;flex-direction:column;gap:6px}.site-admin-address{font-weight:750;overflow-wrap:anywhere}.site-admin-meta{display:flex;flex-wrap:wrap;gap:6px}.site-admin-tag{display:inline-flex;align-items:center;border:1px solid #dce7ed;border-radius:999px;background:#fff;color:#526c7b;padding:4px 8px;font-size:.78rem;font-weight:650}.site-admin-count{background:#eaf0f4;color:#28485a}.site-admin-row form{margin:0;flex:none}.site-admin-row .device-delete{display:inline-flex;align-items:center;justify-content:center;min-height:40px;margin:0;padding:9px 12px;border:1px solid #f0d5ce;border-radius:9px;background:#fff0ed;color:#a63f2d;font-size:.85rem;font-weight:700;white-space:nowrap}.site-admin-controls{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;align-items:center}.site-admin-controls .search{height:38px}.site-admin-row .device-delete{margin:0}.note{color:#708391;font-size:.85rem;margin-top:13px}.site-tools{margin-top:12px;border-top:1px solid #e7edf1;padding-top:10px}.site-tools summary{color:#087f8c;font-weight:700;cursor:pointer;padding:5px 0}.site-tools h3{font-size:.95rem;margin:16px 0 10px}.site-tools-content{padding-top:3px}.site-doc-summary{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}.site-doc-tag{display:inline-flex;align-items:center;border:1px solid #c9e8e5;border-radius:999px;background:#e8f5f4;color:#087f8c;padding:6px 10px;font-size:.82rem;font-weight:700}.site-doc-empty{color:#708391;font-size:.85rem}
@media(max-width:700px){main{margin:17px auto;padding:0 13px}.top{padding:20px 16px}.card{padding:16px;border-radius:13px}.grid{grid-template-columns:1fr;gap:12px}.desktop{display:none}.mobile-list{display:flex;flex-direction:column;gap:10px}.searchrow .button{padding:11px 12px}.form-foot .button{flex:1}.actions .button{flex:1;text-align:center}.searchrow{gap:7px}.doctype-row{display:flex;flex-direction:column;align-items:stretch}.doctype-row .search{width:100%;flex:none}.doctype-row .button{text-align:center}.site-admin-row{flex-direction:column;align-items:stretch;gap:10px}.site-admin-row form,.site-admin-row .device-delete{width:100%}.site-admin-row .device-delete{white-space:normal;text-align:center}.site-admin-address{font-size:.98rem}}
</style></head><body><header class="top"><small>IT Inventar</small><h1>Dokumentation</h1>{% if current_site %}<div style="margin-top:6px;color:#d1e2ef">{{ current_site.street }} {{ current_site.house_number }}</div>{% if current_site.inventory_number %}<div style="margin-top:3px;color:#a9c8df;font-size:.85rem">IM-/VITKO-Nummer · {{ current_site.inventory_number }}</div>{% endif %}{% else %}<div style="margin-top:6px;color:#d1e2ef">Bitte zuerst einen Standort auswählen</div>{% endif %}</header><main>
{% with messages = get_flashed_messages() %}{% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}{% endwith %}
<div class="actions">{% if current_site %}<a class="button" href="#entry">＋ {{ current_type.name }} eintragen</a>{% else %}<a class="button" href="#new-site" onclick="document.getElementById('site-tools').open=true">＋ Standort anlegen</a>{% endif %}{% if current_site %}<button class="button secondary export-choice" type="button" data-url="{{ url_for('export_csv') }}" data-type="csv">↓ CSV · alle Arten</button><button class="button secondary export-choice" type="button" data-url="{{ url_for('export_xlsx') }}" data-type="xlsx">↓ Excel · alle Arten</button>{% endif %}</div>
<section class="card site-card" id="sites"><h2>Standort</h2>{% if sites %}<form autocomplete="off" method="post" action="{{ url_for('select_site') }}"><div class="searchrow"><select autocomplete="off" class="search" name="site_id" onchange="this.form.submit()"><option value="">Standort auswählen …</option>{% for s in sites %}<option value="{{ s.id }}" {% if current_site and s.id==current_site.id %}selected{% endif %}>{{ s.street }} {{ s.house_number }}{% if s.inventory_number %} · {{ s.inventory_number }}{% endif %}</option>{% endfor %}</select></div></form>{% if current_site %}<div class="site-doc-summary" aria-label="Dokumentierte Geräte nach Dokumentationsart">{% if site_documentation_counts %}{% for documentation_count in site_documentation_counts %}<span class="site-doc-tag">{{ documentation_count.name }} {{ documentation_count.total }}</span>{% endfor %}{% else %}<span class="site-doc-empty">Noch keine Geräte dokumentiert</span>{% endif %}</div>{% endif %}{% else %}<p class="note">Lege zuerst deinen aktuellen Standort an.</p>{% endif %}
<details class="site-tools" id="site-tools"><summary>Standorte verwalten</summary><div class="site-tools-content">
{% if current_site %}<h3>Aktuellen Standort bearbeiten</h3><form autocomplete="off" method="post" action="{{ url_for('update_site') }}"><div class="grid"><div class="field"><label for="edit_street">Straße</label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" id="edit_street" name="street" value="{{ current_site.street }}" required></div><div class="field"><label for="edit_house_number">Hausnummer</label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" id="edit_house_number" name="house_number" value="{{ current_site.house_number }}" required></div><div class="field"><label for="edit_inventory_number">IM-/VITKO-Nummer (optional)</label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" id="edit_inventory_number" name="inventory_number" value="{{ current_site.inventory_number }}" placeholder="z. B. IM 123456"></div></div><div class="form-foot"><button class="button secondary" type="submit">Adresse aktualisieren</button></div></form>{% endif %}
<h3 id="new-site">Neuen Standort anlegen</h3><form autocomplete="off" method="post" action="{{ url_for('create_site') }}"><div class="grid"><div class="field"><label for="new_street">Straße</label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" id="new_street" name="street" list="known-streets" placeholder="z. B. Hauptstraße" required><datalist id="known-streets">{% for street in known_streets %}<option value="{{ street }}">{% endfor %}</datalist></div><div class="field"><label for="new_house_number">Hausnummer</label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" id="new_house_number" name="house_number" list="known-house-numbers" placeholder="z. B. 12a" required><datalist id="known-house-numbers"></datalist></div><div class="field"><label for="new_inventory_number">IM-/VITKO-Nummer (optional)</label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" id="new_inventory_number" name="inventory_number" placeholder="z. B. IM 123456"></div></div><div class="form-foot"><button class="button" type="submit">Anlegen und öffnen</button></div></form>
</div>
{% if sites %}<h3>Standorte löschen</h3><div class="field site-delete-search"><label for="site-delete-search">Standort suchen</label><input id="site-delete-search" type="search" placeholder="Adresse oder IM-/VITKO-Nummer" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></div><p class="note" id="site-delete-no-results" hidden>Kein passender Standort gefunden.</p><div class="site-admin-list">{% for s in sites %}{% set assigned = site_counts.get(s.id, 0) %}<div class="site-admin-row" data-search="{{ (s.street ~ ' ' ~ s.house_number ~ ' ' ~ s.inventory_number)|lower }}"><div class="site-admin-info"><strong class="site-admin-address">{{ s.street }} {{ s.house_number }}</strong><div class="site-admin-meta"><span class="site-admin-tag">{% if s.inventory_number %}IM-/VITKO: {{ s.inventory_number }}{% else %}keine IM-/VITKO-Nr.{% endif %}</span><span class="site-admin-tag site-admin-count">{{ assigned }} {{ 'Gerät' if assigned==1 else 'Geräte' }}</span></div></div><form autocomplete="off" method="post" action="{{ url_for('delete_site', site_id=s.id) }}" onsubmit="return confirm('Standort und alle zugeordneten Geräte löschen? Dieser Vorgang kann nicht rückgängig gemacht werden.')"><button class="device-delete" type="submit">Standort &amp; Geräte löschen</button></form></div>{% endfor %}</div>{% endif %}


</details></section>
<section class="card"><h2>Dokumentationsart</h2><form autocomplete="off" method="post" action="{{ url_for('select_documentation_type') }}"><div class="searchrow doctype-row"><select autocomplete="off" class="search" name="type_id" onchange="this.form.submit()">{% for type in types %}<option value="{{ type.id }}" {% if type.id==current_type.id %}selected{% endif %}>{{ type.name }}</option>{% endfor %}</select><a class="button secondary" href="{{ url_for('manage_templates', type_id=current_type.id) }}">Vorlagen verwalten</a></div></form></section>
{% if current_site %}<section class="card" id="entry"><h2>{{ 'Eintrag bearbeiten' if item else current_type.name ~ ' dokumentieren' }}</h2>{% if followup_context and not item %}<p class="followup-note">Folgegerät für denselben Anwender. V-Nummer und RQ-Nummer vom Notebook sind vorgemerkt.</p>{% endif %}<form method="post" autocomplete="off" action="{{ url_for('save', record_id=item.id) if item else url_for('save') }}"><div class="grid">
{% for key,label in fields %}{% if key in remember_keys %}<div class="field"><label for="{{ key }}_choice">{{ label }}</label><select autocomplete="off" class="remembered-select" id="{{ key }}_choice" name="{{ key }}_choice" data-input="{{ key }}_new" data-required="{% if key in required_keys %}1{% else %}0{% endif %}" onchange="toggleRememberedField(this)" {% if key in required_keys %}required{% endif %}><option value="" {% if (item and not item[key]) or (not item and not field_defaults.get(key)) %}selected{% endif %}>Wert auswählen …</option>{% for option in suggestions.get(key,[]) %}<option value="{{ option }}" {% if (item and item[key]==option) or (not item and field_defaults.get(key)==option) %}selected{% endif %}>{{ option }}</option>{% endfor %}<option value="__new__" {% if (item and item[key] and item[key] not in suggestions.get(key,[])) or (not item and not field_defaults.get(key) and not suggestions.get(key,[])) %}selected{% endif %}>Neuen Wert eingeben …</option></select><input id="{{ key }}_new" name="{{ key }}_new" value="{{ item[key] if item else field_defaults.get(key,'') }}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="Neuen Wert eingeben" {% if (item and (not item[key] or item[key] in suggestions.get(key,[]))) or (not item and (field_defaults.get(key) or suggestions.get(key,[]))) %}hidden{% endif %} {% if item and item[key] and item[key] not in suggestions.get(key,[]) and key in required_keys %}required{% endif %}></div>{% elif key=='ap_model' %}<div class="field"><label for="ap_model">{{ label }}</label><input id="ap_model" name="ap_model" value="{{ item.ap_model if item else preferred_model }}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="AP-Modell eingeben" {% if key in required_keys %}required{% endif %}></div>{% elif key=='mac_address' %}<div class="field"><label for="hardware_mac">{{ label }}</label><input id="hardware_mac" name="hardware_mac" value="{{ item[key] if item else '' }}" placeholder="z. B. AA:BB:CC:DD:EE:FF" autocomplete="off" autocorrect="off" autocapitalize="characters" spellcheck="false" inputmode="text" {% if key in required_keys %}required{% endif %}></div>{% elif key=='switch_name' %}<div class="field"><label for="network_switch_device">{{ label }}</label><input id="network_switch_device" name="network_switch_device" value="{{ item[key] if item else '' }}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" {% if key in required_keys %}required{% endif %}></div>{% else %}<div class="field"><label for="{{ key }}">{{ label }}</label><input id="{{ key }}" name="{{ key }}" value="{{ item[key] if item else field_defaults.get(key,'') }}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" {% if key=='room' %}placeholder="z. B. 2. OG · Büro 2.14"{% endif %} {% if key in required_keys %}required{% endif %}></div>{% endif %}{% endfor %}
</div>{% if not item and followup_fields_present %}<label class="followup-option"><input type="checkbox" name="continue_followup" value="1" onchange="toggleFollowupType()"> Weiteres Gerät für diesen Anwender dokumentieren</label><div class="followup-type-picker" id="followup-type-picker" hidden><label for="followup_type_id">Welche Dokumentationsart folgt?</label><select id="followup_type_id" name="followup_type_id" autocomplete="off" disabled><option value="">Dokumentationsart auswählen …</option>{% for type in followup_types %}<option value="{{ type.id }}">{{ type.name }}</option>{% endfor %}</select></div>{% endif %}<div class="form-foot"><button class="button" type="submit">{{ 'Änderungen speichern' if item else 'Eintrag speichern' }}</button>{% if item %}<a class="button secondary" href="{{ url_for('index') }}">Abbrechen</a>{% endif %}</div></form></section>{% else %}<section class="card"><h2>Geräte dokumentieren</h2><div class="empty">Wähle zuerst den Standort aus, an dem du gerade arbeitest.</div></section>{% endif %}
<section class="card"><h2>Dokumentierte {{ current_type.name }}-Einträge</h2><p class="count">Gesamt: {{ total_devices }} {{ 'Gerät' if total_devices==1 else 'Geräte' }}</p>{% if current_site %}<p class="count">Standort: {{ current_site.street }} {{ current_site.house_number }}</p>{% endif %}<form autocomplete="off" class="searchrow" method="get"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" class="search" name="q" value="{{ q }}" placeholder="Einträge durchsuchen …"><button class="button" type="submit">Suchen</button>{% if q %}<a class="button secondary" href="{{ url_for('index') }}">Zurücksetzen</a>{% endif %}</form>{% if q %}<p class="count">{{ rows|length }} Treffer für „{{ q }}“</p>{% endif %}
{% if rows %}<div class="tablewrap desktop"><table><thead><tr>{% for key,label in fields %}<th>{{ label }}</th>{% endfor %}<th></th></tr></thead><tbody>{% for row in rows %}<tr>{% for key,label in fields %}<td>{{ row[key] or '–' }}</td>{% endfor %}<td class="actions-cell"><a class="link" href="{{ url_for('edit', record_id=row.id) }}">Bearbeiten</a> · <form autocomplete="off" style="display:inline" method="post" action="{{ url_for('delete', record_id=row.id) }}" onsubmit="return confirm('Diesen Eintrag löschen?')"><button class="link" style="border:0;background:none;padding:0;cursor:pointer" type="submit">Löschen</button></form></td></tr>{% endfor %}</tbody></table></div>
<div class="mobile-list">{% for row in rows %}<article class="mobile-item"><div class="device-heading"><div class="device-title"><div class="device-model">{{ row[fields[0][0]] if fields and row[fields[0][0]] else current_type.name }}</div></div><a class="device-edit" href="{{ url_for('edit', record_id=row.id) }}">Bearbeiten</a></div><div class="device-grid">{% for key,label in fields %}{% if not loop.first %}<div class="device-field"><span class="device-label">{{ label }}</span><span class="device-value">{{ row[key] or '–' }}</span></div>{% endif %}{% endfor %}</div><form autocomplete="off" method="post" action="{{ url_for('delete', record_id=row.id) }}" onsubmit="return confirm('Diesen Eintrag löschen?')"><button class="device-delete" type="submit">Eintrag löschen</button></form></article>{% endfor %}</div>
{% else %}<div class="empty">{% if q %}Keine passenden Einträge gefunden.{% else %}Noch keine Einträge für diese Dokumentationsart. Lege den ersten Eintrag oben an.{% endif %}</div>{% endif %}<p class="note">Die Daten werden lokal in einer SQLite-Datei im Docker-Datenvolume gespeichert.</p></section></main><dialog id="export-dialog" style="border:0;border-radius:16px;padding:22px;max-width:calc(100% - 28px);width:420px;box-shadow:0 18px 60px #102b4633"><h2 style="margin:0 0 8px">Export teilen</h2><p id="export-status" class="note">Export wird vorbereitet …</p><div class="form-foot"><a id="export-download" class="button secondary" href="#" download>Herunterladen</a><button id="export-share" class="button" type="button" disabled>Per E-Mail / Teilen</button></div><div class="form-foot"><button id="export-close" class="button secondary" type="button">Schließen</button></div></dialog><script>const exportDialog=document.getElementById('export-dialog');let exportFile=null,exportObjectUrl=null;document.querySelectorAll('.export-choice').forEach(button=>button.addEventListener('click',async()=>{exportFile=null;if(exportObjectUrl)URL.revokeObjectURL(exportObjectUrl);exportObjectUrl=null;const status=document.getElementById('export-status'),download=document.getElementById('export-download'),share=document.getElementById('export-share');download.removeAttribute('href');share.disabled=true;status.textContent='Export wird vorbereitet …';exportDialog.showModal();try{const response=await fetch(button.dataset.url);if(!response.ok)throw new Error();const blob=await response.blob();const disposition=response.headers.get('Content-Disposition')||'';const match=disposition.match(/filename="?([^";]+)"?/i);const filename=match?match[1]:'netzwerk-dokumentation.'+button.dataset.type;exportFile=new File([blob],filename,{type:blob.type});exportObjectUrl=URL.createObjectURL(blob);download.href=exportObjectUrl;download.download=filename;share.disabled=false;status.textContent='Wähle Herunterladen oder öffne das Teilen-Menü und wähle Mail.'}catch(error){status.textContent='Der Export konnte nicht vorbereitet werden. Bitte Seite neu laden und erneut versuchen.'}}));document.getElementById('export-share').addEventListener('click',async()=>{const status=document.getElementById('export-status');if(!exportFile)return;if(!window.isSecureContext||!navigator.share||!navigator.canShare||!navigator.canShare({files:[exportFile]})){status.textContent='Teilen mit Mail wird von diesem Browser hier nicht unterstützt. Öffne die Seite über HTTPS oder lade die Datei herunter und teile sie anschließend über die Dateien-App.';setTimeout(()=>exportDialog.close(),2500);return}try{await navigator.share({files:[exportFile],title:exportFile.name})}catch(error){if(error.name!=='AbortError')status.textContent='Teilen wurde nicht abgeschlossen. Du kannst die Datei herunterladen.'}finally{exportDialog.close()}});document.getElementById('export-close').addEventListener('click',()=>exportDialog.close());exportDialog.addEventListener('close',()=>{const urlToRevoke=exportObjectUrl;setTimeout(()=>{if(urlToRevoke)URL.revokeObjectURL(urlToRevoke);if(exportObjectUrl===urlToRevoke)exportObjectUrl=null},1200)});document.getElementById('export-download').addEventListener('click',()=>setTimeout(()=>exportDialog.close(),0));function toggleRememberedField(select){const input=document.getElementById(select.dataset.input);if(!input)return;const custom=select.value==='__new__';input.hidden=!custom;input.required=custom&&select.dataset.required==='1';}document.querySelectorAll('.remembered-select').forEach(toggleRememberedField);function toggleFollowupType(){const checkbox=document.querySelector('[name="continue_followup"]');const picker=document.getElementById('followup-type-picker');const select=document.getElementById('followup_type_id');if(!checkbox||!picker||!select)return;picker.hidden=!checkbox.checked;select.disabled=!checkbox.checked;select.required=checkbox.checked;}function toggleApModel(){const select=document.getElementById('ap_model_choice');const input=document.getElementById('ap_model_new');const custom=select.value==='__new__';input.hidden=!custom;input.required=custom;}const knownSiteAddresses={{ known_site_addresses|tojson }};const newStreetInput=document.getElementById("new_street");const newHouseInput=document.getElementById("new_house_number");const houseNumberList=document.getElementById("known-house-numbers");function updateKnownHouseNumbers(){if(!newStreetInput||!houseNumberList)return;houseNumberList.replaceChildren();const values=[...new Set(knownSiteAddresses.filter(site=>site.street===newStreetInput.value).map(site=>site.house_number))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));values.forEach(value=>{const option=document.createElement("option");option.value=value;houseNumberList.appendChild(option)});}if(newStreetInput){newStreetInput.addEventListener("input",updateKnownHouseNumbers);updateKnownHouseNumbers();}const entryForm=document.querySelector('#entry form');if(entryForm){entryForm.addEventListener('keydown',function(event){if(event.key!=='Enter'||!event.target.matches('input:not([type="hidden"]):not([hidden]),select:not([disabled]),textarea'))return;event.preventDefault();const fields=Array.from(entryForm.querySelectorAll('select:not([disabled]),input:not([type="hidden"]):not([hidden]),textarea'));const current=fields.indexOf(event.target);if(current>=0&&fields[current+1])fields[current+1].focus();});}const siteDeleteSearch=document.getElementById('site-delete-search');if(siteDeleteSearch){const siteDeleteRows=Array.from(document.querySelectorAll('.site-admin-row'));const siteDeleteEmpty=document.getElementById('site-delete-no-results');siteDeleteSearch.addEventListener('input',()=>{const query=siteDeleteSearch.value.trim().toLocaleLowerCase();let visible=0;siteDeleteRows.forEach(row=>{const match=(row.dataset.search||'').toLocaleLowerCase().includes(query);row.hidden=!match;if(match)visible++});if(siteDeleteEmpty)siteDeleteEmpty.hidden=!query||visible>0})}</script></body></html>'''

@app.route("/templates", methods=["GET", "POST"])
def manage_templates():
    conn = db()
    if request.method == "POST":
        action = request.form.get("action", "")
        type_id = request.form.get("type_id", "").strip()
        if action == "create_type":
            name = request.form.get("name", "").strip()
            if name:
                try:
                    conn.execute("INSERT INTO documentation_types (name) VALUES (?)", (name,))
                    conn.commit()
                    from flask import flash
                    flash("Dokumentationsvorlage angelegt. Füge jetzt die gewünschten Felder hinzu.")
                except sqlite3.IntegrityError:
                    from flask import flash
                    flash("Diese Dokumentationsart gibt es bereits.")
        elif action == "add_field" and type_id.isdigit():
            label = request.form.get("label", "").strip()
            if label:
                slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "feld"
                field_key = "custom_" + slug
                existing = conn.execute("SELECT 1 FROM documentation_fields WHERE type_id=? AND field_key=?", (type_id, field_key)).fetchone()
                suffix = 2
                while existing:
                    field_key = "custom_" + slug + "_" + str(suffix)
                    existing = conn.execute("SELECT 1 FROM documentation_fields WHERE type_id=? AND field_key=?", (type_id, field_key)).fetchone()
                    suffix += 1
                position = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM documentation_fields WHERE type_id=?", (type_id,)).fetchone()[0]
                conn.execute("INSERT INTO documentation_fields (type_id,field_key,label,required,enabled,position,is_custom,remember_values) VALUES (?,?,?,?,1,?,1,?)", (type_id,field_key,label,1 if request.form.get("required") else 0,position,1 if request.form.get("remember_values") else 0))
                conn.commit()
                from flask import flash
                flash("Feld hinzugefügt.")
        elif action == "update_field":
            field_id = request.form.get("field_id", "").strip()
            label = request.form.get("label", "").strip()
            move = request.form.get("move", "")
            field = conn.execute("SELECT id,field_key,is_custom FROM documentation_fields WHERE id=? AND type_id=?", (field_id,type_id)).fetchone() if field_id.isdigit() else None
            if field and request.form.get("delete_field"):
                field_key = field["field_key"]
                if field["is_custom"]:
                    for record in conn.execute("SELECT id,custom_data FROM access_points WHERE doc_type_id=?", (type_id,)).fetchall():
                        try:
                            custom_values = json.loads(record["custom_data"] or "{}")
                        except (TypeError, ValueError):
                            custom_values = {}
                        if field_key in custom_values:
                            custom_values.pop(field_key, None)
                            conn.execute("UPDATE access_points SET custom_data=? WHERE id=?", (json.dumps(custom_values,ensure_ascii=False),record["id"]))
                elif field_key in {key for key,_ in FIELDS}:
                    conn.execute("UPDATE access_points SET " + field_key + "='' WHERE doc_type_id=?", (type_id,))
                    if field_key == "ap_model":
                        conn.execute("UPDATE sites SET default_ap_model=''")
                conn.execute("DELETE FROM site_field_defaults WHERE type_id=? AND field_key=?", (type_id,field_key))
                conn.execute("DELETE FROM documentation_fields WHERE id=?", (field_id,))
                ordered = [row["id"] for row in conn.execute("SELECT id FROM documentation_fields WHERE type_id=? ORDER BY position,id", (type_id,)).fetchall()]
                for position, ordered_id in enumerate(ordered):
                    conn.execute("UPDATE documentation_fields SET position=? WHERE id=?", (position,ordered_id))
                conn.commit()
                from flask import flash
                flash("Feld und gespeicherte Werte gelöscht.")
            elif field and label:
                conn.execute("UPDATE documentation_fields SET label=?, required=?, enabled=?, remember_values=? WHERE id=?", (label,1 if request.form.get("required") else 0,1 if request.form.get("enabled") else 0,1 if request.form.get("remember_values") else 0,field_id))
                if move in ("up","down"):
                    ordered = [row["id"] for row in conn.execute("SELECT id FROM documentation_fields WHERE type_id=? ORDER BY position,id", (type_id,)).fetchall()]
                    index = ordered.index(int(field_id))
                    target = index - 1 if move == "up" else index + 1
                    if 0 <= target < len(ordered):
                        ordered[index], ordered[target] = ordered[target], ordered[index]
                    for position, ordered_id in enumerate(ordered):
                        conn.execute("UPDATE documentation_fields SET position=? WHERE id=?", (position,ordered_id))
                conn.commit()
                from flask import flash
                flash("Feld nach oben verschoben." if move == "up" else ("Feld nach unten verschoben." if move == "down" else "Feld gespeichert."))
        elif action == "delete_type" and type_id.isdigit():
            doc_type = conn.execute("SELECT name FROM documentation_types WHERE id=?", (type_id,)).fetchone()
            if doc_type and doc_type["name"] != "Access Point":
                conn.execute("DELETE FROM access_points WHERE doc_type_id=?", (type_id,))
                conn.execute("DELETE FROM site_field_defaults WHERE type_id=?", (type_id,))
                conn.execute("DELETE FROM documentation_fields WHERE type_id=?", (type_id,))
                conn.execute("DELETE FROM documentation_types WHERE id=?", (type_id,))
                conn.commit()
                if str(session.get("documentation_type_id")) == type_id:
                    session.pop("documentation_type_id", None)
                from flask import flash
                flash("Dokumentationsvorlage und alle zugehörigen Einträge gelöscht.")
            else:
                from flask import flash
                flash("Die Access-Point-Vorlage kann nicht gelöscht werden.")
        conn.close()
        return redirect(url_for("manage_templates", type_id=type_id or request.args.get("type_id", "")))
    types = get_types(conn)
    selected_id = request.args.get("type_id", "").strip()
    selected = conn.execute("SELECT * FROM documentation_types WHERE id=?", (selected_id,)).fetchone() if selected_id.isdigit() else None
    if not selected and types:
        selected = types[0]
    fields = conn.execute("SELECT * FROM documentation_fields WHERE type_id=? ORDER BY position,id", (selected["id"],)).fetchall() if selected else []
    type_counts = {row["doc_type_id"]: row["total"] for row in conn.execute("SELECT doc_type_id,COUNT(*) AS total FROM access_points GROUP BY doc_type_id")}
    conn.close()
    return render_template_string(r'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vorlagen verwalten</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f2f6f9;color:#172b3a;margin:0}main{max-width:850px;margin:22px auto;padding:0 14px}.card{background:white;border:1px solid #e0e8ee;border-radius:14px;padding:18px;margin-bottom:14px}h1{font-size:1.4rem}.field{display:grid;grid-template-columns:minmax(110px,1fr) minmax(140px,2fr) auto auto auto auto auto auto;gap:8px;align-items:center;padding:9px 0;border-top:1px solid #e7edf1}.field input[type=text],input[type=number]{min-width:0;padding:9px;border:1px solid #cad8e1;border-radius:8px;font:inherit}.btn{display:inline-block;border:0;border-radius:8px;padding:10px 13px;background:#087f8c;color:white;text-decoration:none;font-weight:700;cursor:pointer}.secondary{background:#eaf0f4;color:#28485a}.selected{background:#087f8c;color:#fff}.danger{background:#fff0ed;color:#a63f2d}.delete-field{white-space:nowrap}.move-button,.arrow-placeholder{width:40px;height:40px;padding:0;display:inline-flex;align-items:center;justify-content:center}.delete-template{margin-top:18px;padding-top:14px;border-top:1px solid #e7edf1}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.note{color:#617988;font-size:.9rem}.flash{background:#e7f5f2;padding:10px;border-radius:8px;margin-bottom:10px}@media(max-width:650px){.field{grid-template-columns:minmax(0,1fr) 40px 40px;gap:8px}.field>label:first-of-type{grid-column:1/-1}.field>input[type=text]{grid-column:1/-1;width:100%}.field>label:nth-of-type(2){grid-column:1/2}.field>label:nth-of-type(3){grid-column:2/4}.field>.save-field{grid-column:1/2;width:100%;min-height:40px}.field>.move-button,.field>.arrow-placeholder{grid-row:auto}.field>.delete-field{grid-column:1/-1;width:100%;min-height:40px}}</style></head><body><main><h1>Dokumentationsvorlagen</h1>{% with messages=get_flashed_messages() %}{% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}{% endwith %}<p><a class="btn secondary" href="{{ url_for('index') }}">← Zur Erfassung</a></p><section class="card"><h2>Vorlagen auswählen</h2><div class="row">{% for t in types %}<a class="btn {{ 'selected' if selected and selected.id==t.id else 'secondary' }}" href="{{ url_for('manage_templates',type_id=t.id) }}">{{ t.name }} · {{ type_counts.get(t.id,0) }}</a>{% endfor %}</div></section><section class="card"><h2>Neue Dokumentationsart</h2><form autocomplete="off" class="row" method="post"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="action" value="create_type"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" name="name" placeholder="z. B. Datendose" required><button class="btn" type="submit">Vorlage anlegen</button></form><p class="note">Neue Vorlagen starten ohne Felder. So kannst du sie passend zur Dokumentationsart zusammenstellen.</p></section>{% if selected %}<section class="card"><h2>Felder: {{ selected.name }}</h2>{% if fields %}{% for f in fields %}<form autocomplete="off" class="field" method="post"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="action" value="update_field"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="type_id" value="{{ selected.id }}"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="field_id" value="{{ f.id }}"><label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" name="enabled" type="checkbox" value="1" {% if f.enabled %}checked{% endif %}> Anzeigen</label><input name="label" type="text" value="{{ f.label }}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" required><label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" name="required" type="checkbox" value="1" {% if f.required %}checked{% endif %}> Pflicht</label><label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" name="remember_values" type="checkbox" value="1" {% if f.remember_values %}checked{% endif %}> Dropdown</label><button class="btn save-field" type="submit">Speichern</button>{% if not loop.first %}<button class="btn secondary move-button" type="submit" name="move" value="up" aria-label="Feld nach oben">↑</button>{% else %}<button class="btn secondary arrow-placeholder" type="button" disabled aria-hidden="true"></button>{% endif %}{% if not loop.last %}<button class="btn secondary move-button" type="submit" name="move" value="down" aria-label="Feld nach unten">↓</button>{% else %}<button class="btn secondary arrow-placeholder" type="button" disabled aria-hidden="true"></button>{% endif %}<button class="btn danger delete-field" type="submit" name="delete_field" value="1" formnovalidate onclick='return confirm("Dieses Feld und alle gespeicherten Werte aus den zugehörigen Einträgen löschen?")'>Feld löschen</button></form>{% endfor %}{% else %}<p class="note">Noch keine Felder angelegt.</p>{% endif %}<h3>Feld hinzufügen</h3><form autocomplete="off" class="row" method="post"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="action" value="add_field"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="type_id" value="{{ selected.id }}"><input name="label" placeholder="Feldname, z. B. VLAN" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" required><label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="checkbox" name="required" value="1"> Pflichtfeld</label><label><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="checkbox" name="remember_values" value="1"> Dropdown (mit bisherigen Werten)</label><button class="btn" type="submit">Hinzufügen</button></form>{% if selected.name != 'Access Point' %}<div class="delete-template"><p class="note">Das Löschen entfernt auch alle Einträge dieser Vorlage.</p><form autocomplete="off" method="post" onsubmit="return confirm('Diese Dokumentationsvorlage und alle zugehörigen Einträge unwiderruflich löschen?')"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="action" value="delete_type"><input autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" type="hidden" name="type_id" value="{{ selected.id }}"><button class="btn danger" type="submit">Vorlage und Einträge löschen</button></form></div>{% endif %}</section>{% endif %}</main></body></html>''', types=types, selected=selected, fields=fields, type_counts=type_counts)

@app.get("/")
def index():
    q = request.args.get("q", "").strip()
    conn = db()
    sites = conn.execute("SELECT * FROM sites ORDER BY street, house_number").fetchall()
    known_streets = sorted({row["street"] for row in sites if row["street"]})
    known_site_addresses = [{"street":row["street"],"house_number":row["house_number"]} for row in sites]
    site_counts = {row["site_id"]: row["total"] for row in conn.execute("SELECT site_id, COUNT(*) AS total FROM access_points WHERE site_id IS NOT NULL GROUP BY site_id")}
    types = get_types(conn)
    type_id = session.get("documentation_type_id")
    current_type = conn.execute("SELECT * FROM documentation_types WHERE id=?", (type_id,)).fetchone() if type_id else None
    if not current_type:
        current_type = conn.execute("SELECT * FROM documentation_types WHERE name='Access Point'").fetchone()
        session["documentation_type_id"] = current_type["id"]
    type_id = current_type["id"]
    fields, required_keys, custom_keys = get_fields(conn, type_id)
    remember_keys = get_remember_fields(conn, type_id)
    suggestions = get_field_suggestions(conn, type_id, remember_keys)
    followup_types = []
    for candidate_type in types:
        candidate_fields, _, _ = get_fields(conn, candidate_type["id"])
        candidate_roles = {
            followup_field_role(key, label)
            for key, label in candidate_fields
            if followup_field_role(key, label)
        }
        if {"v_number", "rq_number"}.issubset(candidate_roles):
            followup_types.append(candidate_type)
    followup_context = session.get("followup_context", {})
    followup_field_roles = {key: followup_field_role(key, label) for key, label in fields}
    followup_defaults = {
        key: followup_context[role]
        for key, role in followup_field_roles.items()
        if role and followup_context.get(role)
    }
    site_id = session.get("current_site_id")
    current_site = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    if site_id and not current_site:
        session.pop("current_site_id", None)
    site_documentation_counts = get_site_documentation_counts(conn, current_site["id"]) if current_site else []
    field_defaults = get_site_field_defaults(conn, current_site["id"] if current_site else None, type_id, remember_keys, current_type, current_site)
    field_defaults.update(followup_defaults)
    followup_fields_present = {"v_number", "rq_number"}.issubset(
        {role for role in followup_field_roles.values() if role}
    )
    for field_key, default_value in field_defaults.items():
        if default_value and field_key in suggestions:
            suggestions[field_key] = sorted(set(suggestions[field_key] + [default_value]), key=str.casefold)[:100]
    preferred_model = current_site["default_ap_model"] if current_site and current_type["name"] == "Access Point" else ""
    total_devices = conn.execute("SELECT COUNT(*) FROM access_points WHERE site_id=? AND doc_type_id=?", (current_site["id"], type_id)).fetchone()[0] if current_site else 0
    models = [row["model"] for row in conn.execute("SELECT ap_model AS model FROM access_points WHERE TRIM(ap_model)<>'' UNION SELECT default_ap_model AS model FROM sites WHERE TRIM(default_ap_model)<>'' ORDER BY model COLLATE NOCASE")]
    ap_model_custom = not models
    rows = []
    if current_site:
        params = [current_site["id"], type_id]
        where = "site_id=? AND doc_type_id=?"
        if q:
            like = "%" + q + "%"
            searchable = [key for key, _ in fields if key in {base for base, _ in FIELDS}]
            clauses = [key + " LIKE ?" for key in searchable]
            if custom_keys:
                clauses.append("custom_data LIKE ?")
            if clauses:
                where += " AND (" + " OR ".join(clauses) + ")"
                params.extend([like] * len(clauses))
        rows = [flatten_row(row) for row in conn.execute("SELECT * FROM access_points WHERE " + where + " ORDER BY id DESC", params).fetchall()]
    conn.close()
    return render_template_string(PAGE, rows=rows, q=q, fields=fields, required_keys=required_keys, custom_keys=custom_keys, item=None, sites=sites, current_site=current_site, site_documentation_counts=site_documentation_counts, site_counts=site_counts, models=models, ap_model_custom=ap_model_custom, preferred_model=preferred_model, total_devices=total_devices, types=types, followup_types=followup_types, current_type=current_type, remember_keys=remember_keys, suggestions=suggestions, field_defaults=field_defaults, followup_context=followup_context, followup_fields_present=followup_fields_present, known_streets=known_streets, known_site_addresses=known_site_addresses)

@app.post("/documentation-type/select")
def select_documentation_type():
    type_id = request.form.get("type_id", "").strip()
    conn = db()
    selected = conn.execute("SELECT id FROM documentation_types WHERE id=?", (type_id,)).fetchone() if type_id.isdigit() else None
    conn.close()
    if selected:
        session["documentation_type_id"] = selected["id"]
    return redirect(url_for("index"))

@app.post("/sites/select")
def select_site():
    site_id = request.form.get("site_id", "").strip()
    conn = db()
    site = conn.execute("SELECT id FROM sites WHERE id=?", (site_id,)).fetchone() if site_id.isdigit() else None
    conn.close()
    if site:
        if session.get("current_site_id") != site["id"]:
            session.pop("followup_context", None)
        session["current_site_id"] = site["id"]
    else:
        session.pop("current_site_id", None)
        session.pop("followup_context", None)
    return redirect(url_for("index"))

@app.post("/sites/create")
def create_site():
    street = request.form.get("street", "").strip()
    house_number = request.form.get("house_number", "").strip()
    inventory_number = request.form.get("inventory_number", "").strip()
    if not street or not house_number:
        from flask import flash
        flash("Bitte Straße und Hausnummer eintragen.")
        return redirect(url_for("index"))
    conn = db()
    cursor = conn.execute("INSERT INTO sites (street, house_number, inventory_number) VALUES (?, ?, ?)", (street, house_number, inventory_number))
    conn.commit()
    session.pop("followup_context", None)
    session["current_site_id"] = cursor.lastrowid
    conn.close()
    from flask import flash
    flash("Standort angelegt und ausgewählt.")
    return redirect(url_for("index"))

@app.post("/sites/delete/<int:site_id>")
def delete_site(site_id):
    conn = db()
    site = conn.execute("SELECT id FROM sites WHERE id=?", (site_id,)).fetchone()
    if not site:
        conn.close()
        return redirect(url_for("index"))
    conn.execute("DELETE FROM access_points WHERE site_id=?", (site_id,))
    conn.execute("DELETE FROM site_field_defaults WHERE site_id=?", (site_id,))
    conn.execute("DELETE FROM sites WHERE id=?", (site_id,))
    conn.commit()
    conn.close()
    if session.get("current_site_id") == site_id:
        session.pop("current_site_id", None)
        session.pop("followup_context", None)
    from flask import flash
    flash("Standort und zugeordnete Geräte gelöscht.")
    return redirect(url_for("index"))

@app.post("/sites/update")
def update_site():
    site_id = session.get("current_site_id")
    if not site_id:
        return redirect(url_for("index"))
    street = request.form.get("street", "").strip()
    house_number = request.form.get("house_number", "").strip()
    inventory_number = request.form.get("inventory_number", "").strip()
    if not street or not house_number:
        from flask import flash
        flash("Bitte Straße und Hausnummer eintragen.")
        return redirect(url_for("index"))
    conn = db()
    conn.execute("UPDATE sites SET street=?, house_number=?, inventory_number=? WHERE id=?", (street, house_number, inventory_number, site_id))
    conn.commit()
    conn.close()
    from flask import flash
    flash("Standortadresse aktualisiert.")
    return redirect(url_for("index"))

@app.post("/save")
@app.post("/save/<int:record_id>")
def save(record_id=None):
    site_id = session.get("current_site_id")
    type_id = session.get("documentation_type_id")
    if not site_id or not type_id:
        from flask import flash
        flash("Bitte zuerst Standort und Dokumentationsart auswählen.")
        return redirect(url_for("index"))
    conn = db()
    doc_type = conn.execute("SELECT * FROM documentation_types WHERE id=?", (type_id,)).fetchone()
    if not doc_type:
        conn.close()
        return redirect(url_for("index"))
    fields, required_keys, custom_keys = get_fields(conn, type_id)
    remember_keys = get_remember_fields(conn, type_id)
    if not fields:
        conn.close()
        from flask import flash
        flash("Diese Vorlage hat noch keine Felder. Bitte zuerst Felder hinzufügen.")
        return redirect(url_for("manage_templates", type_id=type_id))
    values = {}
    for key, label in fields:
        if key in remember_keys:
            choice = request.form.get(key + "_choice", "").strip()
            value = request.form.get(key + "_new", "").strip() if choice == "__new__" else choice
        elif key == "ap_model":
            value = request.form.get("ap_model", "").strip()
        elif key == "mac_address":
            value = request.form.get("hardware_mac", "").strip()
        elif key == "switch_name":
            value = request.form.get("network_switch_device", "").strip()
        else:
            value = request.form.get(key, "").strip()
        values[key] = value
    continue_followup = not record_id and request.form.get("continue_followup") == "1"
    followup_type = None
    new_followup_context = dict(session.get("followup_context", {}))
    if continue_followup:
        followup_type_id = request.form.get("followup_type_id", "").strip()
        followup_type = conn.execute(
            "SELECT id, name FROM documentation_types WHERE id=?",
            (followup_type_id,),
        ).fetchone() if followup_type_id.isdigit() else None
        if not followup_type:
            conn.close()
            from flask import flash
            flash("Bitte wähle die Dokumentationsart des Folgegeräts aus.")
            return redirect(url_for("index"))
        followup_fields, _, _ = get_fields(conn, followup_type["id"])
        followup_roles = {
            followup_field_role(key, label)
            for key, label in followup_fields
            if followup_field_role(key, label)
        }
        if not {"v_number", "rq_number"}.issubset(followup_roles):
            conn.close()
            from flask import flash
            flash("Die Folgegeräte-Vorlage benötigt Felder für V-Nummer und RQ-Nummer.")
            return redirect(url_for("index"))
        for key, label in fields:
            role = followup_field_role(key, label)
            if role and values.get(key):
                new_followup_context[role] = values[key]
        missing_followup = [
            label for role, label in (("v_number", "V-Nummer"), ("rq_number", "RQ-Nummer"))
            if not new_followup_context.get(role)
        ]
        if missing_followup:
            conn.close()
            from flask import flash
            flash("Für das Folgegerät fehlen: " + ", ".join(missing_followup) + ".")
            return redirect(url_for("index"))
    missing = [label for key, label in fields if key in required_keys and not values.get(key)]
    if missing:
        conn.close()
        from flask import flash
        flash("Bitte Pflichtfelder ausfüllen: " + ", ".join(missing))
        return redirect(url_for("index"))
    existing = conn.execute("SELECT custom_data FROM access_points WHERE id=? AND site_id=? AND doc_type_id=?", (record_id,site_id,type_id)).fetchone() if record_id else None
    custom_data = {}
    if existing:
        try:
            custom_data = json.loads(existing["custom_data"] or "{}")
        except (TypeError, ValueError):
            custom_data = {}
    base_keys = {key for key, _ in FIELDS}
    updates = {}
    for key, value in values.items():
        if key in base_keys:
            updates[key] = value
        elif key in custom_keys:
            custom_data[key] = value
    if doc_type["name"] == "Access Point" and "ap_model" in values and values["ap_model"]:
        conn.execute("UPDATE sites SET default_ap_model=? WHERE id=?", (values["ap_model"], site_id))
    for field_key in remember_keys:
        selected_value = values.get(field_key, "").strip()
        if selected_value:
            conn.execute("INSERT INTO site_field_defaults (site_id,type_id,field_key,value) VALUES (?,?,?,?) ON CONFLICT(site_id,type_id,field_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (site_id,type_id,field_key,selected_value))
    if record_id:
        if not existing:
            conn.close()
            abort(404)
        assignments = [key + "=?" for key in updates]
        params = list(updates.values())
        assignments.append("custom_data=?")
        params.extend([json.dumps(custom_data, ensure_ascii=False),record_id,site_id,type_id])
        conn.execute("UPDATE access_points SET " + ", ".join(assignments) + " WHERE id=? AND site_id=? AND doc_type_id=?", params)
        message = "Eintrag aktualisiert."
    else:
        insert_values = {key: "" for key, _ in FIELDS}
        insert_values.update(updates)
        keys = list(insert_values) + ["site_id","doc_type_id","custom_data"]
        params = list(insert_values.values()) + [site_id,type_id,json.dumps(custom_data, ensure_ascii=False)]
        conn.execute("INSERT INTO access_points (" + ", ".join(keys) + ") VALUES (" + ", ".join("?" for _ in keys) + ")", params)
        message = "Eintrag gespeichert."
    conn.commit()
    conn.close()
    from flask import flash
    if not record_id:
        if continue_followup:
            session["followup_context"] = new_followup_context
            session["documentation_type_id"] = followup_type["id"]
            message = "Eintrag gespeichert. Dokumentiere jetzt das Folgegerät: " + followup_type["name"] + "."
        else:
            session.pop("followup_context", None)
    flash(message)
    return redirect(url_for("index"))

@app.get("/edit/<int:record_id>")
def edit(record_id):
    site_id = session.get("current_site_id")
    if not site_id:
        abort(404)
    conn = db()
    raw_item = conn.execute("SELECT * FROM access_points WHERE id=? AND site_id=?", (record_id,site_id)).fetchone()
    if not raw_item:
        conn.close()
        abort(404)
    session["documentation_type_id"] = raw_item["doc_type_id"]
    type_id = raw_item["doc_type_id"]
    current_type = conn.execute("SELECT * FROM documentation_types WHERE id=?", (type_id,)).fetchone()
    fields, required_keys, custom_keys = get_fields(conn, type_id)
    remember_keys = get_remember_fields(conn, type_id)
    suggestions = get_field_suggestions(conn, type_id, remember_keys)
    item = flatten_row(raw_item)
    sites = conn.execute("SELECT * FROM sites ORDER BY street, house_number").fetchall()
    known_streets = sorted({row["street"] for row in sites if row["street"]})
    known_site_addresses = [{"street":row["street"],"house_number":row["house_number"]} for row in sites]
    types = get_types(conn)
    site_counts = {row["site_id"]: row["total"] for row in conn.execute("SELECT site_id, COUNT(*) AS total FROM access_points WHERE site_id IS NOT NULL GROUP BY site_id")}
    models = [row["model"] for row in conn.execute("SELECT ap_model AS model FROM access_points WHERE TRIM(ap_model)<>'' UNION SELECT default_ap_model AS model FROM sites WHERE TRIM(default_ap_model)<>'' ORDER BY model COLLATE NOCASE")]
    ap_model_custom = bool(item.get("ap_model")) and item["ap_model"] not in models
    current_site = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    site_documentation_counts = get_site_documentation_counts(conn, site_id)
    field_defaults = get_site_field_defaults(conn, site_id, type_id, remember_keys, current_type, current_site)
    for field_key, default_value in field_defaults.items():
        if default_value and field_key in suggestions:
            suggestions[field_key] = sorted(set(suggestions[field_key] + [default_value]), key=str.casefold)[:100]
    preferred_model = current_site["default_ap_model"] if current_site and current_type["name"]=="Access Point" else ""
    total_devices = conn.execute("SELECT COUNT(*) FROM access_points WHERE site_id=? AND doc_type_id=?", (site_id,type_id)).fetchone()[0]
    rows = [flatten_row(row) for row in conn.execute("SELECT * FROM access_points WHERE site_id=? AND doc_type_id=? ORDER BY id DESC", (site_id,type_id)).fetchall()]
    conn.close()
    return render_template_string(PAGE, rows=rows, q="", fields=fields, required_keys=required_keys, custom_keys=custom_keys, item=item, sites=sites, current_site=current_site, site_documentation_counts=site_documentation_counts, site_counts=site_counts, models=models, ap_model_custom=ap_model_custom, preferred_model=preferred_model, total_devices=total_devices, types=types, current_type=current_type, remember_keys=remember_keys, suggestions=suggestions, field_defaults=field_defaults, known_streets=known_streets, known_site_addresses=known_site_addresses)

@app.post("/delete/<int:record_id>")
def delete(record_id):
    site_id = session.get("current_site_id")
    if site_id:
        conn = db()
        conn.execute("DELETE FROM access_points WHERE id=? AND site_id=?", (record_id, site_id))
        conn.commit()
        conn.close()
    from flask import flash
    flash("Eintrag gelöscht.")
    return redirect(url_for("index"))

def get_site_export_data(conn, site_id):
    site = conn.execute("SELECT street,house_number,inventory_number FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    types = get_types(conn)
    records = []
    if site_id:
        for raw in conn.execute("SELECT * FROM access_points WHERE site_id=? ORDER BY id DESC", (site_id,)).fetchall():
            row = flatten_row(raw)
            row["documentation_type"] = next((t["name"] for t in types if t["id"] == row.get("doc_type_id")), "Unbekannt")
            records.append(row)
    documented_type_ids = {row.get("doc_type_id") for row in records}
    types = [doc_type for doc_type in types if doc_type["id"] in documented_type_ids]
    return site, types, records

@app.get("/export.xlsx")
def export_xlsx():
    site_id = session.get("current_site_id")
    conn = db()
    site, types, records = get_site_export_data(conn, site_id)
    fields_by_type = {doc_type["id"]: get_fields(conn, doc_type["id"])[0] for doc_type in types}
    conn.close()
    site_address = f"{site['street']} {site['house_number']}".strip() if site else ""
    inventory_number = site["inventory_number"] if site else ""
    max_columns = max([1 + len(fields) for fields in fields_by_type.values()] + [1])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Gesamtexport"
    sheet.sheet_view.showGridLines = False
    current_row = 1
    for doc_type in types:
        fields = fields_by_type[doc_type["id"]]
        section_label = "Dokumentationsart: " + doc_type["name"]
        for column_index in range(1,max_columns+1):
            cell = sheet.cell(row=current_row,column=column_index,value=section_label if column_index==1 else "")
            cell.fill = PatternFill("solid",fgColor="087F8C")
            cell.font = Font(bold=True,color="FFFFFF")
        current_row += 1
        headers = ["Standort"] + [label for _,label in fields]
        for column_index in range(1,max_columns+1):
            value = headers[column_index-1] if column_index <= len(headers) else ""
            cell = sheet.cell(row=current_row,column=column_index,value=value)
            cell.font = Font(bold=True,color="FFFFFF")
            cell.fill = PatternFill("solid",fgColor="102B46")
        current_row += 1
        for row in records:
            if row.get("doc_type_id") != doc_type["id"]:
                continue
            values = [site_address] + [row.get(key,"") for key,_ in fields]
            for column_index, value in enumerate(values,1):
                cell = sheet.cell(row=current_row,column=column_index,value=value)
                cell.data_type = "s"
                cell.number_format = "@"
            current_row += 1
        current_row += 1
    for column in sheet.columns:
        letter = column[0].column_letter
        width = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[letter].width = min(max(width + 2,14),42)
    site_filename = secure_filename(" ".join(part for part in [site["street"],site["house_number"],site["inventory_number"],"Gesamtdokumentation"] if part).strip()) if site else "netzwerk-dokumentation"
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return Response(output.getvalue(),mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{site_filename}.xlsx"'})

def csv_excel_text(value):
    text = "" if value is None else str(value)
    if not text:
        return ""
    # A quoted Excel string literal preserves identifiers/dates as text and
    # keeps user-entered values as text instead of evaluating their contents.
    return '="' + text.replace('"', '""') + '"'


@app.get("/export.csv")
def export_csv():
    site_id = session.get("current_site_id")
    conn = db()
    site, types, records = get_site_export_data(conn, site_id)
    fields_by_type = {doc_type["id"]: get_fields(conn, doc_type["id"])[0] for doc_type in types}
    conn.close()
    site_address = f"{site['street']} {site['house_number']}".strip() if site else ""
    inventory_number = site["inventory_number"] if site else ""
    output = io.StringIO(newline="")
    writer = csv.writer(output,delimiter=";")
    for doc_type in types:
        fields = fields_by_type[doc_type["id"]]
        writer.writerow([csv_excel_text("Dokumentationsart: " + doc_type["name"])])
        writer.writerow([csv_excel_text("Standort")] + [csv_excel_text(label) for _,label in fields])
        for row in records:
            if row.get("doc_type_id") == doc_type["id"]:
                writer.writerow([csv_excel_text(site_address)] + [csv_excel_text(row.get(key,"")) for key,_ in fields])
        writer.writerow([])
    site_filename = secure_filename(" ".join(part for part in [site["street"],site["house_number"],site["inventory_number"],"Gesamtdokumentation"] if part).strip()) if site else "netzwerk-dokumentation"
    return Response("\ufeff"+output.getvalue(),mimetype="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{site_filename}.csv"'})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8086)
