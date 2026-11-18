"""Rigenera le fixture SINTETICHE dello scraper generico (dati simulati, solo per i test).

Uso: python tests/fixtures/jsonld/_genera_fixture.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent
HEAD = "<!doctype html>\n<!-- DATI SIMULATI PER TEST (scraper generico JSON-LD): prezzi e pagine inventati. -->\n"


def page(title, ld_blocks, body="", extra_head=""):
    lds = "\n".join(f'<script type="application/ld+json">{b}</script>' for b in ld_blocks)
    return (f'{HEAD}<html lang="it"><head><title>{title}</title>{extra_head}\n{lds}\n</head>'
            f"<body><main><h1>{title}</h1>{body}</main></body></html>\n")


FILES = {
    "simple_offer.html": page("Ceramol Crema Mani 311 100 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Ceramol Crema Mani 311 100 ml","gtin13":"8029408093058","sku":"A-77","brand":{"@type":"Brand","name":"Ceramol"},
 "offers":{"@type":"Offer","price":"12,90","priceCurrency":"EUR","availability":"https://schema.org/InStock","url":"https://www.farmatest.it/ceramol-crema-mani-311-100-ml",
  "shippingDetails":{"@type":"OfferShippingDetails","shippingRate":{"@type":"MonetaryAmount","value":"4.90","currency":"EUR"},
   "shippingDestination":{"@type":"DefinedRegion","addressCountry":"IT"}}}}"""]),

    "graph_itemlist.html": page("Uriage Xémose Olio Detergente 1 L", ["""
{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","name":"Xemose"},
 {"@type":"ItemList","name":"Correlati","itemListElement":[
   {"@type":"ListItem","position":1,"item":{"@type":"Product","name":"Uriage Xémose Olio Detergente 500 ml","gtin13":"3661434005923","url":"https://www.farmatest.it/xemose-500","offers":{"@type":"Offer","price":"11.50","priceCurrency":"EUR"}}}]},
 {"@type":"Product","name":"Uriage Xémose Olio Detergente 1 L","gtin13":"3661434003370",
  "offers":{"@type":"Offer","price":16.99,"priceCurrency":"EUR","availability":"http://schema.org/InStock"}}]}"""]),

    "aggregate_single.html": page("La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+ 50 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+ 50 ml","gtin13":"3337875797597",
 "offers":{"@type":"AggregateOffer","lowPrice":"13.49","highPrice":"13.49","offerCount":1,"priceCurrency":"EUR","availability":"https://schema.org/InStock"}}"""]),

    "aggregate_range.html": page("La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+", ["""
{"@context":"https://schema.org","@type":"Product","name":"La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+","gtin13":"3337875797597",
 "offers":{"@type":"AggregateOffer","lowPrice":"13.49","highPrice":"24.90","offerCount":3,"priceCurrency":"EUR"}}"""],
        extra_head='<meta property="product:price:amount" content="13.49">'),

    "product_group.html": page("Ceramol Lipocrema 311 50 ml", ["""
{"@context":"https://schema.org","@type":"ProductGroup","name":"Ceramol Lipocrema 311","brand":{"@type":"Brand","name":"Ceramol"},
 "hasVariant":[
  {"@type":"Product","name":"Ceramol Lipocrema 311 50 ml","gtin13":"8029408107021","offers":{"@type":"Offer","price":"11.28","priceCurrency":"EUR","availability":"https://schema.org/InStock"}},
  {"@type":"Product","name":"Ceramol Lipocrema 311 100 ml","gtin13":"8029408107014","offers":{"@type":"Offer","price":"16.10","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}]}"""],
        extra_head='<meta property="product:price:amount" content="11.28">'),

    "product_group_no_ean.html": page("Ceramol Lipocrema 311", ["""
{"@context":"https://schema.org","@type":"Product","name":"Ceramol Lipocrema 311","brand":"Ceramol",
 "offers":[{"@type":"Offer","name":"50 ml","sku":"L50","price":"11.28","priceCurrency":"EUR","availability":"https://schema.org/InStock"},
           {"@type":"Offer","name":"100 ml","sku":"L100","price":"16.10","priceCurrency":"EUR","availability":"https://schema.org/OutOfStock"}]}"""]),

    "price_specification.html": page("Ceramol Lipocrema 311 100 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Ceramol Lipocrema 311 100 ml","gtin13":"8029408107014",
 "offers":{"@type":"Offer","priceCurrency":"EUR","availability":"https://schema.org/InStock",
  "priceSpecification":{"@type":"PriceSpecification","price":"18,73","priceCurrency":"EUR"}}}"""]),

    "non_eur.html": page("Ceramol Crema Mani 311 100 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Ceramol Crema Mani 311 100 ml","gtin13":"8029408093058",
 "offers":{"@type":"Offer","price":"14.90","priceCurrency":"CHF","availability":"https://schema.org/InStock"}}"""]),

    "malformed.html": page("Ceramol Crema Mani 311 100 ml", [
        "﻿// blocco con commenti, virgole finali ed entità HTML\n"
        "{&quot;@context&quot;:&quot;https://schema.org&quot;, /* commento */ &quot;@type&quot;:&quot;Product&quot;,"
        "&quot;name&quot;:&quot;Ceramol Crema Mani 311 100 ml&quot;,&quot;gtin13&quot;:&quot;8029408093058&quot;,"
        "&quot;offers&quot;:{&quot;@type&quot;:&quot;Offer&quot;,&quot;price&quot;:&quot;11.79&quot;,&quot;priceCurrency&quot;:&quot;EUR&quot;,"
        "&quot;url&quot;:&quot;https://www.farmatest.it/a//b&quot;,&quot;availability&quot;:&quot;https://schema.org/InStock&quot;,},}",
        '{ "@type": "Product", "name": ']),

    "ean_mismatch.html": page("Ceramol Crema Mani 311 100 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Ceramol Crema Mani 311 100 ml","gtin13":"8029408107014",
 "offers":{"@type":"Offer","price":"9.99","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}"""]),

    "multipack.html": page("Ceramol Crema Mani 311 Duo Pack 2 x 100 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Ceramol Crema Mani 311 Duo Pack 2 x 100 ml","gtin13":"8029408093058",
 "offers":{"@type":"Offer","price":"20.90","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}"""]),

    "perfumed.html": page("Anthelios UVMune 400 Fluido Invisibile SPF50+ Con Profumo 50 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Anthelios UVMune 400 Fluido Invisibile SPF50+ Con Profumo 50 ml","brand":"La Roche-Posay",
 "offers":{"@type":"Offer","price":"15.90","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}"""]),

    "unperfumed.html": page("Anthelios UVMune 400 Fluido Invisibile SPF50+ Senza Profumo 50 ml", ["""
{"@context":"https://schema.org","@type":"Product","name":"Anthelios UVMune 400 Fluido Invisibile SPF50+ Senza Profumo 50 ml","brand":"La Roche-Posay",
 "offers":{"@type":"Offer","price":"15.90","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}"""]),

    "out_of_stock.html": page("Uriage Xémose Olio Detergente 1 L", ["""
{"@context":"https://schema.org","@type":"Product","name":"Uriage Xémose Olio Detergente 1 L","gtin13":"3661434003370",
 "offers":{"@type":"Offer","price":"17.20","priceCurrency":"EUR","availability":"https://schema.org/OutOfStock"}}"""]),

    "no_structured.html": (f"{HEAD}<html><head><title>Lipikar Baume AP+M 400 ml</title></head><body>"
                           "<h1>La Roche-Posay Lipikar Baume AP+M 400 ml</h1><div class=\"price\">€ 22,90</div>"
                           "<button>Aggiungi al carrello</button></body></html>\n"),

    "home_opensearch.html": (f"{HEAD}<html><head><title>Farmacia Test</title>"
                             '<link rel="search" type="application/opensearchdescription+xml" href="/opensearch.xml" title="Cerca">'
                             '</head><body><a href="/chi-siamo">Chi siamo</a></body></html>\n'),

    "search_results.html": (f"{HEAD}<html><head><title>Risultati</title></head><body>"
                            '<a href="/categoria/viso">Viso</a>'
                            '<a href="/p/123?q=ceramol" title="Ceramol 311 Crema Mani 100 ml">Ceramol 311 Crema Mani 100 ml</a>'
                            '<a href="/p/124">Ceramol Lipocrema 311 50 ml</a>'
                            '<a href="/p/125">Ceramol Lipocrema 311 100 ml</a>'
                            '<a href="https://altro-sito.it/p/9">Ceramol Lipocrema 311 100 ml (altro sito)</a>'
                            '<a href="/p/126">Crema solare generica</a>'
                            "</body></html>\n"),
}

SITEMAP_URLS = [
    "ceramol-crema-mani-311-100-ml", "ceramol-lipocrema-311-gruppo", "ceramol-lipocrema-311-50-ml",
    "uriage-xemose-olio-detergente-1-l", "uriage-xemose-olio-detergente-500-ml", "lipikar-baume-ap-m-400-ml",
    "anthelios-uvmune-400-fluido-invisibile-50-ml", "cerotti-cicatrizzanti", "privato/ceramol-crema-mani-311-100-ml-riservato",
]


def main():
    for name, content in FILES.items():
        (OUT / name).write_text(content, encoding="utf-8")
    (OUT / "opensearch.xml").write_text(
        '<?xml version="1.0"?>\n<!-- DATI SIMULATI PER TEST -->\n<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        '<ShortName>Test</ShortName><Url type="text/html" '
        'template="https://www.farmatest.it/cerca?lang={language?}&amp;testo={searchTerms}&amp;pagina={startPage?}"/>'
        "</OpenSearchDescription>\n", encoding="utf-8")
    (OUT / "sitemap_index.xml").write_text(
        '<?xml version="1.0"?>\n<!-- DATI SIMULATI PER TEST -->\n<sitemapindex>'
        "<sitemap><loc>https://www.farmatest.it/sitemap_blog.xml</loc></sitemap>"
        "<sitemap><loc>https://www.farmatest.it/sitemap_products.xml</loc></sitemap></sitemapindex>\n", encoding="utf-8")
    (OUT / "sitemap_products.xml").write_text(
        '<?xml version="1.0"?>\n<!-- DATI SIMULATI PER TEST: URL misti -->\n<urlset>'
        + "".join(f"<url><loc>https://www.farmatest.it/{u}</loc></url>" for u in SITEMAP_URLS)
        + "</urlset>\n", encoding="utf-8")


if __name__ == "__main__":
    main()
