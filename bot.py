"""
Bot de ofertas de Mercado Livre -> Telegram

Modos:
  python bot.py ofertas   -> busca ofertas y te las manda a ti por privado
  python bot.py publicar  -> publica en tu canal las ofertas a las que
                             respondiste con tu enlace meli.la
"""
import json
import os
import re
import sys
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

# ---------- AJUSTES (puedes cambiarlos) ----------
DESCUENTO_MINIMO = 30   # solo ofertas con este % de rebaja o mas
MAX_OFERTAS = 10        # cuantas ofertas te manda cada dia
URL_OFERTAS = "https://www.mercadolivre.com.br/ofertas"
TEXTO_COMPRA = "🛒 Compre aqui:"  # frase antes de tu enlace en el canal
# -------------------------------------------------

TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER = int(os.environ["OWNER_CHAT_ID"])
CANAL = os.environ["CHANNEL_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
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
    enlace = card.select_one(
        "a.poly-component__title, a.promotion-item__link-container"
    ) or card.select_one("a[href]")
    if not enlace or not enlace.get("href"):
        return None

    t_el = card.select_one(".poly-component__title, .promotion-item__title")
    titulo = (t_el or enlace).get_text(" ", strip=True)
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


def obtener_ofertas():
    r = requests.get(URL_OFERTAS, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Mercado Livre respondio con codigo {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select("div.poly-card") or soup.select(
        "li.promotion-item, div.promotion-item"
    )
    vistos, ofertas = set(), []
    for c in cards:
        o = parsear(c)
        if o and o["url"] not in vistos:
            vistos.add(o["url"])
            ofertas.append(o)
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
    base = "\n".join(lineas).strip()
    return f"{base}\n\n{TEXTO_COMPRA} {link}"


# ---------- Modo 1: buscar y mandarte las ofertas ----------
def modo_ofertas():
    try:
        ofertas = obtener_ofertas()
    except Exception as e:
        tg("sendMessage", chat_id=OWNER,
           text=f"⚠️ No pude leer Mercado Livre: {e}")
        return
    if not ofertas:
        tg("sendMessage", chat_id=OWNER,
           text="⚠️ No pude leer ninguna oferta. Mercado Livre pudo cambiar su "
                "pagina o bloquear la consulta. Avisa para ajustar el bot.")
        return

    buenas = sorted(
        (o for o in ofertas if o["desc"] >= DESCUENTO_MINIMO),
        key=lambda o: -o["desc"],
    )[:MAX_OFERTAS]
    if not buenas:
        tg("sendMessage", chat_id=OWNER,
           text=f"Hoy no hay ofertas con {DESCUENTO_MINIMO}% o mas "
                f"(lei {len(ofertas)} productos).")
        return

    tg("sendMessage", chat_id=OWNER,
       text=f"☀️ {len(buenas)} ofertas de hoy. Genera tu enlace meli.la de las "
            "que quieras y respondelo al mensaje de cada una.")
    for o in buenas:
        txt = texto_privado(o)
        ok = False
        if o["img"]:
            ok = tg("sendPhoto", chat_id=OWNER, photo=o["img"],
                    caption=txt).get("ok")
        if not ok:
            tg("sendMessage", chat_id=OWNER, text=txt,
               disable_web_page_preview="true")


# ---------- Modo 2: publicar lo que respondiste ----------
def modo_publicar():
    res = tg("getUpdates", timeout=0,
             allowed_updates=json.dumps(["message"]))
    updates = res.get("result", [])
    if not updates:
        return

    for u in updates:
        m = u.get("message")
        if not m or m["chat"]["id"] != OWNER:
            continue
        link = re.search(r"https?://meli\.la/\w+", m.get("text", ""))
        if not link:
            continue
        orig = m.get("reply_to_message")
        if not orig:
            tg("sendMessage", chat_id=OWNER,
               text="Para publicar, RESPONDE (reply) al mensaje de la oferta "
                    "con tu enlace meli.la.")
            continue

        original = orig.get("caption") or orig.get("text") or ""
        final = texto_canal(original, link.group(0))
        if orig.get("photo"):
            r = tg("sendPhoto", chat_id=CANAL,
                   photo=orig["photo"][-1]["file_id"], caption=final)
        else:
            r = tg("sendMessage", chat_id=CANAL, text=final,
                   disable_web_page_preview="true")
        if r.get("ok"):
            tg("sendMessage", chat_id=OWNER, text="✅ Publicado en tu canal.")
        else:
            tg("sendMessage", chat_id=OWNER,
               text=f"❌ No pude publicar: {r.get('description')}")

    # marcar como leidos para no repetirlos
    tg("getUpdates", offset=updates[-1]["update_id"] + 1, limit=1, timeout=0)


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "ofertas":
        modo_ofertas()
    elif modo == "publicar":
        modo_publicar()
    else:
        print("Uso: python bot.py ofertas | publicar")
