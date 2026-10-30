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

CARPETA = os.path.dirname(os.path.abspath(__file__))


def cargar_config():
    """Lee config.txt (si existe) con lineas CLAVE=VALOR. Las variables de
    entorno (GitHub) tienen prioridad."""
    ruta = os.path.join(CARPETA, "config.txt")
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8-sig") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


cargar_config()

# ---------- AJUSTES (puedes cambiarlos) ----------
DESCUENTO_MINIMO = 20            # solo ofertas con este % de rebaja o mas
MAX_OFERTAS = 10                 # ofertas NUEVAS que te manda por busqueda
