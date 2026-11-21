# Netzwerk-Dokumentation

Responsives Webtool zur Dokumentation von Access Points an mehreren Standorten. Wähle zuerst den Standort, an dem du gerade arbeitest; Geräte, Suche und CSV-Export beziehen sich dann auf diesen Standort. Die Daten werden dauerhaft in einem Docker-Volume gespeichert.

## In Portainer als Stack starten

1. In Portainer **Stacks** öffnen und **Add stack** wählen.
2. Einen Stack-Namen vergeben, zum Beispiel `dokumentationstool`.
3. **Web editor** auswählen und den Inhalt von `docker-compose.yml` einfügen. Alternativ bei **Git Repository** dieses Repository und `docker-compose.yml` als Compose path auswählen.
4. **Deploy the stack** wählen.
5. In der Weboberfläche zuerst Straße und Hausnummer als Standort anlegen. Der neue Standort wird direkt ausgewählt; danach können die Access Points dafür eingetragen werden. Für weitere Gebäude legst du einen weiteren Standort an und wechselst über die Standortauswahl.

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

- Mehrere Standorte mit jeweils Straße und Hausnummer; jeder Access Point ist einem Standort zugeordnet
- AP Modell, Seriennummer, MAC Adresse, Datenverteiler, Datendose / Port, Switch, Switch Port und Raum
- Einträge anlegen, bearbeiten, löschen und durchsuchen
- CSV-Export mit Semikolon als Trennzeichen
- Persistente Speicherung im Docker-Volume `dokumentation-daten`

Bereits vorhandene Access Points aus der vorherigen Einzelstandort-Version werden beim ersten Start automatisch dem bisher eingetragenen Standort zugeordnet.

## Datensicherung

Docker benennt das Volume mit einem Präfix aus dem Stack-Namen, zum Beispiel `dokumentationstool_dokumentation-daten`. Backup-Beispiel:

```sh
docker run --rm -v dokumentationstool_dokumentation-daten:/data -v "$PWD":/backup alpine tar czf /backup/dokumentation-daten.tar.gz -C /data .
```
