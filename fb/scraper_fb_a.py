import csv
import json
import time
import random
import requests
import os
import re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

# ==========================================
# ⚙️ CONFIGURACIÓN
# ==========================================

ADS_API_KEY    = "29e5eb5717d367ee58ca6914be35e3e5008d5f6b6d18530b"
ADS_PROFILE_ID = "k1ce1hx4"
CSV_PATH       = "formato(Sheet1).csv"
JSON_OUTPUT    = "resultados_fb.json"
ADS_API_URL    = "http://localhost:50325/api/v1/browser/start"
MAX_SCROLL_RONDAS = 10

# ==========================================
# SCRIPT XHR — se inyecta ANTES de navegar
# ==========================================

INTERCEPTOR_JS = """
if (window.__fb_hooked) return;
window.__fb_hooked   = true;
window.__fb_xhr_data = [];

const _push = (text) => {
    if (text && text.length > 50 &&
        (text.includes('node_id') || text.includes('creation_time') ||
         text.includes('pfbid')   || text.includes('__typename') ||
         text.includes('feedback') || text.includes('reaction_count') ||
         text.includes('comment_count') || text.includes('follower_count') ||
         text.includes('profile_picture') || text.includes('wwwURL'))) {
        window.__fb_xhr_data.push(text.substring(0, 500000));
    }
};

// Interceptar fetch
const _origFetch = window.fetch;
window.fetch = async function(...args) {
    const res = await _origFetch.apply(this, args);
    try { const c = res.clone(); c.text().then(_push).catch(()=>{}); } catch(e){}
    return res;
};

// Interceptar XHR
const _origOpen = XMLHttpRequest.prototype.open;
const _origSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function(m, u, ...r) {
    this.__url = u; return _origOpen.call(this, m, u, ...r);
};
XMLHttpRequest.prototype.send = function(...a) {
    this.addEventListener('load', function() {
        try { _push(this.responseText); } catch(e) {}
    });
    return _origSend.apply(this, a);
};
"""

# ==========================================
# CONEXIÓN ADSPOWER
# ==========================================

def iniciar_adspower():
    print("Conectando con AdsPower...")
    params  = {"user_id": ADS_PROFILE_ID}
    headers = {"Authorization": f"Bearer {ADS_API_KEY}"}
    try:
        r = requests.get(ADS_API_URL, params=params, headers=headers).json()
        if r["code"] != 0:
            print(f"❌ AdsPower: {r['msg']}")
            return None
        selenium_url   = r["data"]["ws"]["selenium"]
        webdriver_path = r["data"]["webdriver"]
        os.chmod(webdriver_path, 0o755)
        opts = Options()
        opts.add_experimental_option("debuggerAddress", selenium_url)
        driver = webdriver.Chrome(service=Service(executable_path=webdriver_path), options=opts)
        # Inyectar interceptor globalmente via CDP para que esté antes de cada navegación
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": INTERCEPTOR_JS})
        except Exception as e:
            print(f"   ⚠️ CDP no disponible, usando fallback: {e}")
        print("✅ Conexión exitosa.")
        return driver
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

# ==========================================
# UTILIDADES HUMANAS
# ==========================================

def pausa(mn=3, mx=7):
    time.sleep(random.uniform(mn, mx))

def scroll_humano(driver, rondas=MAX_SCROLL_RONDAS):
    """Scroll con variación de velocidad y micro-pausas para parecer humano."""
    # Asegurar que el interceptor esté activo
    try:
        driver.execute_script(INTERCEPTOR_JS)
    except:
        pass

    print(f"   ⏳ Scrolleando ({rondas} rondas)...")
    prev = -1
    sin_cambio = 0

    for i in range(rondas):
        # Scroll variable — a veces rápido, a veces lento
        px = random.randint(600, 1400)
        driver.execute_script("window.scrollBy(0, arguments[0]);", px)

        # Micro-movimiento de mouse ocasional
        if random.random() < 0.3:
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                ActionChains(driver).move_to_element_with_offset(
                    body, random.randint(100, 800), random.randint(100, 500)
                ).perform()
            except:
                pass

        time.sleep(random.uniform(2.5, 5.5))

        try:
            total = driver.execute_script("return (window.__fb_xhr_data||[]).length;") or 0
        except:
            total = 0

        print(f"      Ronda {i+1}: {total} bloques XHR")
        if total == prev:
            sin_cambio += 1
            if sin_cambio >= 3:
                print("      ✋ Sin cambios. Deteniendo scroll.")
                break
        else:
            sin_cambio = 0
        prev = total

        # Pausa larga ocasional (simula lectura)
        if random.random() < 0.2:
            time.sleep(random.uniform(4, 8))

# ==========================================
# PARSERS GRAPHQL / XHR
# ==========================================

def limpiar_raw(raw):
    """Quita el prefijo for(;;); que FB pone en respuestas XHR."""
    return re.sub(r'^\s*for\s*\(;;\);\s*', '', raw.strip())

def extraer_numero(texto):
    """Extrae número de strings como '1.2K likes' → '1.2K'"""
    if not texto:
        return ""
    m = re.search(r'[\d,\.]+[KkMm]?', texto)
    return m.group(0) if m else texto.strip()

def parsear_seguidores_xhr(bloques):
    """Busca follower_count y following_count en bloques XHR."""
    followers = ""
    following = ""
    profile_pic = ""

    for raw in bloques:
        if not raw:
            continue
        # follower_count
        m = re.search(r'"follower_count"\s*:\s*(\d+)', raw)
        if m and not followers:
            followers = m.group(1)

        # following / friend_count
        m2 = re.search(r'"following_count"\s*:\s*(\d+)', raw)
        if m2 and not following:
            following = m2.group(1)

        m3 = re.search(r'"friend_count"\s*:\s*(\d+)', raw)
        if m3 and not following:
            following = m3.group(1)

        # profile pic
        if not profile_pic:
            m4 = re.search(r'"uri"\s*:\s*"(https://[^"]+/[^"]+profilepic[^"]+)"', raw, re.IGNORECASE)
            if not m4:
                m4 = re.search(r'"profile_picture"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', raw)
            if m4:
                profile_pic = m4.group(1).replace('\\/', '/')

    return followers, following, profile_pic

def decode_fb_text(t):
    """Decodifica unicode escapes de FB incluyendo emojis (surrogates)."""
    try:
        decoded = t.encode('utf-8').decode('unicode_escape', errors='surrogatepass')
        return decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
    except:
        try:
            return t.encode('raw_unicode_escape').decode('unicode_escape', errors='replace')
        except:
            return t

def parsear_bloque_en_stories(raw):
    """
    Parsea un bloque XHR de FB extrayendo Stories completas.
    Cada Story tiene: wwwURL, creation_time, reaction_count, comments.total_count, message.text, author.
    Retorna lista de dicts con los campos ya limpios.
    """
    stories = []
    vistos  = set()

    # Dividir en Stories por boundary "__typename":"Story"
    partes = re.split(r'"__typename"\s*:\s*"Story"', raw)

    for parte in partes[1:]:  # la primera parte es el prefijo sin story
        # URL — viene en wwwURL con escapes \\/
        url_match = re.search(r'"wwwURL"\s*:\s*"([^"]+)"', parte)
        if not url_match:
            continue
        url = url_match.group(1).replace('\\/', '/')
        # Normalizar: quitar query strings que no sean pfbid
        url_limpia = re.sub(r'\?(?!pfbid)[^"]*', '', url).rstrip('/')
        if url_limpia in vistos or len(url_limpia) < 25:
            continue
        vistos.add(url_limpia)

        # Timestamp unix
        fecha_dt  = None
        fecha_iso = ""
        t_match = re.search(r'"creation_time"\s*:\s*(\d{10})', parte)
        if t_match:
            try:
                fecha_dt  = datetime.fromtimestamp(int(t_match.group(1)))
                fecha_iso = fecha_dt.isoformat()
            except:
                pass

        # Reacciones — estructura real: "reaction_count":{"count":N}
        likes = ""
        r_match = re.search(r'"reaction_count"\s*:\s*\{\s*"count"\s*:\s*(\d+)', parte)
        if r_match:
            likes = r_match.group(1)

        # Comentarios — estructura real: "comments":{"total_count":N}
        comments_count = ""
        c_match = re.search(r'"comments"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)', parte)
        if c_match:
            comments_count = c_match.group(1)

        # Caption/message — estructura real: {"message":{"text":"..."}}  (doblemente anidado)
        caption     = ""
        caption_raw = ""
        # Patrón exacto confirmado en datos reales
        m_match = re.search(
            r'"message"\s*:\s*\{[^}]*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
            parte
        )
        if m_match:
            caption_raw = m_match.group(1)
            caption     = decode_fb_text(caption_raw)

        # Autor — owning_profile.name
        autor = ""
        a_match = re.search(r'"owning_profile"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', parte)
        if a_match:
            autor = decode_fb_text(a_match.group(1))

        stories.append({
            "url":         url_limpia,
            "fecha_dt":    fecha_dt,
            "fecha_iso":   fecha_iso,
            "likes":       likes,
            "comments":    comments_count,
            "caption":     caption,
            "caption_raw": caption_raw,
            "autor":       autor,
        })

    return stories


def parsear_posts_xhr(bloques, f_inicio_str, f_fin_str):
    """Extrae posts con url, fecha, likes, comentarios, caption de bloques XHR."""
    vistos = set()
    posts  = []

    try:
        dt_inicio = datetime.fromisoformat(f_inicio_str + "T00:00:00")
        dt_fin    = datetime.fromisoformat(f_fin_str    + "T23:59:59")
    except:
        dt_inicio = None
        dt_fin    = None

    for raw in bloques:
        if not raw:
            continue
        raw = limpiar_raw(raw)

        for s in parsear_bloque_en_stories(raw):
            if s["url"] in vistos:
                continue

            # Filtro de rango
            if dt_inicio and dt_fin and s["fecha_dt"]:
                if not (dt_inicio <= s["fecha_dt"] <= dt_fin):
                    continue

            vistos.add(s["url"])
            posts.append({
                "url":            s["url"],
                "fecha_post_iso": s["fecha_iso"],
                "likes_text":     s["likes"],
                "comments_text":  s["comments"],
                "username_post":  s["autor"],
                "fecha_text":     s["fecha_iso"][:10] if s["fecha_iso"] else "",
                "caption":        s["caption"],
                "caption_raw":    s["caption_raw"],
                "comments":       [],
            })

    return posts

# ==========================================
# EXTRACCIÓN DOM (fallback)
# ==========================================

def extraer_posts_dom(driver, f_inicio_str, f_fin_str):
    """Fallback: extrae posts directo del DOM cuando XHR falla."""
    posts  = []
    vistos = set()

    try:
        dt_inicio = datetime.fromisoformat(f_inicio_str + "T00:00:00")
        dt_fin    = datetime.fromisoformat(f_fin_str    + "T23:59:59")
    except:
        dt_inicio = dt_fin = None

    # Buscar todos los <a> que parezcan links de posts
    links = driver.find_elements(By.XPATH,
        "//a[contains(@href,'/posts/') or contains(@href,'pfbid') or contains(@href,'permalink')]"
    )
    contenedores_vistos = set()

    for a in links:
        href = (a.get_attribute("href") or "").split("?")[0].rstrip("/")
        if not href or href in vistos or len(href) < 20:
            continue
        if not any(k in href for k in ["/posts/", "pfbid", "permalink"]):
            continue
        vistos.add(href)

        # Subir al contenedor del post
        caption = ""
        fecha_iso = ""
        try:
            contenedor = a
            for _ in range(6):
                contenedor = contenedor.find_element(By.XPATH, "..")
                texto_c = contenedor.text.strip()
                if len(texto_c) > 30:
                    caption = texto_c[:500]
                    break
        except:
            pass

        # Fecha desde atributo aria-label o data-utime
        try:
            time_el = driver.find_element(By.XPATH,
                f"//a[@href[contains(.,'{href.split('/')[-1]}')]]//ancestor::div//abbr[@data-utime]"
            )
            utime = time_el.get_attribute("data-utime")
            if utime:
                fecha_dt  = datetime.fromtimestamp(int(utime))
                fecha_iso = fecha_dt.isoformat()
                if dt_inicio and dt_fin and not (dt_inicio <= fecha_dt <= dt_fin):
                    continue
        except:
            pass

        posts.append({
            "url":            href,
            "fecha_post_iso": fecha_iso,
            "likes_text":     "",
            "comments_text":  "",
            "username_post":  "",
            "fecha_text":     fecha_iso[:10] if fecha_iso else "",
            "caption":        caption,
            "caption_raw":    "",
            "comments":       [],
        })

    return posts

# ==========================================
# EXTRACCIÓN DE PERFIL
# ==========================================

def extraer_perfil_dom(driver):
    """Extrae datos del perfil desde el DOM (nombre, bio, ciudad)."""
    datos = {"nombre": "", "bio": "", "ciudad": ""}

    try:
        datos["nombre"] = driver.find_element(By.TAG_NAME, "h1").text.strip()
    except:
        pass

    # Bio — primer span largo en la sección intro
    for xpath in [
        "//div[@data-pagelet='ProfileAppSection_0']//span[string-length(normalize-space(text())) > 15][1]",
        "//div[contains(@class,'x1e56ztr')]//span[string-length(text()) > 15][1]",
    ]:
        try:
            datos["bio"] = driver.find_element(By.XPATH, xpath).text.strip()
            if datos["bio"]:
                break
        except:
            pass

    # Ciudad
    for pattern in ["Vive en", "Lives in", "De ", "From "]:
        try:
            el = driver.find_element(By.XPATH,
                f"//*[contains(normalize-space(text()),'{pattern}')]"
            )
            datos["ciudad"] = el.text.strip()
            break
        except:
            pass

    return datos

def extraer_seguidores_dom(driver):
    """Extrae seguidores/amigos del DOM como fallback."""
    followers = ""
    following = ""

    patrones_follow = [
        "//a[contains(@href,'followers')]//span[contains(text(),'seguidor') or contains(text(),'follower')]",
        "//a[contains(@href,'followers')]",
        "//*[contains(text(),'seguidores') or contains(text(),'followers')]",
    ]
    for xpath in patrones_follow:
        try:
            el  = driver.find_element(By.XPATH, xpath)
            txt = el.text.strip()
            if txt:
                followers = txt
                break
        except:
            pass

    patrones_friend = [
        "//a[contains(@href,'friends')]//span",
        "//*[contains(text(),'amigos') or contains(text(),'friends')]",
    ]
    for xpath in patrones_friend:
        try:
            el  = driver.find_element(By.XPATH, xpath)
            txt = el.text.strip()
            if txt and ("amigo" in txt.lower() or "friend" in txt.lower() or txt[0].isdigit()):
                following = txt
                break
        except:
            pass

    return followers, following

def extraer_profile_pic_dom(driver):
    try:
        img = driver.find_element(By.XPATH,
            "//image[@xlink:href] | //img[contains(@class,'profilePic')] | "
            "//img[contains(@alt,'foto de perfil') or contains(@alt,'profile picture')]"
        )
        return img.get_attribute("src") or img.get_attribute("xlink:href") or ""
    except:
        try:
            # SVG profile pic (FB moderno)
            el = driver.find_element(By.XPATH, "//svg//image")
            return el.get_attribute("xlink:href") or ""
        except:
            return ""

# ==========================================
# ORQUESTADOR POR PERFIL
# ==========================================

def procesar_perfil(driver, info):
    usuario   = info["usuario"].strip()
    url_base  = f"https://www.facebook.com/{usuario}"
    print(f"\n{'='*55}")
    print(f"🔍 Procesando: {url_base}")

    # ── Navegar al feed ──
    driver.get(url_base)
    pausa(6, 10)

    # Asegurar interceptor activo (por si CDP no funcionó)
    try:
        driver.execute_script(INTERCEPTOR_JS)
    except:
        pass

    scroll_humano(driver)

    # ── Recolectar bloques XHR ──
    try:
        bloques_xhr = driver.execute_script("return window.__fb_xhr_data || [];") or []
    except:
        bloques_xhr = []
    print(f"   🌐 Bloques XHR capturados: {len(bloques_xhr)}")

    # ── Parsear seguidores desde XHR ──
    followers, following, profile_pic = parsear_seguidores_xhr(bloques_xhr)

    # ── Fallback DOM para seguidores ──
    if not followers or not following:
        f_dom, fw_dom = extraer_seguidores_dom(driver)
        followers = followers or f_dom
        following = following or fw_dom

    if not profile_pic:
        profile_pic = extraer_profile_pic_dom(driver)

    # ── Parsear posts desde XHR ──
    posts = parsear_posts_xhr(bloques_xhr, info["f_inicio"], info["f_fin"])
    print(f"   📦 Posts XHR: {len(posts)}")

    # ── Fallback DOM si XHR no dio posts ──
    if not posts:
        print("   🔄 Fallback DOM para posts...")
        posts = extraer_posts_dom(driver, info["f_inicio"], info["f_fin"])
        print(f"   📦 Posts DOM: {len(posts)}")

    # ── Navegar a /about ──
    driver.get(f"{url_base}/about")
    pausa(4, 7)

    try:
        driver.execute_script(INTERCEPTOR_JS)
    except:
        pass

    scroll_humano(driver, rondas=4)

    # Más XHR desde /about (puede tener follower_count)
    try:
        bloques_about = driver.execute_script("return window.__fb_xhr_data || [];") or []
        if len(bloques_about) > len(bloques_xhr):
            f2, fw2, pp2 = parsear_seguidores_xhr(bloques_about)
            followers  = followers  or f2
            following  = following  or fw2
            profile_pic = profile_pic or pp2
    except:
        pass

    perfil_dom = extraer_perfil_dom(driver)

    print(f"   👤 {perfil_dom['nombre']} | Followers: {followers} | Posts: {len(posts)}")

    return {
        "id":           info["id"],
        "usuario":      usuario,
        "url":          url_base,
        "f_inicio":     info["f_inicio"],
        "f_fin":        info["f_fin"],
        "username":     usuario,
        "nombre":       perfil_dom["nombre"],
        "followers":    followers,
        "following":    following,
        "posts":        str(len(posts)),
        "profile_pic":  profile_pic,
        "links_posts":  [p["url"] for p in posts],
        "rango_fechas": {"inicio": info["f_inicio"], "fin": info["f_fin"]},
        "posts_data":   posts,
        "fecha_extraccion": datetime.now().isoformat(),
    }

# ==========================================
# CSV LOADER
# ==========================================

def cargar_csv(ruta):
    usuarios = []
    try:
        with open(ruta, mode="r", encoding="utf-8-sig") as f:
            for fila in csv.DictReader(f):
                fb = fila.get("facebook", "").strip()
                if not fb:
                    continue
                usuarios.append({
                    "id":       fila.get("id", "").strip(),
                    "usuario":  fb,
                    "f_inicio": fila.get("F. Inicio", "2000-01-01").strip(),
                    "f_fin":    fila.get("F. Fin",    "2099-12-31").strip(),
                })
    except Exception as e:
        print(f"❌ CSV: {e}")
    return usuarios

# ==========================================
# MAIN
# ==========================================

def main():
    print("=== FB SCRAPER ===")

    usuarios = cargar_csv(CSV_PATH)
    if not usuarios:
        print("❌ Sin usuarios en CSV con columna 'facebook'.")
        return
    print(f"📋 Usuarios a procesar: {len(usuarios)}")

    driver = iniciar_adspower()
    if not driver:
        return

    resultados = []

    try:
        for u in usuarios:
            try:
                data = procesar_perfil(driver, u)
                resultados.append(data)
                print(f"✅ {u['usuario']} — posts: {data['posts']}")
            except Exception as e:
                print(f"❌ Error en {u['usuario']}: {e}")
                resultados.append({
                    "id": u["id"], "usuario": u["usuario"],
                    "url": f"https://www.facebook.com/{u['usuario']}",
                    "error": str(e),
                    "posts_data": []
                })
            pausa(18, 30)  # pausa larga entre perfiles

    except KeyboardInterrupt:
        print("\n🛑 Interrumpido.")

    finally:
        with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
            json.dump({"resultados": resultados}, f, indent=2, ensure_ascii=False)
        print(f"\n📂 Guardado: {JSON_OUTPUT}")

if __name__ == "__main__":
    main()