# Cloud-Setup: ERGO Brand Bidding als Remote Routine

Damit der Scan täglich läuft – auch wenn dein Rechner aus ist. Einmalig
einzurichten, danach automatisch.

## Überblick

```
GitHub-Repo (Scripts + Historie + Reports)
        │  klont bei jedem Lauf
        ▼
Remote Routine (claude.ai/code/routines, läuft in der Cloud)
   • Schedule: täglich
   • API-Trigger: jederzeit per HTTPS-POST manuell auslösbar
   • Env-Var: SERPER_API_KEY
   • Allowed domain: google.serper.dev
        │  committet zurück
        ▼
Ergebnisse im Repo  →  von überall einsehbar
```

## Schritt 1 – Repo (erledigt Claude bzw. du)

Privates Repo mit diesen Dateien:
`brand_bidding_scan.py`, `build_report.py`, `serp_secrets.env.example`,
`README.md`, `.gitignore`, `ROUTINE_PROMPT.md`, `SETUP_CLOUD.md`.
(Die echte `serp_secrets.env` wird NICHT hochgeladen – der Key kommt in die Routine.)

## Schritt 2 – Serper-Key besorgen

Auf serper.dev einloggen, API-Key kopieren. (Bezahldienst; Free-Kontingent zum Testen.)

## Schritt 3 – Remote Routine anlegen

1. Öffne **claude.ai/code/routines** → **New routine**.
2. **Name:** ERGO Brand Bidding.
3. **Prompt:** den Inhalt von `ROUTINE_PROMPT.md` einfügen.
4. **Repositories:** dein neues Repo auswählen.
5. **Environment:**
   - **Environment variable** hinzufügen: `SERPER_API_KEY` = dein Key
     (optional `SERP_PROVIDER=serper`).
   - **Network access** → Custom → **Allowed domains**: `google.serper.dev`
     hinzufügen (Häkchen „default list of package managers" anlassen, damit
     `pip install requests` weiter funktioniert).
   - **Setup script** (optional): `pip install requests`.
6. **Permissions:** für das Repo **Allow unrestricted branch pushes** aktivieren
   (damit die Routine Historie/Report nach `main` committen darf).
7. **Trigger:** **Schedule** → Daily → 05:00 (deine lokale Zeit).
   Optional zusätzlich **API** für manuelles Auslösen von außen.
8. **Create** → dann **Run now** zum Testen. Die Run-Ausgabe zeigt die
   5-Zeilen-Zusammenfassung; im Repo erscheinen Historie + Report.

## Manuell von außen auslösen (optional)

In der Routine einen **API-Trigger** hinzufügen, Token generieren, dann:

```bash
curl -X POST https://api.anthropic.com/v1/claude_code/routines/<ROUTINE_ID>/fire \
  -H "Authorization: Bearer <DEIN_TOKEN>" \
  -H "anthropic-beta: experimental-cc-routine-2026-04-01" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" -d '{}'
```

## Gut zu wissen

- **Kosten:** Serper (SERP-Daten). Die Google-Drive-Spiegelung ist hier nicht nötig –
  die Ergebnisse liegen im Repo. Wer trotzdem ein auto-fortgeschriebenes Google
  Sheet will, ergänzt einen Service-Account (Drive/Sheets API) – sag Bescheid.
- **Daten der Wettbewerber:** Es gibt keine offizielle Google-API für fremde
  Anzeigentexte/-positionen; dafür ist der SERP-Anbieter (Serper) nötig. Die
  Google Ads API zeigt nur das eigene ERGO-Konto (Auction Insights).
