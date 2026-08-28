# Alpha Engine 8.5 · Cost-Aware Edge Core

Motore cross-asset nettamente finanziario con orizzonti adattivi, Signal Lock forward, provenienza evidence-only, costi prudenziali per asset class, watchdog/recovery, doppio QA e pubblicazione Pages tramite allowlist.

Include:
- Web app ottimizzata per iPhone e safe-area.
- Installazione su Home con modalità standalone.
- Icona dedicata.
- Service Worker e fallback offline.
- Ultimi dati salvati anche in localStorage.
- Preferiti locali su iPhone.
- Condivisione nativa iOS.
- Copia ticker con tap.
- Navigazione inferiore stile app.
- Aggiornamento automatico ogni 15 minuti lato interfaccia.
- Motore GitHub Actions ogni 30 minuti nei feriali, con watchdog indipendente e ricevuta pubblica di automazione.
- Backtest walk-forward con rendimenti al netto di una stima prudenziale dei costi round-trip; tasse e market impact restano limiti dichiarati.

## Pubblicazione
1. Carica tutti i file nel repository.
2. Settings → Pages.
3. Deploy from a branch.
4. Branch: main.
5. Folder: / (root).
6. Save.
7. Actions → Update Alpha Engine Data → Run workflow.
8. Apri il link GitHub Pages in Safari.
9. Condividi → Aggiungi alla schermata Home.
