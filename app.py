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
    conn.execute("CREATE TABLE IF NOT EXISTS documentation_fields (id INTEGER PRIMARY KEY AUTOINCREMENT, type_id INTEGER NOT NULL REFERENCES documentation_types(id) ON DELETE CASCADE, field_key TEXT NOT NULL, label TEXT NOT NULL, required INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0, is_custom INTEGER NOT NULL DEFAULT 0, UNIQUE(type_id, field_key))")
    conn.execute("INSERT OR IGNORE INTO documentation_types (name) VALUES ('Access Point')")
    ap_type_id = conn.execute("SELECT id FROM documentation_types WHERE name='Access Point'").fetchone()[0]
    for position, (field_key, label) in enumerate(FIELDS):
        conn.execute("INSERT OR IGNORE INTO documentation_fields (type_id,field_key,label,required,enabled,position,is_custom) VALUES (?,?,?,?,?,?,0)", (ap_type_id,field_key,label,1 if field_key=='ap_model' else 0,1,position))
    conn.execute("UPDATE access_points SET doc_type_id=? WHERE doc_type_id IS NULL", (ap_type_id,))
    conn.commit()
    return conn


def get_types(conn):
    return conn.execute("SELECT * FROM documentation_types ORDER BY id").fetchall()

def get_fields(conn, type_id):
    rows = conn.execute("SELECT field_key,label,required,is_custom FROM documentation_fields WHERE type_id=? AND enabled=1 ORDER BY position,id", (type_id,)).fetchall()
    return [(row["field_key"], row["label"]) for row in rows], {row["field_key"] for row in rows if row["required"]}, {row["field_key"] for row in rows if row["is_custom"]}

def flatten_row(row):
    data = dict(row)
    try:
        data.update(json.loads(data.get("custom_data") or "{}"))
    except (TypeError, ValueError):
        pass
    return data

PAGE = r'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#102b46"><meta name="format-detection" content="telephone=no"><title>Netzwerk-Dokumentation</title>
<style>
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172b3a;background:#f2f6f9;font-synthesis:none}*{box-sizing:border-box}body{margin:0}.top{background:#102b46;color:white;padding:24px max(20px,calc((100% - 1100px)/2));}.top small{color:#a9c8df;letter-spacing:.12em;text-transform:uppercase;font-weight:700}.top h1{margin:6px 0 0;font-size:clamp(1.4rem,4vw,2rem)}main{max-width:1100px;margin:26px auto;padding:0 18px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}.button{border:0;border-radius:10px;padding:12px 16px;background:#087f8c;color:white;font-weight:700;text-decoration:none;cursor:pointer;font-size:.95rem}.button.secondary{background:white;color:#254357;border:1px solid #d6e1e8}.button.danger{background:#fff0ed;color:#a63f2d}.card{background:#fff;border:1px solid #e0e8ee;border-radius:16px;padding:20px;box-shadow:0 5px 18px #16324a0a;margin-bottom:18px}.card h2{margin:0 0 18px;font-size:1.1rem}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field label{display:block;font-size:.83rem;font-weight:700;color:#405a6b;margin:0 0 6px}.field input,.field select{width:100%;height:46px;padding:10px 12px;border:1px solid #cad8e1;border-radius:9px;font:inherit;background:#fff}.field input[hidden]{display:none}.field input:focus,.field select:focus,.search:focus{outline:3px solid #087f8c33;border-color:#087f8c}.form-foot{display:flex;gap:10px;margin-top:18px}.searchrow{display:flex;gap:10px;margin-bottom:14px}.search{flex:1;min-width:0;height:44px;border:1px solid #d3dfe7;border-radius:9px;padding:10px 12px;font:inherit}.count{color:#617988;font-size:.9rem;margin:0 0 12px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:.9rem}th{text-align:left;color:#5b7180;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;padding:11px 10px;border-bottom:1px solid #e1e9ee}td{padding:13px 10px;border-bottom:1px solid #edf1f4;vertical-align:top}tbody tr:last-child td{border:0}.actions-cell{white-space:nowrap}.link{color:#087f8c;font-weight:700;text-decoration:none}.empty{text-align:center;padding:30px;color:#647987}.flash{padding:12px;border-radius:9px;background:#e7f5f2;color:#12695d;margin-bottom:15px}.mobile-list{display:none}.mobile-item{background:#f7f9fb;border:1px solid #e1e9ee;border-radius:12px;padding:13px}.device-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.device-title{min-width:0}.device-model{font-weight:750;font-size:1rem;line-height:1.25;overflow-wrap:anywhere}.device-room{font-size:.84rem;color:#617988;margin-top:4px;overflow-wrap:anywhere}.device-edit{flex:none;border-radius:8px;background:#e5f4f3;color:#087f8c;padding:8px 10px;text-decoration:none;font-size:.85rem;font-weight:700}.device-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:12px}.device-field{min-width:0;background:white;border:1px solid #e7edf1;border-radius:8px;padding:8px 9px}.device-label{display:block;color:#718491;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}.device-value{display:block;color:#253f50;font-size:.88rem;line-height:1.3;overflow-wrap:anywhere}.device-delete{margin:10px 0 0 auto;display:block;border:0;background:none;color:#a45143;font-size:.82rem;cursor:pointer;padding:5px}.site-admin-list{display:flex;flex-direction:column;gap:6px}.site-admin-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid #e7edf1;font-size:.9rem}.site-admin-row form{margin:0}.site-admin-row .note{margin:0}.site-admin-controls{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;align-items:center}.site-admin-controls .search{height:38px}.site-admin-row .device-delete{margin:0}.note{color:#708391;font-size:.85rem;margin-top:13px}.site-tools{margin-top:12px;border-top:1px solid #e7edf1;padding-top:10px}.site-tools summary{color:#087f8c;font-weight:700;cursor:pointer;padding:5px 0}.site-tools h3{font-size:.95rem;margin:16px 0 10px}.site-tools-content{padding-top:3px}
@media(max-width:700px){main{margin:17px auto;padding:0 13px}.top{padding:20px 16px}.card{padding:16px;border-radius:13px}.grid{grid-template-columns:1fr;gap:12px}.desktop{display:none}.mobile-list{display:flex;flex-direction:column;gap:10px}.searchrow .button{padding:11px 12px}.form-foot .button{flex:1}.actions .button{flex:1;text-align:center}.searchrow{gap:7px}}
</style></head><body><header class="top"><small>IT Inventar</small><h1>Netzwerk-Dokumentation</h1>{% if current_site %}<div style="margin-top:6px;color:#d1e2ef">{{ current_site.street }} {{ current_site.house_number }}</div>{% if current_site.inventory_number %}<div style="margin-top:3px;color:#a9c8df;font-size:.85rem">IM-/VITKO-Nummer · {{ current_site.inventory_number }}</div>{% endif %}{% else %}<div style="margin-top:6px;color:#d1e2ef">Bitte zuerst einen Standort auswählen</div>{% endif %}</header><main>
{% with messages = get_flashed_messages() %}{% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}{% endwith %}
<div class="actions">{% if current_site %}<a class="button" href="#entry">＋ Gerät eintragen</a>{% else %}<a class="button" href="#new-site" onclick="document.getElementById('site-tools').open=true">＋ Standort anlegen</a>{% endif %}{% if current_site %}<button class="button secondary export-choice" type="button" data-url="{{ url_for('export_csv') }}" data-type="csv">↓ CSV</button><button class="button secondary export-choice" type="button" data-url="{{ url_for('export_xlsx') }}" data-type="xlsx">↓ Excel (.xlsx)</button>{% endif %}</div>
<section class="card site-card" id="sites"><h2>Standort</h2>{% if sites %}<form method="post" action="{{ url_for('select_site') }}"><div class="searchrow"><select class="search" name="site_id"><option value="">Standort auswählen …</option>{% for s in sites %}<option value="{{ s.id }}" {% if current_site and s.id==current_site.id %}selected{% endif %}>{{ s.street }} {{ s.house_number }}{% if s.inventory_number %} · {{ s.inventory_number }}{% endif %}</option>{% endfor %}</select><button class="button" type="submit">Öffnen</button></div></form>{% else %}<p class="note">Lege zuerst deinen aktuellen Standort an.</p>{% endif %}
<details class="site-tools" id="site-tools"><summary>Standorte verwalten</summary><div class="site-tools-content">
{% if current_site %}<h3>Aktuellen Standort bearbeiten</h3><form method="post" action="{{ url_for('update_site') }}"><div class="grid"><div class="field"><label for="edit_street">Straße</label><input id="edit_street" name="street" value="{{ current_site.street }}" required></div><div class="field"><label for="edit_house_number">Hausnummer</label><input id="edit_house_number" name="house_number" value="{{ current_site.house_number }}" required></div><div class="field"><label for="edit_inventory_number">IM-/VITKO-Nummer (optional)</label><input id="edit_inventory_number" name="inventory_number" value="{{ current_site.inventory_number }}" placeholder="z. B. IM 123456"></div></div><div class="form-foot"><button class="button secondary" type="submit">Adresse aktualisieren</button></div></form>{% endif %}
<h3 id="new-site">Neuen Standort anlegen</h3><form method="post" action="{{ url_for('create_site') }}"><div class="grid"><div class="field"><label for="new_street">Straße</label><input id="new_street" name="street" placeholder="z. B. Hauptstraße" required></div><div class="field"><label for="new_house_number">Hausnummer</label><input id="new_house_number" name="house_number" placeholder="z. B. 12a" required></div><div class="field"><label for="new_inventory_number">IM-/VITKO-Nummer (optional)</label><input id="new_inventory_number" name="inventory_number" placeholder="z. B. IM 123456"></div></div><div class="form-foot"><button class="button" type="submit">Anlegen und öffnen</button></div></form>
</div>
{% if sites %}<h3>Standorte löschen</h3><div class="site-admin-list">{% for s in sites %}{% set assigned = site_counts.get(s.id, 0) %}<div class="site-admin-row"><span>{{ s.street }} {{ s.house_number }} · {{ assigned }} {{ 'Gerät' if assigned==1 else 'Geräte' }}</span><form method="post" action="{{ url_for('delete_site', site_id=s.id) }}" onsubmit="return confirm('Standort und alle zugeordneten Geräte löschen? Dieser Vorgang kann nicht rückgängig gemacht werden.')"><button class="device-delete" type="submit">Standort &amp; Geräte löschen</button></form></div>{% endfor %}</div>{% endif %}


</details></section>
<section class="card"><h2>Dokumentationsart</h2><form method="post" action="{{ url_for('select_documentation_type') }}"><div class="searchrow"><select class="search" name="type_id">{% for type in types %}<option value="{{ type.id }}" {% if type.id==current_type.id %}selected{% endif %}>{{ type.name }}</option>{% endfor %}</select><button class="button" type="submit">Auswählen</button><a class="button secondary" href="{{ url_for('manage_templates') }}">Vorlagen verwalten</a></div></form></section>
{% if current_site %}<section class="card" id="entry"><h2>{{ 'Eintrag bearbeiten' if item else current_type.name ~ ' dokumentieren' }}</h2><form method="post" autocomplete="off" action="{{ url_for('save', record_id=item.id) if item else url_for('save') }}"><div class="grid">
{% for key,label in fields %}{% if key=='ap_model' %}<div class="field"><label for="ap_model_choice">{{ label }}</label><select id="ap_model_choice" name="ap_model_choice" onchange="toggleApModel()" {% if key in required_keys %}required{% endif %}><option value="" disabled {% if not item and not preferred_model and not ap_model_custom %}selected{% endif %}>AP-Modell auswählen …</option>{% for model in models %}<option value="{{ model }}" {% if (item and item.ap_model==model) or (not item and preferred_model==model) %}selected{% endif %}>{{ model }}</option>{% endfor %}<option value="__new__" {% if ap_model_custom %}selected{% endif %}>Neues AP-Modell eingeben …</option></select><input id="ap_model_new" name="ap_model_new" value="{{ item.ap_model if item and ap_model_custom else '' }}" placeholder="z. B. UniFi U6 Pro" {% if not ap_model_custom %}hidden{% endif %} {% if ap_model_custom and key in required_keys %}required{% endif %}></div>{% elif key=='mac_address' %}<div class="field"><label for="hardware_mac">MAC</label><input id="hardware_mac" name="hardware_mac" value="{{ item[key] if item else '' }}" placeholder="z. B. AA:BB:CC:DD:EE:FF" autocomplete="off" autocorrect="off" autocapitalize="characters" spellcheck="false" inputmode="text"></div>{% elif key=='switch_name' %}<div class="field"><label for="network_switch_device">Switch</label><input id="network_switch_device" name="network_switch_device" value="{{ item[key] if item else '' }}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></div>{% else %}<div class="field"><label for="{{ key }}">{{ label }}</label><input id="{{ key }}" name="{{ key }}" value="{{ item[key] if item else '' }}" {% if key=='room' %}placeholder="z. B. 2. OG · Büro 2.14"{% endif %} {% if key in required_keys %}required{% endif %}></div>{% endif %}{% endfor %}
</div><div class="form-foot"><button class="button" type="submit">{{ 'Änderungen speichern' if item else 'Eintrag speichern' }}</button>{% if item %}<a class="button secondary" href="{{ url_for('index') }}">Abbrechen</a>{% endif %}</div></form></section>{% else %}<section class="card"><h2>Geräte dokumentieren</h2><div class="empty">Wähle zuerst den Standort aus, an dem du gerade arbeitest.</div></section>{% endif %}
<section class="card"><h2>Dokumentierte Geräte</h2><p class="count">Gesamt: {{ total_devices }} {{ 'Gerät' if total_devices==1 else 'Geräte' }}</p>{% if current_site %}<p class="count">Standort: {{ current_site.street }} {{ current_site.house_number }}</p>{% endif %}<form class="searchrow" method="get"><input class="search" name="q" value="{{ q }}" placeholder="Suche: Modell, MAC, Raum, Switch …"><button class="button" type="submit">Suchen</button>{% if q %}<a class="button secondary" href="{{ url_for('index') }}">Zurücksetzen</a>{% endif %}</form>{% if q %}<p class="count">{{ rows|length }} Treffer für „{{ q }}“</p>{% endif %}
{% if rows %}<div class="tablewrap desktop"><table><thead><tr>{% for key,label in fields %}<th>{{ label }}</th>{% endfor %}<th></th></tr></thead><tbody>{% for row in rows %}<tr>{% for key,label in fields %}<td>{{ row[key] or '–' }}</td>{% endfor %}<td class="actions-cell"><a class="link" href="{{ url_for('edit', record_id=row.id) }}">Bearbeiten</a> · <form style="display:inline" method="post" action="{{ url_for('delete', record_id=row.id) }}" onsubmit="return confirm('Diesen Eintrag löschen?')"><button class="link" style="border:0;background:none;padding:0;cursor:pointer" type="submit">Löschen</button></form></td></tr>{% endfor %}</tbody></table></div>
<div class="mobile-list">{% for row in rows %}<article class="mobile-item"><div class="device-heading"><div class="device-title"><div class="device-model">{{ row.ap_model or 'Access Point' }}</div><div class="device-room">Raum · {{ row.room or 'Nicht angegeben' }}</div></div><a class="device-edit" href="{{ url_for('edit', record_id=row.id) }}">Bearbeiten</a></div><div class="device-grid"><div class="device-field"><span class="device-label">Seriennummer</span><span class="device-value">{{ row.serial_number or '–' }}</span></div><div class="device-field"><span class="device-label">MAC-Adresse</span><span class="device-value">{{ row.mac_address or '–' }}</span></div><div class="device-field"><span class="device-label">Datenverteiler</span><span class="device-value">{{ row.distribution or '–' }}</span></div><div class="device-field"><span class="device-label">Datendose / Port</span><span class="device-value">{{ row.outlet_port or '–' }}</span></div><div class="device-field"><span class="device-label">Switch</span><span class="device-value">{{ row.switch_name or '–' }}</span></div><div class="device-field"><span class="device-label">Switch Port</span><span class="device-value">{{ row.switch_port or '–' }}</span></div></div><form method="post" action="{{ url_for('delete', record_id=row.id) }}" onsubmit="return confirm('Diesen Eintrag löschen?')"><button class="device-delete" type="submit">Eintrag löschen</button></form></article>{% endfor %}</div>
{% else %}<div class="empty">{% if q %}Keine passenden Einträge gefunden.{% else %}Noch keine Geräte eingetragen. Lege den ersten Eintrag oben an.{% endif %}</div>{% endif %}<p class="note">Die Daten werden lokal in einer SQLite-Datei im Docker-Datenvolume gespeichert.</p></section></main><dialog id="export-dialog" style="border:0;border-radius:16px;padding:22px;max-width:calc(100% - 28px);width:420px;box-shadow:0 18px 60px #102b4633"><h2 style="margin:0 0 8px">Export teilen</h2><p id="export-status" class="note">Export wird vorbereitet …</p><div class="form-foot"><a id="export-download" class="button secondary" href="#" download>Herunterladen</a><button id="export-share" class="button" type="button" disabled>Per E-Mail / Teilen</button></div><div class="form-foot"><button id="export-close" class="button secondary" type="button">Schließen</button></div></dialog><script>const exportDialog=document.getElementById('export-dialog');let exportFile=null,exportObjectUrl=null;document.querySelectorAll('.export-choice').forEach(button=>button.addEventListener('click',async()=>{exportFile=null;if(exportObjectUrl)URL.revokeObjectURL(exportObjectUrl);exportObjectUrl=null;const status=document.getElementById('export-status'),download=document.getElementById('export-download'),share=document.getElementById('export-share');download.removeAttribute('href');share.disabled=true;status.textContent='Export wird vorbereitet …';exportDialog.showModal();try{const response=await fetch(button.dataset.url);if(!response.ok)throw new Error();const blob=await response.blob();const disposition=response.headers.get('Content-Disposition')||'';const match=disposition.match(/filename="?([^";]+)"?/i);const filename=match?match[1]:'netzwerk-dokumentation.'+button.dataset.type;exportFile=new File([blob],filename,{type:blob.type});exportObjectUrl=URL.createObjectURL(blob);download.href=exportObjectUrl;download.download=filename;share.disabled=false;status.textContent='Wähle Herunterladen oder öffne das Teilen-Menü und wähle Mail.'}catch(error){status.textContent='Der Export konnte nicht vorbereitet werden. Bitte Seite neu laden und erneut versuchen.'}}));document.getElementById('export-share').addEventListener('click',async()=>{const status=document.getElementById('export-status');if(!exportFile)return;if(!window.isSecureContext||!navigator.share||!navigator.canShare||!navigator.canShare({files:[exportFile]})){status.textContent='Teilen mit Mail wird von diesem Browser hier nicht unterstützt. Öffne die Seite über HTTPS oder lade die Datei herunter und teile sie anschließend über die Dateien-App.';return}try{await navigator.share({files:[exportFile],title:exportFile.name})}catch(error){if(error.name!=='AbortError')status.textContent='Teilen wurde nicht abgeschlossen. Du kannst die Datei herunterladen.'}});document.getElementById('export-close').addEventListener('click',()=>exportDialog.close());exportDialog.addEventListener('close',()=>{if(exportObjectUrl)URL.revokeObjectURL(exportObjectUrl);exportObjectUrl=null});function toggleApModel(){const select=document.getElementById('ap_model_choice');const input=document.getElementById('ap_model_new');const custom=select.value==='__new__';input.hidden=!custom;input.required=custom;}const entryForm=document.querySelector('#entry form');if(entryForm){entryForm.addEventListener('keydown',function(event){if(event.key!=='Enter'||!event.target.matches('input:not([type="hidden"]):not([hidden])'))return;event.preventDefault();const fields=Array.from(entryForm.querySelectorAll('select:not([disabled]),input:not([type="hidden"]):not([hidden]),textarea'));const current=fields.indexOf(event.target);if(current>=0&&fields[current+1])fields[current+1].focus();});}</script></body></html>'''

@app.get("/")
def index():
    q = request.args.get("q", "").strip()
    conn = db()
    sites = conn.execute("SELECT * FROM sites ORDER BY street, house_number").fetchall()
    site_counts = {row["site_id"]: row["total"] for row in conn.execute("SELECT site_id, COUNT(*) AS total FROM access_points WHERE site_id IS NOT NULL GROUP BY site_id")}
    types = get_types(conn)
    type_id = session.get("documentation_type_id")
    current_type = conn.execute("SELECT * FROM documentation_types WHERE id=?", (type_id,)).fetchone() if type_id else None
    if not current_type:
        current_type = conn.execute("SELECT * FROM documentation_types WHERE name='Access Point'").fetchone()
        session["documentation_type_id"] = current_type["id"]
    type_id = current_type["id"]
    fields, required_keys, custom_keys = get_fields(conn, type_id)
    site_id = session.get("current_site_id")
    current_site = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    if site_id and not current_site:
        session.pop("current_site_id", None)
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
    return render_template_string(PAGE, rows=rows, q=q, fields=fields, required_keys=required_keys, custom_keys=custom_keys, item=None, sites=sites, current_site=current_site, site_counts=site_counts, models=models, ap_model_custom=ap_model_custom, preferred_model=preferred_model, total_devices=total_devices, types=types, current_type=current_type)

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
        session["current_site_id"] = site["id"]
    else:
        session.pop("current_site_id", None)
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
    conn.execute("DELETE FROM sites WHERE id=?", (site_id,))
    conn.commit()
    conn.close()
    if session.get("current_site_id") == site_id:
        session.pop("current_site_id", None)
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
    if not site_id:
        from flask import flash
        flash("Bitte zuerst einen Standort auswählen.")
        return redirect(url_for("index"))
    values = []
    for key, _ in FIELDS:
        if key == "ap_model":
            choice = request.form.get("ap_model_choice", "").strip()
            value = request.form.get("ap_model_new", "").strip() if choice == "__new__" else choice
        elif key == "mac_address":
            value = request.form.get("hardware_mac", "").strip()
        elif key == "switch_name":
            value = request.form.get("network_switch_device", "").strip()
        else:
            value = request.form.get(key, "").strip()
        values.append(value)
    if not values[0]:
        from flask import flash
        flash("Bitte ein AP-Modell auswählen oder eingeben.")
        return redirect(url_for("index"))
    conn = db()
    conn.execute("UPDATE sites SET default_ap_model=? WHERE id=?", (values[0], site_id))
    if record_id:
        conn.execute("UPDATE access_points SET " + ", ".join(f"{key}=?" for key, _ in FIELDS) + " WHERE id=? AND site_id=?", values + [record_id, site_id])
        message = "Eintrag aktualisiert."
    else:
        conn.execute("INSERT INTO access_points (" + ", ".join(key for key, _ in FIELDS) + ", site_id) VALUES (" + ", ".join("?" for _ in FIELDS) + ", ?)", values + [site_id])
        message = "Gerät gespeichert."
    conn.commit()
    conn.close()
    from flask import flash
    flash(message)
    return redirect(url_for("index"))

@app.get("/edit/<int:record_id>")
def edit(record_id):
    site_id = session.get("current_site_id")
    if not site_id:
        abort(404)
    conn = db()
    item = conn.execute("SELECT * FROM access_points WHERE id=? AND site_id=?", (record_id, site_id)).fetchone()
    sites = conn.execute("SELECT * FROM sites ORDER BY street, house_number").fetchall()
    site_counts = {row["site_id"]: row["total"] for row in conn.execute("SELECT site_id, COUNT(*) AS total FROM access_points WHERE site_id IS NOT NULL GROUP BY site_id")}
    models = [row["model"] for row in conn.execute("SELECT ap_model AS model FROM access_points WHERE TRIM(ap_model)<>'' UNION SELECT default_ap_model AS model FROM sites WHERE TRIM(default_ap_model)<>'' ORDER BY model COLLATE NOCASE")]
    ap_model_custom = item is not None and item["ap_model"] not in models
    current_site = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    preferred_model = current_site["default_ap_model"] if current_site else ""
    total_devices = site_counts.get(site_id, 0)
    rows = conn.execute("SELECT * FROM access_points WHERE site_id=? ORDER BY id DESC", (site_id,)).fetchall()
    conn.close()
    if not item or not current_site:
        abort(404)
    return render_template_string(PAGE, rows=rows, q="", fields=FIELDS, item=item, sites=sites, current_site=current_site, site_counts=site_counts, models=models, ap_model_custom=ap_model_custom, preferred_model=preferred_model, total_devices=total_devices)

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

@app.get("/export.xlsx")
def export_xlsx():
    site_id = session.get("current_site_id")
    conn = db()
    site = conn.execute("SELECT street, house_number, inventory_number FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    if site_id:
        rows = conn.execute("SELECT " + ", ".join(key for key, _ in FIELDS) + " FROM access_points WHERE site_id=? ORDER BY id DESC", (site_id,)).fetchall()
    else:
        rows = []
    conn.close()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Netzwerk-Dokumentation"
    sheet.append(["Standort"] + [label for _, label in FIELDS])
    site_address = f"{site['street']} {site['house_number']}".strip() if site else ""
    site_filename = secure_filename(" ".join(part for part in [site["street"], site["house_number"], site["inventory_number"]] if part).strip()) if site else "netzwerk-dokumentation"
    for row in rows:
        sheet.append([site_address] + [row[key] for key, _ in FIELDS])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="102B46")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.data_type = "s"
            cell.number_format = "@"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for column in sheet.columns:
        letter = column[0].column_letter
        width = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[letter].width = min(max(width + 2, 14), 42)

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return Response(output.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{site_filename}.xlsx"'})

@app.get("/export.csv")
def export_csv():
    site_id = session.get("current_site_id")
    conn = db()
    site = conn.execute("SELECT street, house_number, inventory_number FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    if site_id:
        rows = conn.execute("SELECT " + ", ".join(key for key, _ in FIELDS) + " FROM access_points WHERE site_id=? ORDER BY id DESC", (site_id,)).fetchall()
    else:
        rows = []
    conn.close()
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Standort"] + [label for _, label in FIELDS])
    site_address = f"{site['street']} {site['house_number']}".strip() if site else ""
    site_filename = secure_filename(" ".join(part for part in [site["street"], site["house_number"], site["inventory_number"]] if part).strip()) if site else "netzwerk-dokumentation"
    writer.writerows([site_address] + [row[key] for key, _ in FIELDS] for row in rows)
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{site_filename}.csv"'})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8086)
