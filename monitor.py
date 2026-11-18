import json
import os
import re
from curl_cffi import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"

PRECIO_MIN = 480.00   # Evita falsos positivos con fundas o juegos
PRECIO_MAX = 535.00   # Precio oficial (519,99 €) + margen pequeño; descarta reventa

# Pega aquí las URLs exactas de la ficha del producto en cada tienda
TIENDAS = {
    "My Nintendo Store": "https://store.nintendo.com/es-es/nintendo-switch-2-edicin-40-aniversario-de-the-legend-of-zelda-P00211?&srsltid=AYCX9vJGUNcON8hWJQRCiPwA9P-aCQ9JLcPBXTUWpUoTd5DI8MUMXrti",
    "GAME": "https://www.game.es/nintendo-switch-2-edicion-zelda-40th/267689",
    "MediaMarkt": "https://www.mediamarkt.es/es/product/_consola-nintendo-switch-2-edicion-zelda-40-aniversario-79-full-hd-hdr-120-hz-256-gb-magnetic-joy-con-2-con-modo-raton-1674231.html?utm_source=google&utm_medium=v2-sho&utm_campaign=es%7Cmm%7Crt%7Cv2-sho%7Cpmax%7Cnsp%7CMEDIUM%7Cna%7Cd2c%7CNA%7CB0009TVU&utm_content=B0009TVU-TCID23908560920&gclsrc=aw.ds&gad_source=1&gad_campaignid=23944133454&gbraid=0AAAAADL_At-gVj_vGFJR-s9XA8rGskCPe&gclid=Cj0KCQiAzeXXBhCGARIsADO5-zE7wEr7m8HdlCEhin8hX1kTH9D6NOikwXpai_ssAPxJabJcM_hA4YsaAvRvEALw_wcB",
    "El Corte Inglés": "https://www.elcorteingles.es/videojuegos/A202302815-nintendo-switch-2-edicion-zelda-40-aniversario/",
    "Fnac": "https://www.fnac.es/Consola-Nintendo-Switch-2-The-Legend-of-Zelda-40-Aniversario-Videoconsola/a13481099",
    "Alcampo": "https://www.compraonline.alcampo.es/products/nintendo-switch-2-edici%C3%B3n-zelda-40-aniversario/435088",
    "Xtralife": "https://www.xtralife.com/producto/nintendo-switch-2-edicion-the-legend-of-zelda-40-aniversario-switch-2-edicion-especial/113185?srsltid=AYCX9vJJgJUiyG2hcCSQcRYVWvocSKEqw-ONwTrSFUuAHip9g1Lh30EVM1o",
    "Amazon España": "https://www.amazon.es/Nintendo-Switch-2-Legend-Zelda/dp/B0F2TN43GH",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PALABRAS_AGOTADO = [
    "agotado",
    "sin stock",
    "no disponible",
    "temporalmente agotado",
    "avísame",
    "avisarme",
    "alertarme",
    "indisponible",
    "ver disponibilidad en tienda",
    "solo disponible en tienda",
    "out of stock",
]

PALABRAS_COMPRA = [
    "añadir a la cesta",
    "añadir al carrito",
    "comprar ahora",
    "comprar ya",
    "en stock",
    "disponible online",
]


def enviar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Faltan credenciales de Telegram.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    requests.post(url, json=payload, timeout=15)


def limpiar_precio(valor) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).replace("€", "").replace("\xa0", "").strip()
    # Convierte formato europeo "1.099,99" o "519,99" a float
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    match = re.search(r"\d+(?:\.\d{1,2})?", texto)
    return float(match.group()) if match else None


def analizar_json_ld(soup: BeautifulSoup) -> tuple[bool, float | None]:
    """Busca metadatos estructurados Schema.org evitando precios históricos de catálogo."""
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            if not offers:
                continue
            offer_list = offers if isinstance(offers, list) else [offers]
            for offer in offer_list:
                if not isinstance(offer, dict):
                    continue
                avail = str(offer.get("availability", "")).lower()
                # Usamos solo "price" (evitamos "lowPrice", que Fnac deja fijado en 519,99 € aunque sea reventa)
                precio = limpiar_precio(offer.get("price"))
                if (
                    "instock" in avail
                    and precio is not None
                    and PRECIO_MIN <= precio <= PRECIO_MAX
                ):
                    return True, precio
    return False, None


def revisar_tienda(nombre: str, url: str) -> tuple[bool, float | None]:
    if url.endswith("..."):
        return False, None

    # Primer intento como Chrome
    resp = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=20)

    # Si da 403, reintenta como Safari antes de darlo por bloqueado
    if resp.status_code == 403:
        resp = requests.get(url, headers=HEADERS, impersonate="safari15_5", timeout=20)

    if resp.status_code != 200:
        print(f"[{nombre}] Bloqueado por cortafuegos (HTTP {resp.status_code}) - vigilar desde navegador")
        return False, None

    soup = BeautifulSoup(resp.text, "html.parser")
    texto = soup.get_text(" ", strip=True).lower()

    # Filtro anti-reventa para Fnac: si no la vende Fnac directamente, descartamos
    if nombre == "Fnac" and "vendido por fnac" not in texto:
        return False, None

    # 1. Comprobación fiable vía JSON-LD
    hay_stock, precio = analizar_json_ld(soup)
    if hay_stock:
        return True, precio

    # 2. Comprobación alternativa en el texto visible de la página
    if any(p in texto for p in PALABRAS_AGOTADO):
        return False, None

    if any(p in texto for p in PALABRAS_COMPRA):
        precios_encontrados = re.findall(r"(5[0-3]\d[.,]\d{2})\s*€?", texto)
        for p_str in precios_encontrados:
            p_val = limpiar_precio(p_str)
            if p_val and PRECIO_MIN <= p_val <= PRECIO_MAX:
                return True, p_val

    return False, None


def main():
    alertas = []
    for tienda, url in TIENDAS.items():
        try:
            disponible, precio = revisar_tienda(tienda, url)
            if disponible:
                precio_txt = f"{precio:.2f} €".replace(".", ",")
                alertas.append(
                    f"🚨 <b>¡STOCK DETECTADO en {tienda}!</b>\n"
                    f"💰 Precio: <b>{precio_txt}</b>\n"
                    f"🛒 <a href='{url}'>COMPRAR AHORA</a>"
                )
            else:
                print(f"[{tienda}] Sin stock oficial.")
        except Exception as e:
            print(f"[{tienda}] Error al consultar: {e}")

    if alertas:
        enviar_telegram("\n\n".join(alertas))
    elif TEST_MODE:
        enviar_telegram(
            "✅ <b>Prueba superada:</b> El vigilante de Nintendo Switch 2 Zelda está activo y conectado a tu móvil."
        )


if __name__ == "__main__":
    main()
