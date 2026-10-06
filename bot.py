"""
Bot de ofertas de Mercado Livre -> Telegram

Modos:
  python bot.py ofertas   -> busca ofertas NUEVAS y te las manda por privado
  python bot.py publicar  -> publica en tu canal las ofertas a las que
                             respondiste con tu enlace meli.la (con pausa
                             entre una y otra)
"""
import html
import json
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

# ---------- AJUSTES (puedes cambiarlos) ----------
DESCUENTO_MINIMO = 20            # solo ofertas con este % de rebaja o mas
MAX_OFERTAS = 5                  # maximo de ofertas NUEVAS por busqueda
PAUSA_ENTRE_PUBLICACIONES = 120  # segundos entre una publicacion y otra
RECORDAR = 1000                  # cuantas ofertas recuerda para no repetir
JITTER_MAX = 600                 # espera al azar (0 a 10 min) antes de buscar
ESPERAS_BLOQUEO = [60, 120, 240, 360]  # minutos de pausa si ML falla/bloquea
ARCHIVO_ESTADO = "estado.json"
URL_OFERTAS = "https://www.mercadolivre.com.br/ofertas"
TEXTO_COMPRA = "🛒 Compre aqui:"  # frase antes de tu enlace en el canal
# -------------------------------------------------

TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER = int(os.environ["OWNER_CHAT_ID"])
CANAL = os.environ["CHANNEL_ID"]
FORZAR = os.environ.get("FORZAR") == "true"  # ejecucion manual: sin pausas
API = f"https://api.telegram.org/bot{TOKEN}"
AGENTES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def cabeceras():
    return {
        "User-Agent": random.choice(AGENTES),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
        "Upgrade-Insecure-Requests": "1",
    }


# ---------- Telegram ----------
def tg(metodo, **params):
    r = requests.post(f"{API}/{metodo}", data=params, timeout=30)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "description": r.text[:200]}


def brl(v):
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


# ---------- Memoria (para no repetir ofertas) ----------
def cargar_estado():
    try:
        with open(ARCHIVO_ESTADO, encoding="utf-8") as f:
            e = json.load(f)
    except (OSError, ValueError):
        e = {}
    e.setdefault("vistos", [])
    e.setdefault("fallo", False)
    e.setdefault("fallos", 0)
    e.setdefault("pausa_hasta", 0)
    return e


def guardar_estado(e):
    e["vistos"] = e["vistos"][-RECORDAR:]
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, indent=1)


# ---------- Leer ofertas de Mercado Livre ----------
def valor(frac):
    """Convierte el elemento de precio (fraccion + centavos) en numero."""
    if frac is None:
        return None
    digitos = re.sub(r"\D", "", frac.get_text())
    if not digitos:
        return None
    v = float(digitos)
    cont = frac.find_parent(class_="andes-money-amount")
    if cont:
        c = cont.select_one(".andes-money-amount__cents")
        if c:
            cd = re.sub(r"\D", "", c.get_text())
            if cd:
                v += int(cd) / 100
    return v


def parsear(card):
    enlaces = card.select("a[href]")
    enlace = card.select_one(
        "a.poly-component__title, a.promotion-item__link-container"
    ) or (max(enlaces, key=lambda a: len(a.get_text(strip=True)))
          if enlaces else None)
    if not enlace or not enlace.get("href"):
        return None

    t_el = card.select_one(".poly-component__title, .promotion-item__title")
    titulo = (t_el or enlace).get_text(" ", strip=True)
    if not titulo:
        img_t = card.select_one("img[alt]")
        titulo = img_t.get("alt", "").strip() if img_t else ""
    if not titulo:
        return None

    previo = valor(card.select_one(
        ".andes-money-amount--previous .andes-money-amount__fraction, "
        ".promotion-item__oldprice .andes-money-amount__fraction, "
        "s .andes-money-amount__fraction"
    ))
    actual_el = card.select_one(
        ".poly-price__current .andes-money-amount__fraction, "
        ".promotion-item__price .andes-money-amount__fraction"
    )
    if actual_el is None:
        fr = card.select(".andes-money-amount__fraction")
        actual_el = fr[1] if previo and len(fr) > 1 else (fr[0] if fr else None)
    actual = valor(actual_el)
    if actual is None:
        return None

    desc = None
    d_el = card.select_one(
        ".andes-money-amount__discount, .poly-price__disc_label, "
        ".promotion-item__discount-text"
    )
    if d_el:
        m = re.search(r"(\d+)\s*%", d_el.get_text())
        if m:
            desc = int(m.group(1))
    if desc is None and previo and previo > actual:
        desc = round((1 - actual / previo) * 100)
    if not desc:
        return None

    url = urljoin("https://www.mercadolivre.com.br", enlace["href"])
    p = urlsplit(url)
    if "click" not in p.netloc:  # quita parametros de seguimiento
        url = urlunsplit((p.scheme, p.netloc, p.path, "", ""))

    img = None
    img_el = card.select_one("img")
    if img_el:
        src = img_el.get("data-src") or img_el.get("src") or ""
        if src.startswith("http"):
            img = src

    return {"titulo": titulo[:200], "previo": previo, "actual": actual,
            "desc": desc, "url": url, "img": img}


def tarjetas_genericas(soup):
    """Plan B: encuentra productos por sus descuentos, sin depender del
    nombre exacto de las clases de la tarjeta."""
    vistas, tarjetas = set(), []
    for d in soup.select(".andes-money-amount__discount, .poly-price__disc_label"):
        nodo = d
        for _ in range(8):
            nodo = nodo.parent
            if nodo is None:
                break
            if nodo.select_one("a[href]") and nodo.select(
                    ".andes-money-amount__fraction"):
                if id(nodo) not in vistas:
                    vistas.add(id(nodo))
                    tarjetas.append(nodo)
                break
    return tarjetas


def obtener_ofertas():
    r = requests.get(URL_OFERTAS, headers=cabeceras(), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(
            f"Mercado Livre respondio con codigo {r.status_code} ({r.url})")
    soup = BeautifulSoup(r.text, "html.parser")
    candidatos = [
        soup.select("div.poly-card"),
        soup.select("li.promotion-item, div.promotion-item"),
        tarjetas_genericas(soup),
    ]
    vistos, ofertas = set(), []
    for cards in candidatos:
        for c in cards:
            o = parsear(c)
            if o and o["url"] not in vistos:
                vistos.add(o["url"])
                ofertas.append(o)
        if ofertas:
            break
    if not ofertas:
        titulo = soup.title.get_text(strip=True) if soup.title else "?"
        bajo = r.text.lower()
        pista = " | posible captcha/bloqueo" if (
            "captcha" in bajo or "robot" in bajo or "verifica" in bajo) else ""
        raise RuntimeError(
            f"pagina sin ofertas legibles | titulo: {titulo[:80]} | "
            f"url: {r.url[:100]} | bytes: {len(r.text)} | "
            f"poly-card: {len(candidatos[0])} | "
            f"precios: {len(soup.select('.andes-money-amount__fraction'))} | "
            f"descuentos: {len(soup.select('.andes-money-amount__discount'))}"
            f"{pista}")
    return ofertas


# ---------- Mensajes ----------
def texto_privado(o):
    previo = f"De {brl(o['previo'])} " if o["previo"] else ""
    return (
        f"🔥 {o['titulo']}\n\n"
        f"💰 {previo}por {brl(o['actual'])}\n"
        f"📉 {o['desc']}% OFF\n\n"
        f"🔗 Original: {o['url']}\n"
        f"↩️ Responde a este mensaje con tu enlace meli.la para publicarla"
    )


def texto_canal(original, link):
    lineas = [l for l in original.splitlines() if not l.startswith(("🔗", "↩"))]
    base = html.escape("\n".join(lineas).strip())
    # precio anterior tachado y precio actual en negrita
    base = re.sub(r"De (R\$ [\d.,]+) por (R\$ [\d.,]+)",
                  r"De <s>\1</s> por <b>\2</b>", base)
    return f"{base}\n\n{TEXTO_COMPRA} {link}"


# ---------- Modo 1: buscar ofertas nuevas y mandartelas ----------
def buscar_y_enviar(estado):
    if not FORZAR and time.time() < estado["pausa_hasta"]:
        return  # en pausa por un fallo o bloqueo anterior
    if not FORZAR:
        time.sleep(random.uniform(0, JITTER_MAX))  # evita horarios exactos
    try:
        ofertas = obtener_ofertas()
        if not ofertas:
            raise RuntimeError(
                "no encontre ninguna oferta (la pagina pudo cambiar o "
                "bloquear la consulta)")
    except Exception as e:
        estado["fallos"] += 1
        espera = ESPERAS_BLOQUEO[min(estado["fallos"], len(ESPERAS_BLOQUEO)) - 1]
        estado["pausa_hasta"] = time.time() + espera * 60
        if FORZAR or not estado["fallo"]:  # avisa solo una vez
            tg("sendMessage", chat_id=OWNER,
               text=f"⚠️ No pude leer Mercado Livre: {e}\n"
                    f"Pauso las busquedas {espera // 60} h y reintento solo. "
                    "Te aviso solo esta vez.")
        estado["fallo"] = True
        return
    if estado["fallo"]:
        tg("sendMessage", chat_id=OWNER,
           text="✅ Ya puedo leer Mercado Livre otra vez.")
    estado["fallo"] = False
    estado["fallos"] = 0
    estado["pausa_hasta"] = 0

    vistos = set(estado["vistos"])
    nuevas = sorted(
        (o for o in ofertas
         if o["desc"] >= DESCUENTO_MINIMO and o["url"] not in vistos),
        key=lambda o: -o["desc"],
    )[:MAX_OFERTAS]

    for o in nuevas:
        txt = texto_privado(o)
        ok = False
        if o["img"]:
            ok = tg("sendPhoto", chat_id=OWNER, photo=o["img"],
                    caption=txt).get("ok")
        if not ok:
            ok = tg("sendMessage", chat_id=OWNER, text=txt,
                    disable_web_page_preview="true").get("ok")
        if ok:
            estado["vistos"].append(o["url"])


def modo_ofertas():
    estado = cargar_estado()
    try:
        buscar_y_enviar(estado)
    finally:
        guardar_estado(estado)


# ---------- Modo 2: publicar lo que respondiste, con pausas ----------
def publicar_una(m, link):
    orig = m["reply_to_message"]
    original = orig.get("caption") or orig.get("text") or ""
    final = texto_canal(original, link)
    if orig.get("photo"):
        r = tg("sendPhoto", chat_id=CANAL,
               photo=orig["photo"][-1]["file_id"], caption=final,
               parse_mode="HTML")
    else:
        r = tg("sendMessage", chat_id=CANAL, text=final,
               parse_mode="HTML", disable_web_page_preview="true")
    if r.get("ok"):
        tg("sendMessage", chat_id=OWNER, text="✅ Publicado en tu canal.")
    else:
        tg("sendMessage", chat_id=OWNER,
           text=f"❌ No pude publicar: {r.get('description')}")


def modo_publicar():
    hubo_publicacion = False
    while True:  # sigue hasta vaciar la cola (incluye respuestas nuevas)
        res = tg("getUpdates", timeout=0,
                 allowed_updates=json.dumps(["message"]))
        updates = res.get("result", [])
        if not updates:
            return
        for u in updates:
            m = u.get("message")
            if m and m["chat"]["id"] == OWNER:
                link = re.search(r"https?://meli\.la/\w+", m.get("text", ""))
                if link and not m.get("reply_to_message"):
                    tg("sendMessage", chat_id=OWNER,
                       text="Para publicar, RESPONDE (reply) al mensaje de "
                            "la oferta con tu enlace meli.la.")
                elif link:
                    if hubo_publicacion:
                        time.sleep(PAUSA_ENTRE_PUBLICACIONES)
                    publicar_una(m, link.group(0))
                    hubo_publicacion = True
            # marcar como leido (ya procesado) para no repetirlo
            tg("getUpdates", offset=u["update_id"] + 1, limit=1, timeout=0)


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "ofertas":
        modo_ofertas()
    elif modo == "publicar":
        modo_publicar()
    else:
        print("Uso: python bot.py ofertas | publicar")
