import os
import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import json
import time

# --- 1. CONFIGURACIÓN ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_DE_BOTFATHER")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "@tu_canal_o_id_numerico")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "TU_CLAVE_DE_GROQ")
RSS_URL = "http://www.juventudrebelde.cu/get/rss/grupo/generales/"
ARCHIVO_PROCESADOS = "noticias_procesadas.json"

# --- 2. CLIENTE DE IA (Groq) ---
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)


def cargar_procesados():
    if os.path.exists(ARCHIVO_PROCESADOS):
        with open(ARCHIVO_PROCESADOS, "r") as f:
            return set(json.load(f))
    return set()


def guardar_procesados(procesados):
    with open(ARCHIVO_PROCESADOS, "w") as f:
        json.dump(list(procesados), f)


def obtener_url_imagen(entrada):
    """Extrae la URL de la imagen principal: primero del RSS, si falla del HTML canónico."""
    # --- 1. Intentar desde el RSS ---
    if "enclosures" in entrada:
        for enlace in entrada.enclosures:
            if enlace.get("type", "").startswith("image/"):
                return enlace.get("href")
    if "media_content" in entrada:
        for media in entrada.media_content:
            if media.get("url"):
                return media.get("url")
    if "media_thumbnail" in entrada:
        for thumb in entrada.media_thumbnail:
            if thumb.get("url"):
                return thumb.get("url")

    # --- 2. Forzar versión canónica del artículo y buscar og:image ---
    url_original = entrada.link
    if "/index.php/" not in url_original:
        partes = url_original.split("/", 3)
        if len(partes) >= 4:
            url_canonica = f"{partes[0]}//{partes[2]}/index.php/{partes[3]}"
        else:
            url_canonica = url_original
    else:
        url_canonica = url_original

    try:
        respuesta = requests.get(
            url_canonica,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                )
            },
        )
        respuesta.raise_for_status()
        sopa = BeautifulSoup(respuesta.text, "html.parser")

        og_image = sopa.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            return og_image["content"]

        tw_image = sopa.find("meta", attrs={"name": "twitter:image"})
        if tw_image and tw_image.get("content"):
            return tw_image["content"]

    except Exception as e:
        print(f"   ❌ Error al buscar imagen en el artículo: {e}")

    return None


def extraer_contenido(url, reintentos=2, espera=5):
    for intento in range(1, reintentos + 1):
        try:
            respuesta = requests.get(
                url,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    )
                },
            )
            respuesta.raise_for_status()
            sopa = BeautifulSoup(respuesta.text, "html.parser")
            parrafos = sopa.find_all("p")
            texto = "\n".join(
                p.get_text(strip=True)
                for p in parrafos
                if len(p.get_text(strip=True)) > 50
            )
            return texto
        except requests.exceptions.Timeout:
            print(f"   ⏳ Intento {intento}/{reintentos}: Timeout. Reintentando en {espera}s...")
            time.sleep(espera)
        except Exception as e:
            print(f"   ❌ Error al extraer contenido de {url}: {e}")
            return None
    print(f"   ❌ No se pudo obtener el contenido tras {reintentos} intentos.")
    return None


def generar_post_con_ia(titulo, contenido):
    prompt_sistema = """Actúa como un editor de un canal de noticias cubano llamado Juventud Rebelde. A partir del artículo proporcionado, genera un post atractivo para Telegram.

El post debe tener:
1. Un titular llamativo y conciso (máx. 100 caracteres), sin la palabra "Titular". Usa un emoji relacionado al inicio y otro al final del titular.
2. Un resumen de 2-3 frases que explique la noticia principal de forma clara y directa, sin la palabra "Resumen".
3. Un tono informativo, profesional pero cercano, que invite a la lectura.
4. Finaliza con un llamado a la acción (CTA) que diga exactamente: "Si quieres conocer más de esta y otras noticias, visítanos en nuestra web www.juventudrebelde.cu"

El texto completo del post (titular + resumen + CTA) NO debe exceder los 900 caracteres. No uses Markdown ni negritas, solo texto plano y emojis."""

    try:
        respuesta = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Título: {titulo}\n\nContenido: {contenido[:4000]}"},
            ],
            temperature=0.7,
        )
        return respuesta.choices[0].message.content.strip()
    except Exception as e:
        print(f"   ❌ Error al contactar la IA: {e}")
        return None


def enviar_a_telegram(texto, url_imagen=None):
    """
    Envía un mensaje o una foto con texto al canal de Telegram.
    Si hay URL de imagen, la DESCARGA primero y luego la sube como archivo adjunto
    (multipart/form-data), evitando que Telegram intente descargarla por sí mismo.
    """
    if url_imagen:
        try:
            print(f"   📥 Descargando imagen desde: {url_imagen}")
            resp_img = requests.get(
                url_imagen,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    )
                },
            )
            resp_img.raise_for_status()

            url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            files = {"photo": ("imagen.jpg", resp_img.content)}
            payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": texto}

            respuesta = requests.post(url_api, data=payload, files=files, timeout=30)
            respuesta.raise_for_status()
            print("   ✅ Post publicado en Telegram (con imagen subida).")
            return True

        except requests.exceptions.RequestException as e:
            print(f"   ❌ Error al descargar o subir la imagen: {e}")
            # Respaldo: publicar solo texto
            print("   📝 Reintentando publicar solo el texto...")
            url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload_texto = {"chat_id": TELEGRAM_CHAT_ID, "text": texto}
            try:
                resp_texto = requests.post(url_api, json=payload_texto, timeout=15)
                resp_texto.raise_for_status()
                print("   ✅ Post publicado en Telegram (solo texto, respaldo).")
                return True
            except Exception as e2:
                print(f"   ❌ Error al publicar solo el texto: {e2}")
                return False
    else:
        url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "disable_web_page_preview": False,
        }
        try:
            respuesta = requests.post(url_api, json=payload, timeout=15)
            respuesta.raise_for_status()
            print("   ✅ Post publicado en Telegram (solo texto).")
            return True
        except Exception as e:
            print(f"   ❌ Error al publicar en Telegram: {e}")
            return False


def main():
    print("🔍 Buscando noticias nuevas...")
    procesados = cargar_procesados()
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        print("⚠️ No se encontraron entradas en el feed RSS. Verifica la URL.")
        # Guardar de todas formas para que el workflow no falle en 'git add'
        guardar_procesados(procesados)
        return

    for entrada in reversed(feed.entries):
        url = entrada.link
        if url in procesados:
            continue

        print(f"📰 Nueva noticia detectada: {entrada.title}")

        url_imagen = obtener_url_imagen(entrada)

        contenido = extraer_contenido(url)
        if not contenido:
            print("   ❌ No se pudo extraer el contenido. Se omite.")
            continue

        print("   ✍️ Generando post con IA...")
        post = generar_post_con_ia(entrada.title, contenido)
        if not post:
            print("   ❌ La IA no pudo generar el post. Se omite.")
            continue

        exito = enviar_a_telegram(post, url_imagen=url_imagen)

        if exito:
            procesados.add(url)
            print("   ✅ Procesada y guardada.")
        else:
            print("   ❌ Falló el envío a Telegram. Se reintentará en la próxima ejecución.")

        # Pequeña pausa para no saturar el rate limit de Groq
        time.sleep(3)

    # CRÍTICO: guardar SIEMPRE el archivo, aunque no se haya procesado nada,
    # para que 'git add noticias_procesadas.json' funcione en el workflow.
    guardar_procesados(procesados)

    print("🏁 Revisión completada.")


if __name__ == "__main__":
    main()
