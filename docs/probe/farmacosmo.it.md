# Probe JSON-LD: farmacosmo.it

- Data: 2026-11-18T09:30:43Z
- **Compatibilità: FULL**: 4 offerte autentiche (prezzo + disponibilità + EAN/Minsan coincidente)
- Richieste effettuate: 13 (tetto 25)
- Sitemap usate: https://www.farmacosmo.it/IT-sitemap-index.xml
- Pattern di ricerca: nessuno
- Prodotti trovati: P001, P002, P003, P004
- Prodotti non trovati: P005

Le offerte qui sotto NON sono verificate e NON entrano nel carrello finché il dominio non viene promosso a mano
(`python pharmacy_discovery.py promote <dominio>`). Controlla gli esempi aprendo gli URL.

## Esito per prodotto

| Prodotto | Esito | Prezzo | Disponibilità | Match | URL |
|---|---|---|---|---|---|
| P001 Uriage Xémose Olio Detergente 1000 ml | OK | 17,40 € | DISPONIBILE | ID | https://www.farmacosmo.it/cura-e-bellezza-corpo/xemose-olio-detergente-lenitivo-1-lt-085554/ |
| P002 La Roche-Posay Lipikar Baume AP+M 400 ml | OK | 25,08 € | NON DISPONIBILE | ID | https://www.farmacosmo.it/cura-e-bellezza-corpo/lipikar-baume-ap-m-balsamo-corpo-riparatore-400-ml-121460/ |
| P003 Ceramol 311 Lipocrema 100 ml | OK | 18,96 € | DISPONIBILE | ID | https://www.farmacosmo.it/farmaci-e-articoli-sanitari/ceramol-lipocrema-311-trattamento-localizzato-eczemi-e-dermatite-atopica-100-ml-147461/ |
| P004 Ceramol Crema Mani 311 100 ml | OK | 12,15 € | DISPONIBILE | ID | https://www.farmacosmo.it/cura-e-bellezza-corpo/ceramol-crema-mani-311-ad-azione-protettiva-100-ml-122141/ |
| P005 La Roche-Posay Anthelios UVMune 400 Invisible Fluid SPF50+ 50 ml | MATCH NON AFFIDABILE | — | NON DISPONIBILE | ID_DIVERSO | https://www.farmacosmo.it/solari/anthelios-uvmune-400-ultimate-protection-ultra-longs-uva-spf50-fluido-solare-50-ml-185457/ |

## Dettagli

### P001
- motivo match: identificativo coincidente: EAN 3661434003370, Minsan 927291005
- fonte prezzo: json_ld+meta

### P002
- motivo match: identificativo coincidente: EAN 3337875696548, Minsan 979265434
- fonte prezzo: json_ld+meta
- scartato: https://www.farmacosmo.it/outlet/lipikar-baume-ap-m-balsamo-corpo-riparatore-400-ml-erogatore-danneggiato-199411/ (MARCA_DIVERSA: marca della pagina 'L&#039;Oreal Paris' non compatibile)

### P003
- motivo match: identificativo coincidente: EAN 8029408107014, Minsan 980512762
- fonte prezzo: json_ld+meta

### P004
- motivo match: identificativo coincidente: EAN 8029408093058, Minsan 977658020
- fonte prezzo: json_ld+meta

### P005
- motivo match: identificativi della pagina diversi dal catalogo (EAN ['3337875847292'], Minsan [])
- scartato: https://www.farmacosmo.it/cura-e-igiene-viso/lancome-renergie-hpn-uvmune-cream-spf50-trattamento-viso-anti-eta-75-ml-230524/ (ID_DIVERSO: identificativi della pagina diversi dal catalogo (EAN ['3614274420357'], Minsan []))

## Percorsi richiesti (tutti verificati su robots.txt)

- `/IT-sitemap-index.xml`
- `/IT-sitemap-product1.xml`
- `/IT-sitemap-product2.xml`
- `/IT-sitemap-product3.xml`
- `/cura-e-bellezza-corpo/ceramol-crema-mani-311-ad-azione-protettiva-100-ml-122141/`
- `/cura-e-bellezza-corpo/lipikar-baume-ap-m-balsamo-corpo-riparatore-400-ml-121460/`
- `/cura-e-bellezza-corpo/xemose-olio-detergente-lenitivo-1-lt-085554/`
- `/cura-e-igiene-viso/lancome-renergie-hpn-uvmune-cream-spf50-trattamento-viso-anti-eta-75-ml-230524/`
- `/farmaci-e-articoli-sanitari/ceramol-lipocrema-311-trattamento-localizzato-eczemi-e-dermatite-atopica-100-ml-147461/`
- `/outlet/lipikar-baume-ap-m-balsamo-corpo-riparatore-400-ml-erogatore-danneggiato-199411/`
- `/solari/anthelios-uvmune-400-ultimate-protection-ultra-longs-uva-spf50-fluido-solare-50-ml-185457/`
