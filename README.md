# Netzwerk-Dokumentation

Responsives Webtool zur Dokumentation von Access Points. Funktioniert auf Smartphones, Tablets und Desktopgeräten. Daten werden in einer SQLite-Datenbank im persistenten Docker-Volume gespeichert.

## In Portainer als Stack starten

1. In Portainer **Stacks** öffnen und **Add stack** wählen.
2. Einen Stack-Namen vergeben, zum Beispiel `netzwerk-doku`.
3. Als Build-Methode **Git Repository** auswählen.
4. Repository URL: `https://github.com/gedankenstrom/Dokumentationstool`
5. Reference: `refs/heads/main`
6. Compose path: `docker-compose.yml`
7. **Deploy the stack** auswählen.

Portainer lädt das Repository, baut das Image mit dem enthaltenen Dockerfile und startet den Container. Das Volume `netzwerk-daten` hält die Einträge bei einem Neustart oder einer Neuerstellung des Containers dauerhaft vor.

Nach dem Start im Browser öffnen: `http://<IP-Adresse-des-Docker-Hosts>:5000`.

### Anderen Port verwenden

Wenn Port 5000 schon belegt ist, in den Stack-Umgebungsvariablen `WEB_PORT` auf einen freien Port setzen, zum Beispiel `8085`. Dann lautet die Adresse `http://<IP-Adresse-des-Docker-Hosts>:8085`.

### Smartphone-Zugriff

iPhone und Android müssen im selben Netzwerk wie der Docker-Host sein. Die Anwendung ist dann über die IP-Adresse des Hosts erreichbar. Für Zugriff von außerhalb des lokalen Netzwerks VPN oder einen HTTPS-Reverse-Proxy verwenden.

## Lokal mit Docker Compose starten

```sh
docker compose up -d --build
```

Danach `http://localhost:5000` öffnen.

## Enthalten

- AP Modell, Seriennummer, MAC Adresse, Datenverteiler, Datendose / Port, Switch, Switch Port und Raum
- Einträge anlegen, bearbeiten, löschen und durchsuchen
- CSV-Export mit Semikolon als Trennzeichen
- Persistente Speicherung im Volume `netzwerk-daten`

## Datensicherung

Das Docker-Volume heißt in Docker `<stackname>_netzwerk-daten` (bei Stackname `netzwerk-doku` also üblicherweise `netzwerk-doku_netzwerk-daten`). Ein Backup-Beispiel, wenn es als `netzwerk-doku_netzwerk-daten` angelegt wurde:

```sh
docker run --rm -v netzwerk-doku_netzwerk-daten:/data -v "$PWD":/backup alpine tar czf /backup/netzwerk-daten.tar.gz -C /data .
```
