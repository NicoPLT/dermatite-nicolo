# Audit della dashboard originale

La prima domanda è quanto costa il carrello e quanti prodotti comprende. Nella scansione del 18 novembre 2026 il totale è **43,53 € per 3 prodotti su 5**. A 375 px serve scorrere oltre la prima schermata per leggerlo.

## Schermate e metodo

Playwright con Chromium headless ha aperto la versione iniziale tramite `python -m http.server`, con i tre JSON del repository e i CDN raggiungibili. Le immagini alla scala 1× mostrano il primo schermo. La quarta inquadratura documenta una tabella durante lo scorrimento. Le coordinate sono in pixel CSS dal bordo superiore del documento.

| Finestra | Posizione verticale del totale | Altezza della panoramica | Altezza della pagina |
| --- | ---: | ---: | ---: |
| [375 × 812](screenshots/prima/reale-375.png) | 953 | 426 | 12.489 |
| [768 × 1024](screenshots/prima/reale-768.png) | 732 | 298 | 8.932 |
| [1280 × 900](screenshots/prima/reale-1280.png) | 628 | 214 | 7.653 |

## Problemi osservati

| Aspetto | Osservazione | Obiettivo del redesign |
| --- | --- | --- |
| Gerarchia | Sei indicatori operativi precedono il costo del carrello. Anche il tempo di scansione ha la stessa enfasi delle offerte valide. Il badge verde «Affidabilità alta» è associato a una soluzione parziale, spiegata da un avviso sopra il riquadro. | Portare totale, copertura e affidabilità nello stesso blocco. Spostare i dati operativi nelle sezioni di approfondimento. |
| Densità | A 375 px il carrello occupa 2.002 px e il confronto prodotti 3.022 px. Le classifiche e gli elenchi di offerte competono con la sintesi. | Mostrare per primi il vincitore e un risultato per prodotto. Rendere consultabili su richiesta le alternative e le classifiche. |
| Allineamento | Su desktop il prezzo a sinistra e il giudizio di affidabilità all'estrema destra richiedono due punti di lettura. I nomi lunghi nelle righe spostano i dettagli su più linee. | Raggruppare gli indicatori che qualificano il totale, riservare una colonna ai prezzi e una seconda riga ai dettagli. |
| Tabelle su telefono | A 375 px la pagina mantiene la larghezza del viewport, ma la tabella delle farmacie fisiche è larga 640 px. Sono visibili solo il nome e una parte dell'indirizzo. Distanza, viaggio e disponibilità richiedono uno scorrimento laterale. | Associare le etichette ai valori in card verticali. [Schermata delle farmacie vicine](screenshots/prima/tabella-375.png) |
| Contrasto | Il testo secondario `#64748b` su bianco raggiunge circa 4,76:1. I valori mancanti e i prezzi barrati in alcune celle usano `text-slate-400` (`#94a3b8`), circa 2,56:1 su bianco. | Usare un colore leggibile anche per i dati assenti. Verificare tutte le coppie testo/sfondo e distinguere gli stati anche con testo e icona. |
| Spaziatura e tipografia | L'intestazione consuma molto spazio prima dei risultati su telefono. Nel confronto secondario si passa da titoli di 18 px a didascalie di 10–12 px, con padding locali diversi tra celle, liste e card. | Definire ruoli tipografici e spaziature comuni, con numeri tabulari e righe di dettaglio coerenti. |
| Navigazione | I collegamenti del menu sono alti 20 px. La barra sparisce durante lo scorrimento e manca un'indicazione della sezione corrente. | Garantire target di almeno 44 px, navigazione persistente e stato corrente percepibile. |
| Caricamento e freschezza | Le scritte «Caricamento dati…» occupano una sola riga. I contenuti completati possono occuparne decine. La data assoluta richiede di stimare l'età della scansione. | Riservare spazio per i contenuti principali. Mostrare età e segnale di freschezza insieme alla data. |
| Errori e vuoti | Con lo storico portato a HTTP 503, il carrello e il confronto rimangono disponibili. La sezione storico indica soltanto il file indisponibile. Con un catalogo senza offerte, il testo invita a eseguire lo scraper, un'azione tecnica per chi consulta la pagina. | Conservare l'indipendenza delle sezioni, spiegare il risultato mancante e indicare un'azione adatta a chi sta leggendo. |
| Storico e preferenze | Con una sola giornata si rimanda alla vista tabella. L'asse Y del grafico utilizza un dominio automatico. Il salvataggio normale delle preferenze manca di una conferma dedicata. | Mostrare il dato isolato, distinguere due punti da una serie estesa, segnalare la scala e confermare il salvataggio nel browser. |

Le prove di errore e di assenza di offerte hanno sostituito le risposte HTTP solo nella sessione Playwright. I JSON di produzione sono rimasti intatti.
