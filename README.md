# Netzwerk-Dokumentation

Ein kleines, responsives Webtool zur Dokumentation von Access Points. Die Oberfläche funktioniert auf Smartphones, Tablets und Desktopgeräten. Daten bleiben in einer SQLite-Datenbank im persistenten Docker-Volume.

## Starten

Im Projektordner ausführen:

```sh
docker compose up -d --build
```

Dann im Browser öffnen: `http://localhost:5000`.

Auf iPhone oder Android im selben WLAN die lokale IP-Adresse des Docker-Hosts verwenden, zum Beispiel `http://192.168.1.20:5000`. Für Zugriff von außerhalb des lokalen Netzes sollte ein VPN oder ein HTTPS-Reverse-Proxy verwendet werden.

## Enthalten

- AP Modell, Seriennummer, MAC Adresse, Datenverteiler, Datendose / Port, Switch, Switch Port und Raum
- Einträge anlegen, bearbeiten, löschen und durchsuchen
- CSV-Export mit Semikolon als Trennzeichen
- Persistente Speicherung im Volume `netzwerk-daten`

## Datensicherung

Das Datenvolume heißt `netzwerk-daten`. Ein Beispiel-Backup:

```sh
docker run --rm -v netzwerk-daten:/data -v "$PWD":/backup alpine tar czf /backup/netzwerk-daten.tar.gz -C /data .
```
