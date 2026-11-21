# Dokumentation

Responsives Webtool zur Erfassung und Verwaltung von Netzwerkgeräten und technischen Informationen an mehreren Standorten. Wähle zuerst den Standort und danach die Dokumentationsart. Die Daten bleiben in einer SQLite-Datenbank im Docker-Volume gespeichert.

## In Portainer starten

1. In Portainer **Stacks** öffnen und **Add stack** wählen.
2. Einen Stack-Namen vergeben, zum Beispiel `dokumentationstool`.
3. **Web editor** wählen und den Inhalt von `docker-compose.yml` einfügen. Alternativ **Git Repository** auswählen und dieses Repository mit `docker-compose.yml` als Compose path verwenden.
4. **Deploy the stack** wählen.
5. In der Weboberfläche einen Standort anlegen und eine Dokumentationsart auswählen.

Der Stack lädt `ghcr.io/gedankenstrom/dokumentationstool:latest` und stellt die Oberfläche unter Port **8086** bereit:

`http://<IP-Adresse-des-Docker-Hosts>:8086`

Das Docker-Volume `dokumentation-daten` enthält die Datenbank und bleibt bei Container-Neustarts erhalten.

## Updates über GitHub und Portainer

GitHub Actions baut bei jedem Push auf `main` ein neues Image und veröffentlicht es in GHCR. Zum Aktualisieren des laufenden Stacks in Portainer **Pull and redeploy** wählen.

Falls Portainer das Image nicht laden kann, im GitHub-Repository unter **Packages** das Paket `dokumentationstool` öffnen und seine Sichtbarkeit auf **Public** setzen.

## Standorte

- Ein Standort besteht aus Straße und Hausnummer. Optional kannst du eine IM- oder VITKO-Nummer hinterlegen.
- Beim Anlegen eines Standorts werden bereits verwendete Straßen vorgeschlagen. Nach der Straßenauswahl schlägt das Hausnummernfeld passende Nummern für diese Straße vor.
- Die IM-/VITKO-Nummer gehört zum Standort, nicht zu den einzelnen Gerätefeldern. Sie wird nicht als Exportspalte ausgegeben.
- Beim Löschen eines Standorts werden nach Bestätigung auch alle zugehörigen Einträge gelöscht.

## Dokumentationsvorlagen und Felder

Du kannst eigene Dokumentationsarten anlegen, zum Beispiel für Access Points, Notebooks, Datendosen oder Switches. Die Access-Point-Vorlage bleibt als Startvorlage erhalten.

Unter **Vorlagen verwalten** kannst du für jede eigene Vorlage:

- Textfelder hinzufügen, umbenennen, ein- oder ausblenden
- Felder als Pflichtfelder markieren
- die Reihenfolge mit **↑** und **↓** ändern
- optional **Dropdown mit bisherigen Werten** aktivieren

Wenn die Dropdown-Option aktiv ist, stehen bereits verwendete Werte der Dokumentationsart zur Auswahl. Der zuletzt gespeicherte Wert wird je Standort und Dokumentationsart beim nächsten Eintrag vorausgewählt. Neue Werte lassen sich weiterhin eingeben.

Die Felder der Access-Point-Vorlage lassen sich ebenfalls verwalten. Für das AP-Modell ist das Dropdown mit bisherigen Werten optional. Das zuletzt gespeicherte Modell wird je Standort vorausgewählt.

Änderungen an Feldern löschen keine vorhandenen Werte. Eigene Dokumentationsvorlagen können gelöscht werden; dabei werden auch alle zugehörigen Einträge gelöscht. Die Access-Point-Vorlage ist geschützt.

## Erfassung und Übersicht

- Einträge lassen sich anlegen, bearbeiten, löschen und durchsuchen.
- Die Übersicht passt sich der ausgewählten Dokumentationsart an.
- Die Erfassung und Übersicht sind für iPhone, Android und Desktop ausgelegt.
- Bei einem Bluetooth-Tastaturscanner springt **Enter** zum nächsten Feld. Es speichert den Eintrag nicht automatisch; am letzten Feld wird Enter ebenfalls abgefangen.
- Einträge werden erst mit **Speichern** gesichert.

## CSV- und Excel-Export

Der Export bezieht sich auf den aktuell ausgewählten Standort und enthält Einträge aller Dokumentationsarten. Jede Dokumentationsart erscheint als eigener Abschnitt mit eigener Überschrift und den passenden Spalten. Auch Vorlagen ohne Einträge werden im Export aufgeführt.

Die Standortadresse steht in jeder Zeile. Die Exportdatei wird nach Adresse und – falls vorhanden – IM-/VITKO-Nummer benannt. Die IM-/VITKO-Nummer selbst wird nicht als Tabellenspalte ausgegeben.

Beim Export kannst du **Herunterladen** oder **Per E-Mail / Teilen** wählen. Auf iPhone und iPad öffnet die zweite Option das iOS-Teilen-Menü, über das du zum Beispiel Apple Mail auswählen kannst. Dafür muss die Weboberfläche über HTTPS erreichbar sein. Über eine reine HTTP-Adresse steht der Download zur Verfügung.

Der CSV-Export verwendet Semikolon als Trennzeichen. Im Excel-Export werden Werte wie Seriennummer, MAC-Adresse und Raum als Text ausgegeben, damit Excel sie nicht automatisch als Datum oder Zahl umwandelt.

## Smartphone-Zugriff

iPhone und Android müssen sich im selben Netzwerk wie der Docker-Host befinden. Öffne die Weboberfläche mit der IP-Adresse des Hosts und Port 8086. Für Zugriff außerhalb des lokalen Netzwerks VPN oder einen HTTPS-Reverse-Proxy verwenden.

## Datensicherung

Docker versieht das Volume mit einem Präfix aus dem Stack-Namen, zum Beispiel `dokumentationstool_dokumentation-daten`. Backup-Beispiel:

```sh
docker run --rm -v dokumentationstool_dokumentation-daten:/data -v "$PWD":/backup alpine tar czf /backup/dokumentation-daten.tar.gz -C /data .
```

Vor einem Update oder einer Änderung am Stack empfiehlt sich eine Sicherung des Volumes.
