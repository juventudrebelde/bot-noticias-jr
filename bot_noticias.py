import os
import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import json

# --- 1. CONFIGURACIÓN ---
TELEGRAM_BOT_TOKEN = "9039109666:AAEdVjYcUawSXkjc8RsKqkmbHsCWnxJi6qU"
TELEGRAM_CHAT_ID = "-1001728612831"
GROQ_API_KEY = "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"
RSS_URL = "http://www.juventudrebelde.cu/get/rss/grupo/generales/"
ARCHIVO_PROCESADOS = "noticias_procesadas.json"

# --- 2. CLIENTE DE IA (Groq) ---
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)

def cargar_procesados():
    if os.path.exists(ARCHIVO_PROCESADOS):
        with open(ARCHIVO_PROCESADOS, 'r') as f:
            return set(json.load(f))
    return set()

def guardar_procesados(procesados):
    with open(ARCHIVO_PROCESADOS, 'w') as f:
        json.dump(list(procesados), f)

    import time # Asegúrate de tener esta importación al inicio del archivo

def extraer_contenido(url, reintentos=2, espera=5):
    for intento in range(1, reintentos + 1):
        try:
            respuesta = requests.get(
                url,
                timeout=30,  # Aumentado de 15 a 30 segundos
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            respuesta.raise_for_status()
            sopa = BeautifulSoup(respuesta.text, 'html.parser')
            parrafos = sopa.find_all('p')
            texto = "\n".join(p.get_text(strip=True) for p in parrafos if len(p.get_text(strip=True)) > 50)
            return texto
        except requests.exceptions.Timeout:
            print(f"   ⏳ Intento {intento}/{reintentos}: Tiempo de espera agotado. Reintentando en {espera}s...")
            time.sleep(espera)
        except Exception as e:
            print(f"   ❌ Error al extraer contenido de {url}: {e}")
            return None # Para otros errores (404, 500, etc.), no reintentar
    print(f"   ❌ No se pudo obtener el contenido tras {reintentos} intentos.")
    return None

def generar_post_con_ia(titulo, contenido):
    prompt_sistema = """Actúa como un editor de un canal de noticias cubano. A partir del artículo proporcionado, genera un post para Telegram.
El post debe tener:
1. Un titular llamativo y conciso (máx. 100 caracteres), sin que nunca diga Titular o algo parecido, solo el titular generado. Emojis que se asocien con el post en el titular, uno al inicio y otro al final. 
2. Un resumen de 2-3 frases que explique la noticia principal, sin que nunca diga resumen, solo el texto del resumen.
3. Un tono informativo, profesional y cercano.
4. Al final, incluye un CTA que invite a visitar nuestra web en www.juventudrebelde.cu que diga: Si quieres conocer más de esta y otras noticias, visítanos en nuestra web www.juventudrebelde.cu"""
    
    try:
        respuesta = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Título: {titulo}\n\nContenido: {contenido[:4000]}"}
            ],
            temperature=0.7,
        )
        return respuesta.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error al contactar la IA: {e}")
        return None

def enviar_a_telegram(texto):
    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "disable_web_page_preview": False
    }
    try:
        respuesta = requests.post(url_api, json=payload, timeout=10)
        respuesta.raise_for_status()
        print("✅ Post publicado en Telegram.")
        return True
    except Exception as e:
        print(f"Error al publicar en Telegram: {e}")
        return False

def main():
    print("🔍 Buscando noticias nuevas...")
    procesados = cargar_procesados()
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        print("⚠️ No se encontraron entradas en el feed RSS. Verifica la URL.")
        return

    for entrada in reversed(feed.entries):
        url = entrada.link
        if url in procesados:
            continue

        print(f"📰 Nueva noticia detectada: {entrada.title}")
        contenido = extraer_contenido(url)
        if not contenido:
            print("   ❌ No se pudo extraer el contenido. Se omite.")
            continue

        print("   ✍️ Generando post con IA...")
        post = generar_post_con_ia(entrada.title, contenido)
        if not post:
            print("   ❌ La IA no pudo generar el post. Se omite.")
            continue

        if enviar_a_telegram(post):
            procesados.add(url)
            guardar_procesados(procesados)
            print("   ✅ Procesada y guardada.")
        else:
            print("   ❌ Falló el envío a Telegram. Se reintentará en la próxima ejecución.")

    print("🏁 Revisión completada.")

if __name__ == "__main__":
    main()
