from flask import Flask, Response, abort, redirect, render_template_string, request, url_for
import csv
import io
import os
import sqlite3

app = Flask(__name__)
DB_PATH = os.environ.get("DATABASE_PATH", "/data/inventory.db")
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
    return conn

PAGE = r'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#102b46"><title>Netzwerk-Dokumentation</title>
<style>
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172b3a;background:#f2f6f9;font-synthesis:none}*{box-sizing:border-box}body{margin:0}.top{background:#102b46;color:white;padding:24px max(20px,calc((100% - 1100px)/2));}.top small{color:#a9c8df;letter-spacing:.12em;text-transform:uppercase;font-weight:700}.top h1{margin:6px 0 0;font-size:clamp(1.4rem,4vw,2rem)}main{max-width:1100px;margin:26px auto;padding:0 18px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}.button{border:0;border-radius:10px;padding:12px 16px;background:#087f8c;color:white;font-weight:700;text-decoration:none;cursor:pointer;font-size:.95rem}.button.secondary{background:white;color:#254357;border:1px solid #d6e1e8}.button.danger{background:#fff0ed;color:#a63f2d}.card{background:#fff;border:1px solid #e0e8ee;border-radius:16px;padding:20px;box-shadow:0 5px 18px #16324a0a;margin-bottom:18px}.card h2{margin:0 0 18px;font-size:1.1rem}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field label{display:block;font-size:.83rem;font-weight:700;color:#405a6b;margin:0 0 6px}.field input{width:100%;height:46px;padding:10px 12px;border:1px solid #cad8e1;border-radius:9px;font:inherit;background:#fff}.field input:focus,.search:focus{outline:3px solid #087f8c33;border-color:#087f8c}.form-foot{display:flex;gap:10px;margin-top:18px}.searchrow{display:flex;gap:10px;margin-bottom:14px}.search{flex:1;min-width:0;height:44px;border:1px solid #d3dfe7;border-radius:9px;padding:10px 12px;font:inherit}.count{color:#617988;font-size:.9rem;margin:0 0 12px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:.9rem}th{text-align:left;color:#5b7180;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;padding:11px 10px;border-bottom:1px solid #e1e9ee}td{padding:13px 10px;border-bottom:1px solid #edf1f4;vertical-align:top}tbody tr:last-child td{border:0}.actions-cell{white-space:nowrap}.link{color:#087f8c;font-weight:700;text-decoration:none}.empty{text-align:center;padding:30px;color:#647987}.flash{padding:12px;border-radius:9px;background:#e7f5f2;color:#12695d;margin-bottom:15px}.mobile-list{display:none}.mobile-item{border-top:1px solid #e7edf1;padding:14px 0}.mobile-item:first-child{border:0}.mobile-title{display:flex;justify-content:space-between;gap:12px;font-weight:750}.mobile-meta{color:#526a7a;font-size:.9rem;line-height:1.6;margin-top:7px}.mobile-meta b{color:#253f50}.mobile-links{display:flex;gap:14px;margin-top:9px}.note{color:#708391;font-size:.85rem;margin-top:13px}
@media(max-width:700px){main{margin:17px auto;padding:0 13px}.top{padding:20px 16px}.card{padding:16px;border-radius:13px}.grid{grid-template-columns:1fr;gap:12px}.desktop{display:none}.mobile-list{display:block}.searchrow .button{padding:11px 12px}.form-foot .button{flex:1}.actions .button{flex:1;text-align:center}.searchrow{gap:7px}}
</style></head><body><header class="top"><small>IT Inventar</small><h1>Netzwerk-Dokumentation</h1></header><main>
{% with messages = get_flashed_messages() %}{% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}{% endwith %}
<div class="actions"><a class="button" href="#entry">＋ Gerät eintragen</a><a class="button secondary" href="{{ url_for('export_csv') }}">↓ CSV exportieren</a></div>
<section class="card" id="entry"><h2>{{ 'Eintrag bearbeiten' if item else 'Access Point dokumentieren' }}</h2><form method="post" action="{{ url_for('save', record_id=item.id) if item else url_for('save') }}"><div class="grid">
{% for key,label in fields %}<div class="field"><label for="{{ key }}">{{ label }}</label><input id="{{ key }}" name="{{ key }}" value="{{ item[key] if item else '' }}" {% if key=='ap_model' %}placeholder="z. B. UniFi U6 Pro"{% elif key=='mac_address' %}placeholder="z. B. AA:BB:CC:DD:EE:FF" autocapitalize="characters"{% elif key=='room' %}placeholder="z. B. 2. OG · Büro 2.14"{% endif %} {% if key=='ap_model' %}required{% endif %}></div>{% endfor %}
</div><div class="form-foot"><button class="button" type="submit">{{ 'Änderungen speichern' if item else 'Eintrag speichern' }}</button>{% if item %}<a class="button secondary" href="{{ url_for('index') }}">Abbrechen</a>{% endif %}</div></form></section>
<section class="card"><h2>Dokumentierte Geräte</h2><form class="searchrow" method="get"><input class="search" name="q" value="{{ q }}" placeholder="Suche: Modell, MAC, Raum, Switch …"><button class="button" type="submit">Suchen</button>{% if q %}<a class="button secondary" href="{{ url_for('index') }}">Zurücksetzen</a>{% endif %}</form><p class="count">{{ rows|length }} {{ 'Eintrag' if rows|length==1 else 'Einträge' }}{% if q %} für „{{ q }}“{% endif %}</p>
{% if rows %}<div class="tablewrap desktop"><table><thead><tr>{% for key,label in fields %}<th>{{ label }}</th>{% endfor %}<th></th></tr></thead><tbody>{% for row in rows %}<tr>{% for key,label in fields %}<td>{{ row[key] or '–' }}</td>{% endfor %}<td class="actions-cell"><a class="link" href="{{ url_for('edit', record_id=row.id) }}">Bearbeiten</a> · <form style="display:inline" method="post" action="{{ url_for('delete', record_id=row.id) }}" onsubmit="return confirm('Diesen Eintrag löschen?')"><button class="link" style="border:0;background:none;padding:0;cursor:pointer" type="submit">Löschen</button></form></td></tr>{% endfor %}</tbody></table></div>
<div class="mobile-list">{% for row in rows %}<div class="mobile-item"><div class="mobile-title"><span>{{ row.ap_model or 'Access Point' }}</span><span>{{ row.room or '' }}</span></div><div class="mobile-meta">{% if row.serial_number %}<b>Seriennummer:</b> {{ row.serial_number }}<br>{% endif %}{% if row.mac_address %}<b>MAC:</b> {{ row.mac_address }}<br>{% endif %}{% if row.distribution or row.outlet_port %}<b>Datendose:</b> {{ row.distribution or '–' }} · {{ row.outlet_port or '–' }}<br>{% endif %}{% if row.switch_name or row.switch_port %}<b>Switch:</b> {{ row.switch_name or '–' }} · Port {{ row.switch_port or '–' }}{% endif %}</div><div class="mobile-links"><a class="link" href="{{ url_for('edit', record_id=row.id) }}">Bearbeiten</a><form method="post" action="{{ url_for('delete', record_id=row.id) }}" onsubmit="return confirm('Diesen Eintrag löschen?')"><button class="link" style="border:0;background:none;padding:0;cursor:pointer" type="submit">Löschen</button></form></div></div>{% endfor %}</div>
{% else %}<div class="empty">{% if q %}Keine passenden Einträge gefunden.{% else %}Noch keine Geräte eingetragen. Lege den ersten Eintrag oben an.{% endif %}</div>{% endif %}<p class="note">Die Daten werden lokal in einer SQLite-Datei im Docker-Datenvolume gespeichert.</p></section></main></body></html>'''

@app.get("/")
def index():
    q = request.args.get("q", "").strip()
    conn = db()
    if q:
        like = f"%{q}%"
        where = " OR ".join(f"{key} LIKE ?" for key, _ in FIELDS)
        rows = conn.execute(f"SELECT * FROM access_points WHERE {where} ORDER BY id DESC", [like] * len(FIELDS)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM access_points ORDER BY id DESC").fetchall()
    conn.close()
    return render_template_string(PAGE, rows=rows, q=q, fields=FIELDS, item=None)

@app.post("/save")
@app.post("/save/<int:record_id>")
def save(record_id=None):
    values = [request.form.get(key, "").strip() for key, _ in FIELDS]
    conn = db()
    if record_id:
        conn.execute("UPDATE access_points SET " + ", ".join(f"{key}=?" for key, _ in FIELDS) + " WHERE id=?", values + [record_id])
        message = "Eintrag aktualisiert."
    else:
        conn.execute("INSERT INTO access_points (" + ", ".join(key for key, _ in FIELDS) + ") VALUES (" + ", ".join("?" for _ in FIELDS) + ")", values)
        message = "Gerät gespeichert."
    conn.commit(); conn.close()
    from flask import flash
    flash(message)
    return redirect(url_for("index"))

@app.get("/edit/<int:record_id>")
def edit(record_id):
    conn = db(); item = conn.execute("SELECT * FROM access_points WHERE id=?", (record_id,)).fetchone(); conn.close()
    if not item: abort(404)
    conn = db(); rows = conn.execute("SELECT * FROM access_points ORDER BY id DESC").fetchall(); conn.close()
    return render_template_string(PAGE, rows=rows, q="", fields=FIELDS, item=item)

@app.post("/delete/<int:record_id>")
def delete(record_id):
    conn = db(); conn.execute("DELETE FROM access_points WHERE id=?", (record_id,)); conn.commit(); conn.close()
    from flask import flash
    flash("Eintrag gelöscht.")
    return redirect(url_for("index"))

@app.get("/export.csv")
def export_csv():
    conn = db(); rows = conn.execute("SELECT " + ", ".join(key for key, _ in FIELDS) + " FROM access_points ORDER BY id DESC").fetchall(); conn.close()
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";"); writer.writerow([label for _, label in FIELDS])
    writer.writerows([row[key] for key, _ in FIELDS] for row in rows)
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=netzwerk-dokumentation.csv"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8086)
