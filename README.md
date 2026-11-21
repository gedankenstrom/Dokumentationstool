# Netzwerk-Dokumentation

Responsives Webtool zur Dokumentation verschiedener Netzwerkkomponenten an mehreren Standorten. Wähle den Standort und die gewünschte Dokumentationsart. Die Daten werden dauerhaft in einem Docker-Volume gespeichert.

## In Portainer als Stack starten

1. In Portainer **Stacks** öffnen und **Add stack** wählen.
2. Einen Stack-Namen vergeben, zum Beispiel `dokumentationstool`.
3. **Web editor** auswählen und den Inhalt von `docker-compose.yml` einfügen. Alternativ bei **Git Repository** dieses Repository und `docker-compose.yml` als Compose path auswählen.
4. **Deploy the stack** wählen.
5. In der Weboberfläche zuerst Straße und Hausnummer als Standort anlegen. Optional kann zusätzlich eine IM- oder VITKO-Nummer hinterlegt werden. Der neue Standort wird direkt ausgewählt; danach können die Geräte und andere Netzwerkkomponenten dafür dokumentiert werden. Für weitere Gebäude legst du einen weiteren Standort an und wechselst über die Standortauswahl.

Der Stack lädt `ghcr.io/gedankenstrom/dokumentationstool:latest` und veröffentlicht die Weboberfläche auf Port **8086**. Im Browser öffnen: `http://<IP-Adresse-des-Docker-Hosts>:8086`.

Das Volume `dokumentation-daten` enthält die Datenbank und bleibt bei Container-Neustarts erhalten.

## GitHub Container Registry

GitHub Actions baut das Image bei jedem Push auf `main` und veröffentlicht es in GHCR. Beim ersten Veröffentlichen ist das Container-Paket möglicherweise privat. Damit Portainer das Image ohne Registry-Anmeldung laden kann:

1. Auf GitHub das Repository öffnen und unter **Packages** das Paket `dokumentationstool` auswählen.
2. In den Paket-Einstellungen die Sichtbarkeit auf **Public** setzen.
3. Danach den Stack in Portainer deployen oder mit **Pull and redeploy** aktualisieren.

## Smartphone-Zugriff

iPhone und Android müssen im selben Netzwerk wie der Docker-Host sein. Verwende die IP-Adresse des Hosts mit Port 8086. Für Zugriff von außerhalb des lokalen Netzwerks VPN oder HTTPS-Reverse-Proxy verwenden.

## Enthalten

- Mehrere Standorte mit Straße und Hausnummer sowie optionaler IM-/VITKO-Nummer; jeder Access Point ist einem Standort zugeordnet
- Die IM-/VITKO-Nummer ist eine Standortangabe und kein Geräte-Erfassungsfeld; iOS soll sie nicht automatisch als Telefonnummer verlinken
- CSV- und Excel-Export enthalten den Standort als zusätzliche Spalte; die Exportdatei wird nach Straße, Hausnummer, optionaler IM-/VITKO-Nummer und Dokumentationsart benannt
- Beim Export kann zwischen direktem Download und Teilen gewählt werden; auf iPhone/iPad lässt sich die Datei über das Teilen-Menü zum Beispiel an Apple Mail übergeben
- Für das Teilen-Menü ist HTTPS erforderlich; über eine reine HTTP-Adresse steht weiterhin der Download zur Verfügung
- Excel-Export speichert Raum, MAC-Adresse und Seriennummer als Text, damit Excel sie nicht automatisch in Datum oder Zahlen umwandelt
- Beim Löschen eines Standorts werden nach Bestätigung auch die zugeordneten Geräte gelöscht
- Dokumentationsvorlagen in der Weboberfläche erstellen und verwalten, zum Beispiel für Access Points, Datendosen oder Switches
- Je Vorlage eigene Felder anlegen, umbenennen, ein- oder ausblenden, als Pflichtfeld markieren und sortieren
- Für Textfelder optional „Auswahl merken“ aktivieren; bereits eingegebene Werte derselben Dokumentationsart erscheinen dann im Dropdown; eigene Werte lassen sich weiterhin ergänzen
- Die bestehende Access-Point-Erfassung bleibt als Vorlage erhalten; vorhandene Einträge bleiben bei Änderungen erhalten
- CSV- und Excel-Export verwenden die Felder und Bezeichnungen der ausgewählten Vorlage
- AP-Modell aus vorhandenen Einträgen auswählen oder neue Modelle direkt hinzufügen; das zuletzt gespeicherte Modell wird je Standort vorausgewählt
- Bluetooth-Tastaturscanner: Enter springt zum nächsten Feld und speichert den Datensatz nicht automatisch
- Einträge mit Feldern je Dokumentationsvorlage anlegen, bearbeiten, löschen und durchsuchen
- Einträge anlegen, bearbeiten, löschen und durchsuchen
- CSV-Export mit Semikolon als Trennzeichen
- Persistente Speicherung im Docker-Volume `dokumentation-daten`

Bereits vorhandene Access Points aus der vorherigen Einzelstandort-Version werden beim ersten Start automatisch dem bisher eingetragenen Standort zugeordnet.

## Datensicherung

Docker benennt das Volume mit einem Präfix aus dem Stack-Namen, zum Beispiel `dokumentationstool_dokumentation-daten`. Backup-Beispiel:

```sh
docker run --rm -v dokumentationstool_dokumentation-daten:/data -v "$PWD":/backup alpine tar czf /backup/dokumentation-daten.tar.gz -C /data .
```
