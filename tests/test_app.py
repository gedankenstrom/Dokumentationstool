import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path

# Give app.py a writable database path before importing the Flask application.
os.environ["DATABASE_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="dokumentation-tests-"), "bootstrap.db"
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import app as documentation


def rows(sql, params=()):
    conn = documentation.db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def create_site(client):
    response = client.post(
        "/sites/create",
        data={"street": "Teststraße", "house_number": "12", "inventory_number": "IM-42"},
    )
    assert response.status_code == 302
    # Initialize the default documentation type stored in the session.
    assert client.get("/").status_code == 200
    with client.session_transaction() as session:
        return session["current_site_id"]


def create_type(client, name):
    response = client.post("/templates", data={"action": "create_type", "name": name})
    assert response.status_code == 302
    result = rows("SELECT id FROM documentation_types WHERE name=?", (name,))
    assert result
    return result[0]["id"]


def add_field(client, type_id, label, **options):
    response = client.post(
        "/templates",
        data={"action": "add_field", "type_id": str(type_id), "label": label, **options},
    )
    assert response.status_code == 302


def ap_payload(**overrides):
    payload = {
        "ap_model_choice": "__new__",
        "ap_model_new": "AP-500",
        "serial_number": "SN-100",
        "hardware_mac": "AA:BB:CC:DD:EE:FF",
        "distribution": "DV-1",
        "outlet_port": "P-2",
        "network_switch_device": "SW-1",
        "switch_port": "3",
        "room": "02. Okt",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(documentation, "DB_PATH", str(tmp_path / "inventory.db"))
    documentation.app.config.update(TESTING=True, SECRET_KEY="pytest-secret")
    with documentation.app.test_client() as test_client:
        yield test_client


def test_access_point_can_be_created_and_updated(client):
    create_site(client)

    response = client.post("/save", data=ap_payload())
    assert response.status_code == 302
    record = rows(
        "SELECT * FROM access_points WHERE doc_type_id="
        "(SELECT id FROM documentation_types WHERE name='Access Point')"
    )
    assert len(record) == 1
    record_id = record[0]["id"]
    assert record[0]["ap_model"] == "AP-500"
    assert record[0]["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert record[0]["room"] == "02. Okt"

    assert client.get(f"/edit/{record_id}").status_code == 200
    response = client.post(f"/save/{record_id}", data=ap_payload(room="Raum 02"))
    assert response.status_code == 302
    updated = rows("SELECT room FROM access_points WHERE id=?", (record_id,))
    assert updated[0]["room"] == "Raum 02"


def test_custom_template_required_field_and_exports(client):
    create_site(client)
    type_id = create_type(client, "Notebook")
    add_field(client, type_id, "Modell", required="1", remember_values="1")
    add_field(client, type_id, "Raum")

    client.post("/documentation-type/select", data={"type_id": str(type_id)})
    response = client.post("/save", data={"custom_modell_choice": "", "custom_raum": "02. Okt"})
    assert response.status_code == 302
    assert not rows("SELECT id FROM access_points WHERE doc_type_id=?", (type_id,))

    response = client.post(
        "/save",
        data={
            "custom_modell_choice": "__new__",
            "custom_modell_new": "Lenovo TP L13 5G",
            "custom_raum": "02. Okt",
        },
    )
    assert response.status_code == 302
    record = rows("SELECT custom_data FROM access_points WHERE doc_type_id=?", (type_id,))
    assert len(record) == 1
    assert json.loads(record[0]["custom_data"]) == {
        "custom_modell": "Lenovo TP L13 5G",
        "custom_raum": "02. Okt",
    }

    csv_response = client.get("/export.csv")
    assert csv_response.status_code == 200
    csv_text = csv_response.get_data(as_text=True).lstrip("\ufeff")
    csv_rows = list(csv.reader(io.StringIO(csv_text), delimiter=";"))
    assert csv_rows[0][0] == '="Dokumentationsart: Notebook"'
    assert not any(row and row[0] == '="Dokumentationsart: Access Point"' for row in csv_rows)
    assert any('="02. Okt"' in row for row in csv_rows)
    assert any('="Lenovo TP L13 5G"' in row for row in csv_rows)

    xlsx_response = client.get("/export.xlsx")
    assert xlsx_response.status_code == 200
    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(xlsx_response.data), data_only=False).active
    values = [cell.value for row in sheet.iter_rows() for cell in row if cell.value is not None]
    assert "Dokumentationsart: Notebook" in values
    assert "Dokumentationsart: Access Point" not in values
    assert "02. Okt" in values


def test_template_manager_opens_selected_documentation_type(client):
    create_site(client)
    notebook_type = create_type(client, "Notebook")
    add_field(client, notebook_type, "Modell")
    client.post("/documentation-type/select", data={"type_id": str(notebook_type)})

    page = client.get("/").get_data(as_text=True)
    assert f'href="/templates?type_id={notebook_type}">Vorlagen verwalten</a>' in page

    manager = client.get(f"/templates?type_id={notebook_type}").get_data(as_text=True)
    assert "Felder: Notebook" in manager
    assert "Felder: Access Point" not in manager


def test_old_device_return_is_available_as_followup_without_new_rq_prefill(client):
    create_site(client)
    notebook_type = create_type(client, "Notebook")
    add_field(client, notebook_type, "V Nummer")
    add_field(client, notebook_type, "RQ Nummer")
    client.post("/documentation-type/select", data={"type_id": str(notebook_type)})

    return_type = rows(
        "SELECT id,workflow_kind FROM documentation_types WHERE name='Altgeräte-Rücknahme'"
    )[0]
    assert return_type["workflow_kind"] == "old_device_return"
    return_fields = rows(
        "SELECT field_key,label,required FROM documentation_fields WHERE type_id=? ORDER BY position",
        (return_type["id"],),
    )
    assert [field["label"] for field in return_fields] == ["Gerätetyp/Modell", "Seriennummer"]
    assert all(field["required"] for field in return_fields)

    page = client.get("/").get_data(as_text=True)
    picker = page.split('id="followup_type_id"', 1)[1].split("</select>", 1)[0]
    assert '<strong class="next-step-title">Nächster Schritt (optional)</strong>' in page
    assert "ein weiteres Gerät dokumentieren oder ein Altgerät zurücknehmen." in page
    assert '<option value="">Kein weiteres Gerät</option>' in picker
    assert '<optgroup label="Folgegerät dokumentieren">' in picker
    assert f'<optgroup label="Altgerät zurücknehmen"><option value="{return_type["id"]}">Altgeräte-Rücknahme</option></optgroup>' in picker

    response = client.post(
        "/save",
        data={
            "custom_v_nummer": "V-123",
            "custom_rq_nummer": "NB-NEW-123",
            "continue_followup": "1",
            "followup_type_id": str(return_type["id"]),
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["documentation_type_id"] == return_type["id"]
        assert session["followup_context"]["rq_number"] == "NB-NEW-123"

    page = client.get("/").get_data(as_text=True)
    assert "Gerätetyp/Modell" in page
    assert "Seriennummer" in page
    assert "Altgerät zurücknehmen" in page
    assert "Die RQ-Nummer des neuen Notebooks wird nicht übernommen." not in page
    assert 'class="followup-note return-device-note"' in page
    assert 'value="NB-NEW-123"' not in page

    response = client.post(
        "/save",
        data={
            "custom_device_type_model_choice": "__new__",
            "custom_device_type_model_new": "Lenovo ThinkPad",
            "custom_serial_number": "OLD-SN-123",
        },
    )
    assert response.status_code == 302
    old_device = rows(
        "SELECT custom_data FROM access_points WHERE doc_type_id=?",
        (return_type["id"],),
    )[0]
    assert json.loads(old_device["custom_data"]) == {
        "custom_device_type_model": "Lenovo ThinkPad",
        "custom_serial_number": "OLD-SN-123",
    }
    with client.session_transaction() as session:
        assert "followup_context" not in session


def test_followup_device_inherits_v_and_rq_numbers(client):
    create_site(client)
    monitor_type = create_type(client, "Monitor")
    add_field(client, monitor_type, "V Nummer")
    add_field(client, monitor_type, "RQ Nummer")
    add_field(client, monitor_type, "Raum")

    outlet_type = create_type(client, "Datendose")
    add_field(client, outlet_type, "Port")

    notebook_type = create_type(client, "Notebook")
    add_field(client, notebook_type, "V Nummer")
    add_field(client, notebook_type, "RQ Nummer")
    add_field(client, notebook_type, "Raum")
    return_type = rows("SELECT id FROM documentation_types WHERE name='Altgeräte-Rücknahme'")[0]["id"]
    client.post("/documentation-type/select", data={"type_id": str(notebook_type)})

    response = client.get("/").get_data(as_text=True)
    assert "Nächster Schritt (optional)" in response
    picker = response.split('id="followup_type_id"', 1)[1].split("</select>", 1)[0]
    assert f'<option value="{monitor_type}">Monitor</option>' in picker
    assert f'<option value="{outlet_type}">Datendose</option>' not in picker

    response = client.post(
        "/save",
        data={
            "custom_v_nummer": "V-123",
            "custom_rq_nummer": "NB-123",
            "custom_raum": "Büro 2.14",
            "followup_type_id": str(monitor_type),
        },
    )
    assert response.status_code == 302
    notebook = rows(
        "SELECT custom_data FROM access_points WHERE doc_type_id=?",
        (notebook_type,),
    )[0]
    assert json.loads(notebook["custom_data"]) == {
        "custom_v_nummer": "V-123",
        "custom_rq_nummer": "NB-123",
        "custom_raum": "Büro 2.14",
    }
    with client.session_transaction() as session:
        assert session["followup_context"] == {
            "v_number": "V-123",
            "rq_number": "NB-123",
            "room": "Büro 2.14",
        }
        assert session["documentation_type_id"] == monitor_type

    page = client.get("/").get_data(as_text=True)
    assert 'name="custom_v_nummer" value="V-123"' in page
    assert 'name="custom_rq_nummer" value="NB-123"' in page
    assert 'name="custom_raum" value="Büro 2.14"' in page
    assert "Folgegerät für denselben Anwender" in page
    picker = page.split('id="followup_type_id"', 1)[1].split("</select>", 1)[0]
    assert f'<optgroup label="Altgerät zurücknehmen"><option value="{return_type}">Altgeräte-Rücknahme</option></optgroup>' in picker

    response = client.post(
        "/save",
        data={
            "custom_v_nummer": "V-123",
            "custom_rq_nummer": "NB-123",
            "custom_raum": "Büro 2.14",
            "followup_type_id": str(return_type),
        },
    )
    assert response.status_code == 302
    monitor = rows(
        "SELECT custom_data FROM access_points WHERE doc_type_id=?",
        (monitor_type,),
    )[0]
    assert json.loads(monitor["custom_data"]) == {
        "custom_v_nummer": "V-123",
        "custom_rq_nummer": "NB-123",
        "custom_raum": "Büro 2.14",
    }
    with client.session_transaction() as session:
        assert session["documentation_type_id"] == return_type
        assert session["followup_context"] == {
            "v_number": "V-123",
            "rq_number": "NB-123",
            "room": "Büro 2.14",
        }
    page = client.get("/").get_data(as_text=True)
    assert "Altgerät zurücknehmen" in page
    assert "Gerätetyp/Modell" in page and "Seriennummer" in page
    response = client.post(
        "/save",
        data={
            "custom_device_type_model_choice": "__new__",
            "custom_device_type_model_new": "Lenovo ThinkPad",
            "custom_serial_number": "OLD-SN-123",
        },
    )
    assert response.status_code == 302
    old_device = rows(
        "SELECT custom_data FROM access_points WHERE doc_type_id=?",
        (return_type,),
    )[0]
    assert json.loads(old_device["custom_data"]) == {
        "custom_device_type_model": "Lenovo ThinkPad",
        "custom_serial_number": "OLD-SN-123",
    }
    with client.session_transaction() as session:
        assert "followup_context" not in session


def test_deleting_site_removes_devices_and_remembered_values(client):
    site_id = create_site(client)
    response = client.post("/save", data=ap_payload())
    assert response.status_code == 302
    assert rows("SELECT field_key FROM site_field_defaults WHERE site_id=?", (site_id,))

    response = client.post(f"/sites/delete/{site_id}")
    assert response.status_code == 302
    assert not rows("SELECT id FROM sites WHERE id=?", (site_id,))
    assert not rows("SELECT id FROM access_points WHERE site_id=?", (site_id,))
    assert not rows("SELECT site_id FROM site_field_defaults WHERE site_id=?", (site_id,))


def test_deleting_custom_template_removes_its_records(client):
    create_site(client)
    type_id = create_type(client, "Datendose")
    add_field(client, type_id, "Port")
    client.post("/documentation-type/select", data={"type_id": str(type_id)})
    assert client.post("/save", data={"custom_port": "A-12"}).status_code == 302

    response = client.post("/templates", data={"action": "delete_type", "type_id": str(type_id)})
    assert response.status_code == 302
    assert not rows("SELECT id FROM documentation_types WHERE id=?", (type_id,))
    assert not rows("SELECT id FROM access_points WHERE doc_type_id=?", (type_id,))


def test_deleted_access_point_field_stays_deleted_after_next_request(client):
    create_site(client)
    ap_type = rows("SELECT id FROM documentation_types WHERE name='Access Point'")[0]["id"]
    field = rows(
        "SELECT id FROM documentation_fields WHERE type_id=? AND field_key='room'",
        (ap_type,),
    )[0]

    response = client.post(
        "/templates",
        data={
            "action": "update_field",
            "type_id": str(ap_type),
            "field_id": str(field["id"]),
            "delete_field": "1",
        },
    )
    assert response.status_code == 302

    # The next request initializes the database again; the deleted field must not be reseeded.
    assert client.get("/").status_code == 200
    assert not rows(
        "SELECT field_key FROM documentation_fields WHERE type_id=? AND field_key='room'",
        (ap_type,),
    )


def test_site_summary_shows_documented_counts_per_type(client):
    create_site(client)
    assert client.post("/save", data=ap_payload()).status_code == 302

    notebook_type = create_type(client, "Notebook")
    add_field(client, notebook_type, "Raum")
    client.post("/documentation-type/select", data={"type_id": str(notebook_type)})
    assert client.post("/save", data={"custom_raum": "Raum 1"}).status_code == 302
    assert client.post("/save", data={"custom_raum": "Raum 2"}).status_code == 302

    empty_type = create_type(client, "Datendose")
    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'class="site-doc-tag">Access Point 1</span>' in page
    assert 'class="site-doc-tag">Notebook 2</span>' in page
    assert 'class="site-doc-tag">Datendose 0</span>' not in page


def test_site_delete_list_distinguishes_same_address_by_inventory_number(client):
    create_site(client)  # Teststraße 12 with IM-42
    client.post(
        "/sites/create",
        data={"street": "Teststraße", "house_number": "12", "inventory_number": ""},
    )

    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "IM-/VITKO: IM-42" in page
    assert "keine IM-/VITKO-Nr." in page


def test_site_delete_list_has_address_and_inventory_search(client):
    create_site(client)
    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="site-delete-search"' in page
    assert 'placeholder="Adresse oder IM-/VITKO-Nummer"' in page
    assert 'data-search="teststraße 12 im-42"' in page
    assert "siteDeleteSearch.addEventListener('input'" in page
    assert ".site-admin-row[hidden]{display:none}" in page
    assert 'class="site-admin-info"' in page
    assert '.site-admin-row{flex-direction:column;align-items:stretch;gap:10px}' in page


def test_old_device_model_dropdown_is_shared_across_sites(client):
    first_site_id = create_site(client)
    return_type = rows(
        "SELECT id FROM documentation_types WHERE workflow_kind='old_device_return'"
    )[0]["id"]
    model_field = rows(
        "SELECT field_key,remember_values FROM documentation_fields "
        "WHERE type_id=? AND label='Gerätetyp/Modell'",
        (return_type,),
    )[0]
    assert model_field["remember_values"] == 1

    client.post("/documentation-type/select", data={"type_id": str(return_type)})
    response = client.post(
        "/save",
        data={
            "custom_device_type_model_choice": "__new__",
            "custom_device_type_model_new": "Lenovo ThinkPad",
            "custom_serial_number": "OLD-SN-1",
        },
    )
    assert response.status_code == 302
    assert rows(
        "SELECT value FROM site_field_defaults WHERE site_id=? AND type_id=? AND field_key=?",
        (first_site_id, return_type, model_field["field_key"]),
    )

    response = client.post(
        "/sites/create",
        data={"street": "Zweite Straße", "house_number": "8", "inventory_number": ""},
    )
    assert response.status_code == 302
    page = client.get("/").get_data(as_text=True)
    assert '<select autocomplete="off" class="remembered-select" id="custom_device_type_model_choice"' in page
    assert 'value="Lenovo ThinkPad"' in page
    assert "Lenovo ThinkPad</option>" in page


def test_documentation_type_selector_separates_old_device_return(client):
    create_site(client)
    return_type = rows(
        "SELECT id FROM documentation_types WHERE workflow_kind='old_device_return'"
    )[0]["id"]
    client.post("/documentation-type/select", data={"type_id": str(return_type)})

    page = client.get("/").get_data(as_text=True)
    selector = page.split('name="type_id"', 1)[1].split("</select>", 1)[0]
    assert '<optgroup label="Geräte dokumentieren">' in selector
    assert '<optgroup label="↩ Altgerät zurücknehmen">' in selector
    assert f'<option value="{return_type}" selected>Altgeräte-Rücknahme</option>' in selector


def test_standalone_old_device_return_can_continue_with_new_device(client):
    create_site(client)
    notebook_type = create_type(client, "Notebook")
    add_field(client, notebook_type, "V Nummer")
    add_field(client, notebook_type, "RQ Nummer")
    return_type = rows(
        "SELECT id FROM documentation_types WHERE workflow_kind='old_device_return'"
    )[0]["id"]
    client.post("/documentation-type/select", data={"type_id": str(return_type)})

    page = client.get("/").get_data(as_text=True)
    picker = page.split('id="followup_type_id"', 1)[1].split("</select>", 1)[0]
    assert '<strong class="next-step-title">Nächster Schritt (optional)</strong>' in page
    assert '<optgroup label="Altgerät zurücknehmen">' in picker
    assert '<optgroup label="Folgegerät dokumentieren">' in picker
    assert f'<option value="{notebook_type}">Notebook</option>' in picker

    response = client.post(
        "/save",
        data={
            "custom_device_type_model_choice": "__new__",
            "custom_device_type_model_new": "Lenovo ThinkPad",
            "custom_serial_number": "OLD-SN-2",
            "followup_type_id": str(notebook_type),
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["documentation_type_id"] == notebook_type
    page = client.get("/").get_data(as_text=True)
    assert "Notebook dokumentieren" in page
    assert 'name="custom_v_nummer" value=""' in page
    assert 'name="custom_rq_nummer" value=""' in page

    response = client.post(
        "/save",
        data={
            "custom_v_nummer": "V-456",
            "custom_rq_nummer": "NB-456",
        },
    )
    assert response.status_code == 302
    assert len(rows(
        "SELECT id FROM access_points WHERE doc_type_id=?",
        (notebook_type,),
    )) == 1


def test_access_point_offers_followup_and_old_device_next_step(client):
    create_site(client)
    notebook_type = create_type(client, "Notebook")
    add_field(client, notebook_type, "V Nummer")
    add_field(client, notebook_type, "RQ Nummer")

    page = client.get("/").get_data(as_text=True)
    picker = page.split('id="followup_type_id"', 1)[1].split("</select>", 1)[0]
    assert '<strong class="next-step-title">Nächster Schritt (optional)</strong>' in page
    assert '<optgroup label="Folgegerät dokumentieren">' in picker
    assert '<optgroup label="Altgerät zurücknehmen">' in picker

    response = client.post(
        "/save",
        data={
            **ap_payload(),
            "followup_type_id": str(notebook_type),
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["documentation_type_id"] == notebook_type
    page = client.get("/").get_data(as_text=True)
    assert "Notebook dokumentieren" in page
    assert 'name="custom_v_nummer" value=""' in page
    assert 'name="custom_rq_nummer" value=""' in page


def test_access_point_can_be_selected_as_followup(client):
    create_site(client)
    ap_type = rows(
        "SELECT id FROM documentation_types WHERE name='Access Point'"
    )[0]["id"]

    page = client.get("/").get_data(as_text=True)
    picker = page.split('id="followup_type_id"', 1)[1].split("</select>", 1)[0]
    assert f'<option value="{ap_type}">Access Point</option>' in picker

    response = client.post(
        "/save",
        data={
            **ap_payload(),
            "followup_type_id": str(ap_type),
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["documentation_type_id"] == ap_type
    assert len(rows("SELECT id FROM access_points WHERE doc_type_id=?", (ap_type,))) == 1


def test_site_documentation_tag_opens_matching_device_overview(client):
    create_site(client)
    response = client.post("/save", data=ap_payload())
    assert response.status_code == 302
    ap_type = rows(
        "SELECT id FROM documentation_types WHERE name='Access Point'"
    )[0]["id"]

    page = client.get("/").get_data(as_text=True)
    assert f'href="/documentation-type/select-from-tag/{ap_type}"' in page
    assert "Access Point 1</a>" in page

    response = client.get(f"/documentation-type/select-from-tag/{ap_type}")
    assert response.status_code == 302
    assert response.location.endswith("#entries")
    with client.session_transaction() as session:
        assert session["documentation_type_id"] == ap_type

    overview = client.get(response.location).get_data(as_text=True)
    assert '<section class="card" id="entries"><h2>Dokumentierte Access Point-Einträge</h2>' in overview
