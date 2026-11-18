# Design system della dashboard

Il totale del carrello, la sua copertura e il grado di affidabilità devono poter essere letti insieme. La riga di un prodotto dà precedenza al prezzo acquistabile. Costi, classifiche alternative, diagnostica e registro hanno un secondo livello di lettura.

La sorgente dei token è [assets/styles.css](../assets/styles.css). La configurazione Tailwind in [index.html](../index.html) rimanda alle stesse variabili per blu, neutri, stati e font. Usare i componenti accessibili e i formattatori di [assets/ui.js](../assets/ui.js) per nuove sezioni. Il CSS locale impagina la dashboard anche durante il caricamento delle librerie CDN.

## Colori e superfici

I token seguono le forme `--blue-N` e `--neutral-N`. I toni 50–200 definiscono superfici, selezioni e separatori. I toni 600–900 definiscono testo, comandi e la superficie del totale.

| Tono | Blu | Neutro |
| ---: | --- | --- |
| 50 | `#eff5fe` | `#f6f8fb` |
| 100 | `#dbeafa` | `#edf1f5` |
| 200 | `#bdd7f3` | `#dbe2e9` |
| 300 | `#92bce9` | `#c4cfd9` |
| 400 | `#65a0db` | `#8494a4` |
| 500 | `#427ebd` | `#5d6f82` |
| 600 | `#295f99` | `#485c70` |
| 700 | `#1c528a` | `#34485c` |
| 800 | `#193f72` | `#243547` |
| 900 | `#12335d` | `#142539` |

Ogni stato ha uno sfondo `--<stato>-bg` e un primo piano `--<stato>-text`. Un badge associa sempre un'icona e un'etichetta.

| Uso | Testo | Fondo | Rapporto |
| --- | --- | --- | ---: |
| Successo, verificato | `#176348` | `#e3f3eb` | 6,27 |
| Attenzione, parziale o da verificare | `#765015` | `#fff3d8` | 6,51 |
| Errore | `#973b35` | `#fbe9e7` | 5,98 |
| Informazioni e stime | blu 700 | blu 50 | 7,30 |
| Stato neutro | neutro 700 | neutro 100 | 8,31 |
| Totale o bottone primario al passaggio | bianco | blu 900 | 12,67 |
| Dettagli del totale e tooltip del grafico | `--hero-ink` · `#dceafa` | blu 900 | 10,37 |
| Icona e focus sul totale | `--mint` · `#a1e3de` | blu 900 | 8,78 |
| Bottone primario | bianco | blu 800 | 10,52 |

### Matrice del contrasto

I rapporti sono calcolati sui valori sRGB del foglio di stile mediante luminanza relativa. La matrice copre le superfici chiare della pagina, dei pannelli, dei campi, dei controlli selezionati e delle offerte candidate. Ogni valore indica il rapporto rispetto a 1. Le soglie per il testo normale e per gli indicatori grafici essenziali sono rispettivamente 4,5 e 3. [Contrasto del testo](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) · [Contrasto degli elementi grafici](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)

| Primo piano | Bianco | Neutro 50 | Neutro 100 | Blu 50 | Attenzione chiaro |
| --- | ---: | ---: | ---: | ---: | ---: |
| Neutro 900 · titoli e corpo | 15,52 | 14,58 | 13,67 | 14,16 | 14,09 |
| Neutro 800 · comandi | 12,54 | 11,78 | 11,04 | 11,44 | 11,38 |
| Neutro 700 · stato neutro | 9,43 | 8,86 | 8,31 | 8,60 | 8,56 |
| Neutro 600 · testo secondario | 6,91 | 6,49 | 6,09 | 6,30 | 6,27 |
| Neutro 500 · placeholder e valori assenti | 5,17 | 4,86 | 4,56 | 4,72 | 4,69 |
| Blu 700 · link e navigazione | 8,01 | 7,52 | 7,05 | 7,30 | 7,27 |
| Blu 800 · prezzi e selezione | 10,52 | 9,89 | 9,27 | 9,60 | 9,55 |
| Blu 900 · evidenza al passaggio | 12,67 | 11,91 | 11,16 | 11,56 | 11,50 |
| `--focus` · anello `#2165ab` | 5,98 | 5,62 | 5,26 | 5,45 | 5,42 |

Il bordo dei campi neutro 400 ha rapporto 3,11 sul bianco del pannello. Le due serie del grafico usano blu 700 e neutro 600 sul bianco, con tratti rispettivamente continui e tratteggiati. Gli assi usano neutro 600. Il bordo sottile neutro 200 serve per separare righe e superfici.

## Tipografia e numeri

Il font è Inter da Google Fonts con fallback `system-ui, sans-serif`. I valori in rem seguono la dimensione del testo impostata dal lettore. Con la base a 16 px si ottengono i valori riportati. I pesi di riferimento e le interlinee si trovano in `--weight-*` e `--leading-*`.

| Ruolo | Token | Dimensione a 16 px | Interlinea | Peso |
| --- | --- | --- | ---: | --- |
| Totale principale | `--display` | 48–72 px, fluida | 1,12 | 650 |
| Titolo della pagina | `--h1` | 32–46 px, fluida | 1,12 | 750 |
| Titolo di sezione | `--h2` | 22–30 px, fluida | 1,2 | 700 |
| Titolo di dettaglio | `--h3` | 17 px | 1,4 | 650 |
| Corpo | `--body` | 16 px | 1,5 | 400 |
| Tabella, descrizione o comando | `--small` | 14 px | 1,5 | 400 per testo, 600 per comandi |
| Didascalia o badge | `--caption` | 12 px | 1,5 per testo, 1,35 per badge | 400 per testo, 650 per badge |

Tutti i numeri ereditano `font-variant-numeric: tabular-nums`. Usare `money`, `num`, `percent`, `km`, `fmtDate` e `fmtDay` per euro, quantità, variazioni, distanze e date. Il locale è `it-IT` e le date hanno fuso `Europe/Rome`. I dati mancanti restituiscono «n.d.». Una variazione richiede due giorni con una base di confronto valida.

## Griglia e materiali

| Token | Valori e impiego |
| --- | --- |
| `--s1`…`--s8`, `--s10`, `--s12` | 4, 8, 12, 16, 20, 24, 28, 32, 40, 48 px. Usare i passi piccoli nei componenti e quelli grandi tra le sezioni. |
| `--container`, `--gutter` | Contenuto fino a 1184 px. Gutter laterali di 16 px, 28 px da 681 px, 40 px da 1000 px. |
| `--r1`, `--r2`, `--round` | 8 px per campi, tile e note, 16 px per pannelli, 999 px per le pillole. |
| `--shadow-1`, `--shadow-2`, `--shadow-3` | Ombra corta per pannelli, intermedia sui pannelli con focus da tastiera, più ampia per il totale. Le card modalità e le tile usano il solo bordo. |

Fino a 680 px il totale precede i prodotti del carrello. Le tabelle diventano card con intestazione di riga e nomi dei campi, la navigazione scorre nel proprio contenitore e le preferenze chiudono la pagina. Da 681 px il carrello ha due colonne e le modalità quattro. Da 1000 px le preferenze affiancano mercato e sistema. La barra sticky riserva spazio alle ancore tramite l'altezza della testata.

## Componenti e stati

| Componente | Struttura e comportamento |
| --- | --- |
| Card standard, evidenziata, di avviso | `.panel`, `.hero-total`, `.mode-card.chosen` e `.notice.warning`. Il totale domina la pagina. Il confronto indica la soluzione scelta anche con testo. |
| Stat tile | `tile(etichetta, valore, dettaglio)` scrive nell'ordine valore, etichetta, dettaglio. Nello storico il quarto indicatore mostra la variazione con il segno e la data di riferimento. |
| Stato e affidabilità | `pill` associa tono, icona SVG e dicitura italiana. Usare «Verificato», «Parziale», «Non verificato» o «Stima» secondo la provenienza del dato. Per il registro e la scansione il testo identifica lo specifico stato operativo. |
| Bottoni e azioni testuali | `.btn.primary` per salvare, `.btn` per un'azione secondaria, `.section-link` per la navigazione tra sezioni. I link di farmacia usano `link`, icona di apertura e `rel="noopener noreferrer"`. |
| Input, select e segmentato | Il focus ha un anello di 3 px. Il CAP usa tastiera numerica e cinque cifre. I mezzi sono radio con etichetta testuale, icona, stato selezionato e navigazione nativa con le frecce. |
| Tab prodotto e legenda | Tab attiva con contorno e `aria-selected`. Le frecce, Home ed End cambiano prodotto. I bottoni della legenda alternano la visibilità delle serie usando `aria-pressed`. |
| Tabella e dettaglio | `table` produce una caption e intestazioni con `scope`. I valori restano associati all'etichetta nella vista a card. `details` produce un riepilogo attivabile con Invio o Spazio e un indicatore di apertura. |
| Note e tooltip | `note` spiega nel testo un limite del dato. Il tooltip del grafico riporta prezzi, farmacia del minimo e numero di offerte. Gli stessi valori sono leggibili nella tabella con la tastiera. |
| Banner | Il banner fixture rimane nella testata sticky. L'avviso per dati vecchi usa il tempo dalla scansione e il testo precisa la freschezza. Tono verde per meno di 36 ore, attenzione fino a 72 ore escluse, errore da 72 ore. |
| Skeleton, vuoto, errore | `.loading` riserva almeno 152 px e la variante hero più spazio. `aria-busy` accompagna la lettura del JSON. `empty` dice cosa manca e quando riprovare. `failure` identifica il file illeggibile. |

I controlli passano dal default a hover, focus visibile e pressione. I bottoni disabilitati e i campi usano neutro 500 su neutro 100. Un bottone con `aria-busy="true"` mostra l'attesa e il chiamante gestisce la disponibilità dell'azione. Card, tile e badge sono contenuti informativi. L'interazione appartiene al link, al bottone o al riepilogo contenuti al loro interno. I controlli principali hanno area di attivazione di almeno 44 px in altezza. I messaggi di freschezza, di salvataggio e di filtro del registro dispongono di un aggiornamento annunciabile.

## Icone, grafici, movimento e stampa

Le icone di `icon()` condividono una vista 24 × 24, tratti da 1,7 e terminali arrotondati. La misura standard è 20 px, i badge e i link esterni usano 14 px. Le SVG decorative hanno `aria-hidden` e accompagnano etichette testuali.

Lo storico raggruppa le rilevazioni giornaliere del formato selezionato. Il minimo e la media vengono espressi in euro sull'asse da zero. Con zero giorni compare uno stato vuoto, con un giorno la tabella e con due giorni un avviso sull'intervallo breve. Il lettore può aprire la tabella per qualsiasi serie. Se manca Chart.js la tabella rimane disponibile.

I cambi di colore, bordo, ombra e orientamento del chevron usano `--motion` di 170 ms. `prefers-reduced-motion: reduce` disattiva animazioni, transizioni e scorrimento fluido. Chart.js disegna i dati senza animazione. La stampa rimuove la navigazione e i comandi del modulo, mantiene leggibile il totale su bianco e include la tabella dei dati storici.
