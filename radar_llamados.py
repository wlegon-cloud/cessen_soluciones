#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Radar de llamados - Essen
Baja el RSS de Compras Estatales, entra al detalle de cada llamado, lee los
"Ítems del llamado" y filtra por palabras clave (en el objeto y en los ítems).
Genera llamados_essen.html y lo abre en el navegador.

Uso en tu PC:      python radar_llamados.py
Uso en un servidor: python radar_llamados.py --salida site/index.html --no-abrir
No necesita instalar nada: solo Python 3.8 o superior.
"""
import argparse
import concurrent.futures as cf
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
import webbrowser
from datetime import datetime

# ----------------------------------------------------------------------------
# CONFIGURACIÓN: editá estas listas a gusto.
# Coinciden por comienzo de palabra y sin tildes: "estante" encuentra
# "estantes" y "estanterías".
# ----------------------------------------------------------------------------
PALABRAS = {
    "Almacenamiento": ["estante", "rack", "locker", "casillero", "guardarropa",
                       "almacenaje", "entrepiso", "góndola"],
    "Puertas cortafuego": ["cortafuego", "corta fuego", "puerta", "antipánico",
                           "armario ignífugo"],
    "Papeleras y contenedores": ["contenedor", "papelera", "cesto", "recipiente",
                                 "tacho", "cortopunzante", "descartador",
                                 "residuo", "reciclaje"],
    "Compactadoras y trituradoras": ["compactadora", "prensa", "enfardadora",
                                     "trituradora"],
    "Ruedas": ["rueda", "ruedita"],
    "Carros y transportadores": ["carro", "carrito", "transpaleta",
                                 "zorra hidráulica", "apilador",
                                 "mesa elevadora", "carretilla"],
    "Pallets, antiderrames y pisos": ["pallet", "palet", "tarima", "antiderrame",
                                      "derrame", "piso plástico", "baldosa"],
    "Cajones, gavetas y bins": ["cajón", "gaveta", "bins", "caja plástica",
                                "canasto"],
    "Gomas y patas regulables": ["pata regulable", "goma"],
}
# Obras o servicios donde Essen puede ser proveedor del contratista.
PALABRAS_RELACIONADAS = {
    "Obras y servicios relacionados": ["incendio", "evacuación", "bomberos",
                                       "DNB", "mobiliario", "equipamiento"],
}

RSS_URL = "https://www.comprasestatales.gub.uy/consultas/rss"
HILOS = 6            # descargas en paralelo
ESPERA = 20          # segundos de timeout por página
CARPETA = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(CARPETA, "cache_llamados.json")
SALIDA = os.path.join(CARPETA, "llamados_essen.html")
UA = {"User-Agent": "Mozilla/5.0 (radar-llamados; uso interno)"}


# ----------------------------------------------------------------------------
def bajar(url, intentos=3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=ESPERA) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(2 * (i + 1))


def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def a_texto(fragmento):
    """HTML -> texto, una línea por bloque."""
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragmento)
    t = re.sub(r"(?s)<[^>]+>", "\n", t)
    t = html.unescape(t).replace("\xa0", " ")
    lineas = [re.sub(r"[ \t]+", " ", l).strip() for l in t.split("\n")]
    return "\n".join(l for l in lineas if l)


def etiqueta(bloque, nombre):
    m = re.search(r"(?is)<%s(?:\s[^>]*)?>(.*?)</%s>" % (nombre, nombre), bloque)
    if not m:
        return ""
    v = m.group(1).strip()
    v = re.sub(r"^<!\[CDATA\[", "", v)
    v = re.sub(r"\]\]>$", "", v)
    return v.strip()


def fecha(m):
    if not m:
        return None
    d, mes, a, h, mi = m.groups()
    return datetime(int(a), int(mes), int(d), int(h or 0), int(mi or 0))


RE_FECHA = r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2}))?"


def leer_rss(xml):
    llamados = []
    for m in re.finditer(r"(?is)<item\b[^>]*>(.*?)</item>", xml):
        b = m.group(1)
        titulo = re.sub(r"\s+", " ", html.unescape(etiqueta(b, "title"))).strip()
        crudo = etiqueta(b, "description")
        desc = html.unescape(re.sub(r"(?i)<br\s*/?>", "\n", crudo)).replace("\xa0", " ")
        link = html.unescape(etiqueta(b, "link") or etiqueta(b, "guid")).strip()
        t = re.match(r"^(.*?)\s+(\S+/\d{4})\s+-\s+(.*)$", titulo)
        org = t.group(3) if t else ""
        partes = org.split(" | ")
        if len(partes) == 2 and partes[0].strip() == partes[1].strip():
            org = partes[0].strip()
        corte = re.search(r"(?i)Recepci[oó]n de ofertas hasta:", desc)
        objeto = (desc[:corte.start()] if corte else desc).strip()
        objeto = unicodedata.normalize("NFC", re.sub(r"[ \t]+", " ", objeto))
        llamados.append({
            "id": link.rstrip("/").split("/")[-1],
            "link": link,
            "tipo": t.group(1) if t else titulo,
            "num": t.group(2) if t else "",
            "org": org,
            "objeto": objeto,
            "cierre": fecha(re.search(r"(?i)hasta:\s*" + RE_FECHA, desc)),
            "publicado": fecha(re.search(r"(?i)Publicado:\s*" + RE_FECHA, desc)),
            "huella": hashlib.md5(crudo.encode("utf-8")).hexdigest(),
        })
    return llamados


RE_ITEM = re.compile(
    r"(?is)[ÍI]tem\s+N[º°o]\.?\s*(\d+)\s*(.*?)\(\s*C[oó]d\.?\s*Art[ií]culo\s*(\d+)\s*\)"
    r"(?:\s*Cantidad:\s*([^\n]+))?")


def leer_items(pagina):
    """Devuelve la lista de ítems de una página de detalle."""
    i = pagina.find("tems del llamado")
    texto = a_texto(pagina[i:] if i >= 0 else pagina)
    items = []
    for m in RE_ITEM.finditer(texto):
        items.append({
            "n": m.group(1),
            "desc": unicodedata.normalize("NFC", re.sub(r"\s+", " ", m.group(2)).strip()),
            "cod": m.group(3),
            "cant": (m.group(4) or "").strip(),
        })
    return items


def paginas_extra(pagina, id_):
    """Links a otras páginas de ítems del mismo llamado (si el sitio pagina)."""
    urls = set()
    for href in re.findall(r'href="([^"]+)"', pagina):
        href = html.unescape(href)
        if id_ in href and re.search(r"(?i)pag", href) and "login" not in href:
            if href.startswith("/"):
                href = "https://www.comprasestatales.gub.uy" + href
            urls.add(href)
    return sorted(urls)


def detalle(llamado):
    pagina = bajar(llamado["link"])
    items = leer_items(pagina)
    vistos = {(i["n"], i["cod"], i["desc"]) for i in items}
    for url in paginas_extra(pagina, llamado["id"])[:40]:
        try:
            for it in leer_items(bajar(url)):
                k = (it["n"], it["cod"], it["desc"])
                if k not in vistos:
                    vistos.add(k)
                    items.append(it)
        except Exception:
            pass
    return items


# ----------------------------------------------------------------------------
def compilar(grupos):
    out = []
    for cat, palabras in grupos.items():
        for p in palabras:
            patron = r"(?:^|[^a-z0-9])(" + r"\s+".join(map(re.escape, norm(p).split())) + ")"
            out.append((cat, re.compile(patron)))
    return out


def marcar(texto, patrones):
    """Devuelve (html con <mark>, categorías que coinciden)."""
    n = norm(texto)
    if len(n) != len(texto):          # caso raro: no resaltar, solo detectar
        cats = {c for c, r in patrones if r.search(n)}
        return html.escape(texto), cats
    rangos, cats = [], set()
    for cat, r in patrones:
        for m in r.finditer(n):
            rangos.append(m.span(1))
            cats.add(cat)
    if not rangos:
        return html.escape(texto), cats
    rangos.sort()
    out, pos = [], 0
    for a, b in rangos:
        if b <= pos:
            continue
        a = max(a, pos)
        out.append(html.escape(texto[pos:a]) + "<mark>" + html.escape(texto[a:b]) + "</mark>")
        pos = b
    out.append(html.escape(texto[pos:]))
    return "".join(out), cats


def f(d, hora=True):
    if not d:
        return "sin fecha"
    return d.strftime("%d/%m/%Y %H:%M" if hora else "%d/%m/%Y")


ESTILO = """
body{font-family:Segoe UI,system-ui,sans-serif;background:#f1f4f6;color:#15202b;margin:0;padding:24px;line-height:1.45}
.w{max-width:980px;margin:0 auto}h1{margin:0 0 4px}h2{margin:28px 0 10px;font-size:1.15rem}
.sub{color:#586674;margin:0 0 8px}.c{background:#fff;border:1px solid #d5dce2;border-radius:8px;padding:12px 14px;margin:8px 0}
.top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.id{font-weight:600}
.org{color:#586674;font-size:.9rem}.obj{margin:8px 0;white-space:pre-line}
mark{background:#ffe27a;border-radius:2px;padding:0 1px}
ul{margin:6px 0 8px;padding-left:20px}li{margin:2px 0}.cant{color:#586674;font-size:.88rem}
.tag{display:inline-block;background:#e6ecf0;border-radius:4px;padding:1px 7px;font-size:.8rem;margin-right:5px}
.pill{border:1px solid;border-radius:99px;padding:0 8px;font-size:.8rem;font-weight:600;margin-left:6px}
.ok{color:#1f7a4d}.warn{color:#9a5b00}.crit{color:#b3261e}a{color:#0b5c7a;font-weight:600}
.pie{font-size:.88rem;color:#586674}.err{color:#b3261e}
"""


def tarjeta(l, patrones):
    obj_html, cats = marcar(l["objeto"], patrones)
    filas, n_items = [], len(l["items"])
    for it in l["items"]:
        h, c = marcar(it["desc"], patrones)
        if c:
            cats |= c
            filas.append("<li>Ítem %s: %s <span class='cant'>%s · cód. %s</span></li>"
                         % (html.escape(it["n"]), h, html.escape(it["cant"]), html.escape(it["cod"])))
    if not cats:
        return None, cats
    pill = ""
    if l["cierre"]:
        dias = (l["cierre"] - datetime.now()).total_seconds() / 86400
        if dias < 0:
            pill = "<span class='pill crit'>cerrado</span>"
        else:
            cls = "crit" if dias <= 3 else "warn" if dias <= 7 else "ok"
            pill = "<span class='pill %s'>faltan %d días</span>" % (cls, int(dias) + 1)
    lista = ("<ul>%s</ul>" % "".join(filas)) if filas else ""
    aviso = " <span class='err'>(no se pudo leer el detalle)</span>" if l.get("error") else ""
    h = ("<div class='c'><div class='top'><div><div class='id'>%s %s</div><div class='org'>%s</div></div>"
         "<div>Cierra %s%s</div></div><div class='obj'>%s</div>%s"
         "<div class='pie'>%s %d de %d ítems coinciden · Publicado %s%s · <a href='%s' target='_blank'>Ver llamado</a></div></div>"
         % (html.escape(l["tipo"]), html.escape(l["num"]), html.escape(l["org"]), f(l["cierre"]), pill,
            obj_html, lista, "".join("<span class='tag'>%s</span>" % html.escape(c) for c in sorted(cats)),
            len(filas), n_items, f(l["publicado"], False), aviso, html.escape(l["link"])))
    return h, cats


def informe(llamados):
    directos = compilar(PALABRAS)
    todos = directos + compilar(PALABRAS_RELACIONADAS)
    lejos = datetime(9999, 1, 1)
    orden = sorted(llamados, key=lambda l: l["cierre"] or lejos)
    sec_d, sec_r = [], []
    nombres_d = set(PALABRAS)
    for l in orden:
        h, cats = tarjeta(l, todos)
        if not h:
            continue
        (sec_d if cats & nombres_d else sec_r).append(h)
    fallas = sum(1 for l in llamados if l.get("error"))
    partes = [
        "<!doctype html><meta charset='utf-8'><meta name='robots' content='noindex,nofollow'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Llamados Essen</title><style>%s</style><div class='w'>" % ESTILO,
        "<h1>Llamados para Essen</h1>",
        "<p class='sub'>Generado el %s · %d llamados vigentes revisados (objeto e ítems) · %d de producto directo · %d de obras relacionadas%s</p>"
        % (f(datetime.now()), len(llamados), len(sec_d), len(sec_r),
           (" · <span class='err'>%d detalles no se pudieron leer</span>" % fallas) if fallas else ""),
        "<h2>Producto directo (%d)</h2>" % len(sec_d),
        "".join(sec_d) or "<p class='sub'>Ninguno hoy.</p>",
        "<h2>Obras y servicios relacionados (%d)</h2>" % len(sec_r),
        "".join(sec_r) or "<p class='sub'>Ninguno hoy.</p>",
        "</div>",
    ]
    return "".join(partes), len(sec_d), len(sec_r)


# ----------------------------------------------------------------------------
def main():
    global SALIDA, CACHE
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=SALIDA, help="archivo HTML a generar")
    ap.add_argument("--cache", default=CACHE, help="archivo de caché")
    ap.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    args = ap.parse_args()
    SALIDA, CACHE = os.path.abspath(args.salida), os.path.abspath(args.cache)
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    print("Bajando el RSS...")
    llamados = leer_rss(bajar(RSS_URL))
    if not llamados:
        sys.exit("El RSS vino vacío o cambió de formato.")
    try:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}

    pendientes = []
    for l in llamados:
        c = cache.get(l["id"])
        if c and c.get("huella") == l["huella"]:
            l["items"] = c["items"]
        else:
            pendientes.append(l)
    print("%d llamados vigentes. %d nuevos o modificados para leer." % (len(llamados), len(pendientes)))

    hechos = 0
    with cf.ThreadPoolExecutor(HILOS) as pool:
        futuros = {pool.submit(detalle, l): l for l in pendientes}
        for fut in cf.as_completed(futuros):
            l = futuros[fut]
            try:
                l["items"] = fut.result()
                cache[l["id"]] = {"huella": l["huella"], "items": l["items"]}
            except Exception:
                l["items"], l["error"] = [], True
            hechos += 1
            if hechos % 25 == 0 or hechos == len(pendientes):
                print("  %d / %d" % (hechos, len(pendientes)))

    vigentes = {l["id"] for l in llamados}
    cache = {k: v for k, v in cache.items() if k in vigentes}
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False)

    pagina, nd, nr = informe(llamados)
    with open(SALIDA, "w", encoding="utf-8") as fh:
        fh.write(pagina)
    sin_items = sum(1 for l in llamados if not l["items"] and not l.get("error"))
    print("Listo: %d de producto directo, %d de obras relacionadas." % (nd, nr))
    print("%d llamados no tienen ítems listados en su página." % sin_items)
    print("Informe: " + SALIDA)
    fallas = sum(1 for l in llamados if l.get("error"))
    if pendientes and fallas > len(pendientes) * 0.5:
        sys.exit("ERROR: no se pudo leer el detalle de %d de %d llamados. "
                 "Puede que el sitio esté bloqueando a este servidor." % (fallas, len(pendientes)))
    if not args.no_abrir:
        webbrowser.open("file://" + SALIDA.replace(os.sep, "/"))


if __name__ == "__main__":
    main()
