import csv
import json
import time
import random
import requests
import os
import re
import hashlib
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ CONFIGURACIÓN
# ==========================================

ADS_API_KEY    = "29e5eb5717d367ee58ca6914be35e3e5008d5f6b6d18530b"
ADS_PROFILE_ID = "k1ce1hx4"
CSV_PATH       = "formato(Sheet1).csv"
CARPETA_OUTPUT = "resultados_fb"
JSON_OUTPUT    = os.path.join(CARPETA_OUTPUT, "resultados_fb.json")
JSON_ANON      = os.path.join(CARPETA_OUTPUT, "resultados_fb_anonimizado.json")
ADS_API_URL    = "http://localhost:50325/api/v1/browser/start"
MAX_SCROLL_RONDAS = 16

# ==========================================
# INTERCEPTOR XHR
# — NO resetea __fb_hooked para evitar doble-hook de fetch
# ==========================================

INTERCEPTOR_JS = """
if (window.__fb_hooked) return;
window.__fb_hooked   = true;
window.__fb_xhr_data = window.__fb_xhr_data || [];

const _push = (text) => {
    if (!text || text.length < 50) return;
    if (text.startsWith('for (;;);') ||
        text.includes('creation_time') || text.includes('pfbid')   ||
        text.includes('wwwURL')        || text.includes('"story"')  ||
        text.includes('"edges"')       || text.includes('"node"')   ||
        text.includes('reaction_count')|| text.includes('"body"')   ||
        text.includes('follower_count')|| text.includes('"text"')) {
        window.__fb_xhr_data.push(text.substring(0, 500000));
    }
};

const _origFetch = window.fetch;
window.fetch = async function(...args) {
    const res = await _origFetch.apply(this, args);
    try { const c = res.clone(); c.text().then(_push).catch(()=>{}); } catch(e){}
    return res;
};

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

# Reset solo borra el buffer, nunca el hook
RESET_BUFFER_JS = "window.__fb_xhr_data = [];"

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
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                                   {"source": INTERCEPTOR_JS})
            print("   CDP interceptor registrado.")
        except Exception as e:
            print(f"   ⚠️ CDP no disponible: {e}")
        print("✅ Conexión exitosa.")
        return driver
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

# ==========================================
# UTILIDADES
# ==========================================

def pausa(mn=2, mx=5):
    time.sleep(random.uniform(mn, mx))

def asegurar_interceptor(driver):
    """Inyecta solo si no está activo aún — evita doble-hook."""
    try:
        activo = driver.execute_script("return !!window.__fb_hooked;")
        if not activo:
            driver.execute_script(INTERCEPTOR_JS)
    except:
        pass

def resetear_buffer(driver):
    """Solo limpia el buffer, nunca toca el hook."""
    try:
        driver.execute_script(RESET_BUFFER_JS)
    except:
        pass

def scroll_humano(driver, rondas=MAX_SCROLL_RONDAS):
    """
    Scrollea Y captura posts del DOM en cada ronda — FB usa virtualizacion
    (windowing) y descarta del DOM los <article> que salen de pantalla.
    Si solo leemos el DOM al final, ya se perdieron. Por eso esta funcion
    ahora también extrae y acumula posts en vivo, ronda por ronda.
    """
    asegurar_interceptor(driver)
    print(f"   ⏳ Scrolleando ({rondas} rondas)...")
    prev_total       = -1
    sin_cambio       = 0
    posts_acumulados = []
    urls_vistas      = set()

    # Captura inmediata en posición actual (antes de cualquier scroll)
    for dp in extraer_posts_dom(driver):
        if dp["url"] not in urls_vistas:
            urls_vistas.add(dp["url"])
            posts_acumulados.append(dp)

    for i in range(rondas):
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(200, 350))
            time.sleep(random.uniform(0.6, 1.1))
        time.sleep(random.uniform(1.2, 2.0))

        # Capturar posts del DOM AHORA, mientras los articles de esta zona existen
        for dp in extraer_posts_dom(driver):
            if dp["url"] not in urls_vistas:
                urls_vistas.add(dp["url"])
                posts_acumulados.append(dp)
                print(f"      📌 Post capturado en ronda {i+1}: {dp['url'][-50:]}")

        try:
            bloques = driver.execute_script("return window.__fb_xhr_data || [];") or []
        except:
            bloques = []
        total_stories = sum(b.count('"__typename":"Story"') for b in bloques)

        try:
            total_articles = driver.execute_script(
                "return document.querySelectorAll('[role=\"article\"]').length;"
            ) or 0
        except:
            total_articles = 0

        total_combinado = total_stories + len(posts_acumulados)
        print(f"      Ronda {i+1}: {len(bloques)} bloques XHR | "
              f"{total_stories} Stories | {total_articles} articles DOM | "
              f"{len(posts_acumulados)} posts acumulados")

        if total_combinado == prev_total:
            sin_cambio += 1
            if sin_cambio >= 4:
                print("      ✋ Sin cambios. Deteniendo.")
                break
        else:
            sin_cambio = 0
        prev_total = total_combinado

    print(f"   📌 Total posts capturados durante scroll: {len(posts_acumulados)}")
    return posts_acumulados

# ==========================================
# DECODE
# ==========================================

def decode_fb_text(t):
    try:
        decoded = t.encode('utf-8').decode('unicode_escape', errors='surrogatepass')
        return decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
    except:
        try:
            return t.encode('raw_unicode_escape').decode('unicode_escape', errors='replace')
        except:
            return t

def limpiar_raw(raw):
    return re.sub(r'^\s*for\s*\(;;\);\s*', '', raw.strip())

# ==========================================
# PARSER DE STORIES (posts)
# ==========================================

def parsear_bloque_en_stories(raw):
    stories = []
    vistos  = set()
    partes  = re.split(r'"__typename"\s*:\s*"Story"', raw)

    for parte in partes[1:]:
        m = re.search(r'"wwwURL"\s*:\s*"([^"]+)"', parte)
        if not m:
            continue
        url = m.group(1).replace('\\/', '/')
        url_limpia = re.sub(r'\?(?!pfbid)[^"]*', '', url).rstrip('/')
        if url_limpia in vistos or len(url_limpia) < 25:
            continue
        vistos.add(url_limpia)

        fecha_dt = None; fecha_iso = ""
        t = re.search(r'"creation_time"\s*:\s*(\d{10})', parte)
        if t:
            try:
                fecha_dt  = datetime.fromtimestamp(int(t.group(1)))
                fecha_iso = fecha_dt.isoformat()
            except: pass

        likes = ""
        r = re.search(r'"reaction_count"\s*:\s*\{\s*"count"\s*:\s*(\d+)', parte)
        if r: likes = r.group(1)

        comments_count = ""
        c = re.search(r'"comments"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)', parte)
        if c: comments_count = c.group(1)

        caption = ""; caption_raw = ""
        mm = re.search(r'"message"\s*:\s*\{[^}]*"text"\s*:\s*"((?:[^"\\]|\\.)*)"', parte)
        if mm:
            caption_raw = mm.group(1)
            caption     = decode_fb_text(caption_raw)

        autor = ""
        a = re.search(r'"owning_profile"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', parte)
        if a: autor = decode_fb_text(a.group(1))

        # Filtrar posts que no son contenido real
        TIPOS_IGNORAR = [
            'StoryAttachmentCoverPhotoStyleRenderer',
            'StoryAttachmentProfileMediaStyleRenderer',
            'StoryAttachmentLifeEventStyleRenderer',
        ]
        if any(t in parte[:800] for t in TIPOS_IGNORAR):
            continue

        stories.append({
            "url": url_limpia, "fecha_dt": fecha_dt, "fecha_iso": fecha_iso,
            "likes": likes, "comments_count": comments_count,
            "caption": caption, "caption_raw": caption_raw, "autor": autor,
        })
    return stories

def extraer_pfbids_dom_html(html):
    """Posts que FB sirve como <a href> en DOM, no en scripts JSON. Común en amigos."""
    posts = []
    vistos = set()
    for url in re.findall(r'href="(https://www\.facebook\.com/[^"]+(?:pfbid|/posts/)[^"?#]+)"', html):
        base = re.sub(r'\?.*', '', url).rstrip('/')
        if base in vistos or len(base) < 30: continue
        if any(x in base for x in ['/photo/', '/video/', '/events/', '/groups/', '#']): continue
        # Validar que el pfbid no sea en realidad un user.id de cuenta logueada
        # (aparece en el menu "cambiar de cuenta", no es un post)
        pfbid_val = re.search(r'(pfbid[A-Za-z0-9]+)', base)
        if pfbid_val and f'"id":"{pfbid_val.group(1)}"' in html and 'SwitcherAccount' in html:
            continue
        vistos.add(base)
        posts.append({"url": base, "fecha_dt": None, "fecha_iso": "",
                      "likes": "", "comments_count": "", "caption": "",
                      "caption_raw": "", "autor": ""})
    return posts


def parsear_posts_de_fuentes(bloques_xhr, page_source, f_inicio_str, f_fin_str,
                              page_source_inicial=None):
    """
    Parsea posts de 4 fuentes en orden de prioridad:
    1. Scripts JSON del HTML inicial (antes del scroll) — captura lo que llega temprano
    2. Scripts JSON del HTML final (después del scroll)
    3. XHR capturado durante el scroll
    4. Links pfbid en el DOM — fallback para perfiles de amigos
    """
    vistos = set()
    posts  = []

    try:
        dt_inicio = datetime.fromisoformat(f_inicio_str + "T00:00:00")
        dt_fin    = datetime.fromisoformat(f_fin_str    + "T23:59:59")
    except:
        dt_inicio = dt_fin = None

    # Fuentes HTML
    htmls = []
    if page_source_inicial:
        htmls.append(page_source_inicial)
    if page_source and page_source != page_source_inicial:
        htmls.append(page_source)

    for html in htmls:
        for s in re.findall(r'<script[^>]+type=["\'"]application/json["\'"][^>]*>(.*?)</script>', html, re.DOTALL):
            if 'wwwURL' not in s and 'creation_time' not in s: continue
            for story in parsear_bloque_en_stories(s):
                if story["url"] not in vistos:
                    vistos.add(story["url"]); posts.append(story)

    # XHR del scroll
    for raw in bloques_xhr:
        if not raw: continue
        for story in parsear_bloque_en_stories(limpiar_raw(raw)):
            if story["url"] not in vistos:
                vistos.add(story["url"]); posts.append(story)

    # Fallback DOM — pfbid en HTML (perfiles de amigos)
    for story in extraer_pfbids_dom_html(page_source_inicial or page_source or ""):
        if story["url"] not in vistos:
            vistos.add(story["url"]); posts.append(story)
            print(f"   🔗 Post DOM fallback: {story['url'][-50:]}")

    # Filtrar por rango
    filtrados = []
    for s in posts:
        if dt_inicio and dt_fin and s["fecha_dt"]:
            if not (dt_inicio <= s["fecha_dt"] <= dt_fin): continue
        filtrados.append({
            "url":        s["url"],
            "fecha_post": s["fecha_iso"],
            "likes_text": s["likes"],
            "comments_text":  s["comments_count"],
            "username_post":  s["autor"],
            "fecha_text":     s["fecha_iso"][:10] if s["fecha_iso"] else "",
            "caption":        s["caption"],
            "caption_raw":    s["caption_raw"],
            "comments":       [],
        })

    return filtrados


def generar_author_id(autor, fb_id=""):
    """
    ID estable por autor — para detectar cuando la misma persona
    comenta/responde varias veces.
    Usa el ID real de FB si se logró capturar; si no, cae a un hash
    del nombre normalizado (mismo nombre => mismo ID en esta corrida).
    """
    if fb_id:
        return fb_id
    nombre_norm = (autor or "desconocido").strip().lower()
    return "h_" + hashlib.md5(nombre_norm.encode("utf-8")).hexdigest()[:10]

def parsear_comments_de_fuentes(bloques_xhr, page_source):
    """Extrae comments de XHR y del HTML estático."""
    comments = []
    vistos   = set()

    fuentes = bloques_xhr + re.findall(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        page_source, re.DOTALL
    )

    for raw in fuentes:
        if not raw or 'body' not in raw:
            continue
        raw = limpiar_raw(raw)
        ultimo_root_id = ""  # último comentario raíz visto en este bloque — sirve de padre para sus replies

        # Patron 1: "comment":{"body":{"text":"..."}} — HTML estatico
        # depth:0 = comment raiz, depth:1+ = reply
        segmentos = re.split(r'"comment"\s*:\s*\{', raw)
        for seg in segmentos[1:]:
            body_m = re.search(r'"body"\s*:\s*\{[^}]*"text"\s*:\s*"((?:[^"\\]|\\.){2,500})"', seg)
            if not body_m: continue
            texto = decode_fb_text(body_m.group(1)).strip()
            if not texto or texto in vistos or len(texto) < 2: continue
            depth_m = re.search(r'"depth"\s*:\s*(\d+)', seg[:400])
            depth = int(depth_m.group(1)) if depth_m else 0
            vistos.add(texto)
            autor = ""
            a_m = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', seg)
            if a_m: autor = decode_fb_text(a_m.group(1))
            autor_fbid = ""
            ai_m = re.search(r'"author"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"', seg)
            if ai_m: autor_fbid = ai_m.group(1)
            fecha_iso = ""
            t_m = re.search(r'"created_time"\s*:\s*(\d{10})', seg)
            if t_m:
                try: fecha_iso = datetime.fromtimestamp(int(t_m.group(1))).isoformat()
                except: pass
            comment_id        = f"cmt_{len(comments) + 1}"
            es_reply           = depth > 0
            parent_comment_id = ultimo_root_id if es_reply else ""
            if not es_reply:
                ultimo_root_id = comment_id
            cmt = {
                "comment_id": comment_id, "es_reply": es_reply,
                "autor": autor, "texto": texto, "fecha_iso": fecha_iso,
                "parent_comment_id": parent_comment_id,
                "author_id": generar_author_id(autor, autor_fbid),
            }
            comments.append(cmt)

        # Patron 2: __typename Comment — XHR al cargar mas comentarios
        partes = re.split(r'"__typename"\s*:\s*"Comment"', raw)
        for parte in partes[1:]:
            body_m = re.search(r'"body"\s*:\s*\{[^}]*"text"\s*:\s*"((?:[^"\\]|\\.){2,500})"', parte)
            if not body_m: continue
            texto = decode_fb_text(body_m.group(1)).strip()
            if not texto or texto in vistos: continue
            depth_m = re.search(r'"depth"\s*:\s*(\d+)', parte[:400])
            depth = int(depth_m.group(1)) if depth_m else 0
            vistos.add(texto)
            autor = ""
            a_m = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', parte)
            if a_m: autor = decode_fb_text(a_m.group(1))
            autor_fbid = ""
            ai_m = re.search(r'"author"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"', parte)
            if ai_m: autor_fbid = ai_m.group(1)
            fecha_iso = ""
            t_m = re.search(r'"created_time"\s*:\s*(\d{10})', parte)
            if t_m:
                try: fecha_iso = datetime.fromtimestamp(int(t_m.group(1))).isoformat()
                except: pass
            comment_id        = f"cmt_{len(comments) + 1}"
            es_reply           = depth > 0
            parent_comment_id = ultimo_root_id if es_reply else ""
            if not es_reply:
                ultimo_root_id = comment_id
            cmt = {
                "comment_id": comment_id, "es_reply": es_reply,
                "autor": autor, "texto": texto, "fecha_iso": fecha_iso,
                "parent_comment_id": parent_comment_id,
                "author_id": generar_author_id(autor, autor_fbid),
            }
            comments.append(cmt)

    return comments

# ==========================================
# EXTRACCIÓN DE COMMENTS POR POST
# ==========================================

def extraer_comments_post(driver, post, forzar=False):
    if not forzar and (not post.get("comments_text") or post["comments_text"] == "0"):
        return []

    print(f"      💬 Comments en: {post['url'][-50:]}")
    try:
        resetear_buffer(driver)
        driver.get(post["url"])
        pausa(3, 5)
        asegurar_interceptor(driver)

        # Scroll progresivo para cargar comentarios
        prev = -1
        for ronda in range(5):
            driver.execute_script("window.scrollBy(0, 700);")
            time.sleep(random.uniform(2.0, 3.0))

            # Click en "Ver más comentarios" si aparece
            try:
                for xpath in [
                    "//*[contains(text(),'Ver más comentarios')]",
                    "//*[contains(text(),'View more comments')]",
                    "//*[contains(text(),'Ver') and contains(text(),'comentario')]",
                ]:
                    botones = driver.find_elements(By.XPATH, xpath)
                    for btn in botones[:2]:
                        try: btn.click(); time.sleep(1.5)
                        except: pass
            except: pass

            total = driver.execute_script("return (window.__fb_xhr_data||[]).length;") or 0
            print(f"         ronda {ronda+1}: {total} bloques")
            if total == prev:
                break
            prev = total

        bloques  = driver.execute_script("return window.__fb_xhr_data || [];") or []
        src      = driver.page_source
        comments = parsear_comments_de_fuentes(bloques, src)
        print(f"         → {len(comments)} comments extraídos")
        return comments

    except Exception as e:
        print(f"      ⚠️ Error: {e}")
        return []

# ==========================================
# SEGUIDORES / PERFIL
# ==========================================

def parsear_seguidores_xhr(bloques):
    followers = ""; following = ""; profile_pic = ""
    for raw in bloques:
        if not raw: continue
        if not followers:
            m = re.search(r'"follower_count"\s*:\s*(\d+)', raw)
            if m: followers = m.group(1)
        if not following:
            m = re.search(r'"following_count"\s*:\s*(\d+)', raw)
            if m: following = m.group(1)
            if not following:
                m = re.search(r'"friend_count"\s*:\s*(\d+)', raw)
                if m: following = m.group(1)
        if not profile_pic:
            m = re.search(r'"profile_picture"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', raw)
            if m: profile_pic = m.group(1).replace('\\/', '/')
    return followers, following, profile_pic

def parsear_seguidores_html(page_source):
    """Busca follower_count en scripts JSON del HTML estático."""
    followers = ""; following = ""; profile_pic = ""
    scripts = re.findall(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        page_source, re.DOTALL
    )
    for s in scripts:
        if not followers:
            m = re.search(r'"follower_count"\s*:\s*(\d+)', s)
            if m: followers = m.group(1)
        if not following:
            m = re.search(r'"following_count"\s*:\s*(\d+)', s)
            if m: following = m.group(1)
            if not following:
                m = re.search(r'"friend_count"\s*:\s*(\d+)', s)
                if m: following = m.group(1)
        if not profile_pic:
            m = re.search(r'"profile_picture"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', s)
            if m: profile_pic = m.group(1).replace('\\/', '/')
        if followers and following and profile_pic:
            break
    return followers, following, profile_pic

def extraer_seguidores_dom(driver):
    followers = ""; following = ""
    for xpath in [
        "//a[contains(@href,'followers')]//span[contains(text(),'seguidor') or contains(text(),'follower')]",
        "//*[contains(text(),'seguidores') or contains(text(),'followers')]",
    ]:
        try:
            txt = driver.find_element(By.XPATH, xpath).text.strip()
            if txt: followers = txt; break
        except: pass
    for xpath in [
        "//a[contains(@href,'friends')]//span",
        "//*[contains(text(),'amigos') or contains(text(),'friends')]",
    ]:
        try:
            txt = driver.find_element(By.XPATH, xpath).text.strip()
            if txt and ("amigo" in txt.lower() or "friend" in txt.lower() or txt[0].isdigit()):
                following = txt; break
        except: pass
    return followers, following

def extraer_nombre_dom(driver):
    try: return driver.find_element(By.TAG_NAME, "h1").text.strip()
    except: return ""

def extraer_profile_pic_dom(driver):
    try:
        el = driver.find_element(By.XPATH, "//svg//image")
        return el.get_attribute("xlink:href") or ""
    except:
        try:
            img = driver.find_element(By.XPATH,
                "//img[contains(@alt,'foto de perfil') or contains(@alt,'profile picture')]")
            return img.get_attribute("src") or ""
        except: return ""

# ==========================================
# FALLBACK DOM POSTS
# ==========================================

def extraer_posts_dom(driver):
    posts = []; vistos = set()
    links = driver.find_elements(By.XPATH,
        "//a[contains(@href,'/posts/') or contains(@href,'pfbid') or contains(@href,'permalink')]")
    for a in links:
        href = (a.get_attribute("href") or "").split("?")[0].rstrip("/")
        if not href or href in vistos or len(href) < 20: continue
        if not any(k in href for k in ["/posts/", "pfbid", "permalink"]): continue
        # Excluir links del menu de cambio de cuenta (account switcher) — no son posts
        try:
            aria = (a.get_attribute("aria-label") or "").lower()
        except:
            aria = ""
        if "cambiar" in aria or "switch" in aria or "cuenta" in aria:
            continue
        vistos.add(href)

        caption        = ""
        likes_text     = ""
        comments_text  = ""
        try:
            contenedor = a
            # Subir hasta el contenedor completo del post (suele ser un <div role="article">)
            article = None
            for _ in range(10):
                contenedor = contenedor.find_element(By.XPATH, "..")
                rol = contenedor.get_attribute("role") or ""
                if rol == "article":
                    article = contenedor
                    break
                if len(contenedor.text.strip()) > 30 and not caption:
                    caption = contenedor.text.strip()[:500]
            objetivo = article or contenedor

            # Likes — aria-label tipo "A Juan y 5 personas más les gusta esto"
            # o el numero suelto junto al boton de reacciones
            try:
                like_el = objetivo.find_element(By.XPATH,
                    ".//span[contains(@aria-label,'le gusta') or contains(@aria-label,'reaccion') "
                    "or contains(@aria-label,'like') or contains(@aria-label,'reaction')]")
                txt = like_el.get_attribute("aria-label") or like_el.text
                m = re.search(r'(\d[\d,.]*)', txt)
                if m: likes_text = m.group(1)
            except: pass

            # Comments — el numero junto a "comentarios" / "comments"
            try:
                com_el = objetivo.find_element(By.XPATH,
                    ".//span[contains(text(),'comentario') or contains(text(),'comment')]")
                m = re.search(r'(\d[\d,.]*)', com_el.text)
                if m: comments_text = m.group(1)
            except: pass

            if not comments_text:
                # Fallback: buscar patron "N comentarios" en todo el texto del contenedor
                m = re.search(r'(\d+)\s*comentario', objetivo.text)
                if m: comments_text = m.group(1)
        except: pass

        posts.append({
            "url": href, "fecha_post": "", "likes_text": likes_text,
            "comments_text": comments_text, "username_post": "", "fecha_text": "",
            "caption": caption, "caption_raw": "", "comments": [],
        })

    # Filtrar posts que son eventos de vida (cumpleaños, fecha de nacimiento, etc.)
    # — su caption es solo "Nombre\nDD de mes de AAAA\n· ·" sin contenido real
    PATRON_EVENTO_VIDA = re.compile(
        r'^\s*[\wÁÉÍÓÚáéíóúÑñ ]+\n\d{1,2} de \w+ de \d{4}\s*\n?\s*[·.\s]*$'
    )
    posts_filtrados = []
    for p in posts:
        cap = p.get("caption", "").strip()
        if PATRON_EVENTO_VIDA.match(cap):
            continue
        # También descartar si el caption es solo nombre + fecha sin nada más (heuristica laxa)
        lineas = [l.strip() for l in cap.split("\n") if l.strip()]
        if len(lineas) <= 2 and re.search(r'\d{1,2} de \w+ de \d{4}', cap):
            continue
        posts_filtrados.append(p)

    return posts_filtrados

# ==========================================
# DEBUG
# ==========================================

def guardar_debug(usuario, bloques, page_source):
    try:
        with open(f"debug_xhr_{usuario}.json", "w", encoding="utf-8") as f:
            json.dump(bloques, f, ensure_ascii=False, indent=2)
        with open(f"debug_html_{usuario}.html", "w", encoding="utf-8") as f:
            f.write(page_source)
        print(f"   🔍 Debug: debug_xhr_{usuario}.json")
    except Exception as e:
        print(f"   ⚠️ Debug error: {e}")

# ==========================================
# ORQUESTADOR
# ==========================================

def procesar_perfil(driver, info):
    usuario  = info["usuario"].strip()
    url_base = f"https://www.facebook.com/{usuario}"
    print(f"\n{'='*55}")
    print(f"🔍 Procesando: {url_base}")

    # Paso 1: ir a blank para que CDP quede registrado limpio antes de FB
    # AdsPower no propaga el CDP entre perfiles — sin esto las primeras queries escapan
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                               {"source": INTERCEPTOR_JS})
    except: pass

    try:
        driver.get("about:blank")
        time.sleep(0.8)
    except: pass
    resetear_buffer(driver)

    # Paso 2: navegar al perfil — interceptor ya activo desde la primera request
    driver.get(url_base)
    pausa(7, 11)
    asegurar_interceptor(driver)  # refuerzo por si contexto se reinició

    # Capturar HTML inicial ANTES del scroll
    page_source_inicial = driver.page_source
    bloques_iniciales = driver.execute_script("return (window.__fb_xhr_data||[]).length;") or 0
    print(f"   📄 HTML inicial: {len(page_source_inicial)} chars | XHR ya: {bloques_iniciales}")

    posts_dom_scroll = scroll_humano(driver)

    try:
        bloques_xhr = driver.execute_script("return window.__fb_xhr_data || [];") or []
    except:
        bloques_xhr = []

    # Si XHR sigue vacío, hacer segundo intento — en la segunda carga
    # el interceptor ya está en el contexto activo y sí captura las queries
    if len(bloques_xhr) == 0:
        print("   🔄 XHR vacío — segundo intento con interceptor activo...")
        resetear_buffer(driver)
        driver.get(url_base)
        pausa(6, 9)
        asegurar_interceptor(driver)
        posts_dom_scroll_2 = scroll_humano(driver)
        urls_ya_acumuladas = {p["url"] for p in posts_dom_scroll}
        for dp in posts_dom_scroll_2:
            if dp["url"] not in urls_ya_acumuladas:
                posts_dom_scroll.append(dp)
        try:
            bloques_xhr = driver.execute_script("return window.__fb_xhr_data || [];") or []
        except:
            bloques_xhr = []

    page_source = driver.page_source
    print(f"   🌐 Bloques XHR: {len(bloques_xhr)}")

    # DEBUG — quitar cuando funcione todo
    guardar_debug(usuario, bloques_xhr, page_source_inicial)

    # Seguidores — XHR + HTML estático + DOM
    followers, following, profile_pic = parsear_seguidores_xhr(bloques_xhr)
    if not followers or not following or not profile_pic:
        f2, fw2, pp2 = parsear_seguidores_html(page_source)
        followers   = followers   or f2
        following   = following   or fw2
        profile_pic = profile_pic or pp2
    if not followers or not following:
        f_dom, fw_dom = extraer_seguidores_dom(driver)
        followers  = followers  or f_dom
        following  = following  or fw_dom
    if not profile_pic:
        profile_pic = extraer_profile_pic_dom(driver)

    nombre = extraer_nombre_dom(driver)

    # Posts — XHR + HTML inicial + HTML final
    posts = parsear_posts_de_fuentes(bloques_xhr, page_source,
                                     info["f_inicio"], info["f_fin"],
                                     page_source_inicial=page_source_inicial)
    print(f"   📦 Posts (parsers): {len(posts)}")

    # DOM supplement — usa los posts capturados EN VIVO durante scroll_humano.
    # FB usa virtualizacion (windowing): los <article> que salen de pantalla
    # se destruyen del DOM. Leer el DOM despues de terminar el scroll es tarde;
    # por eso la captura real ocurre dentro de scroll_humano, ronda por ronda.
    urls_ya = {p["url"] for p in posts}
    for dp in posts_dom_scroll:
        if dp["url"] not in urls_ya:
            dp["_origen_dom_suplemento"] = True
            posts.append(dp)
            urls_ya.add(dp["url"])
            print(f"   Post DOM suplemento: {dp['url'][-50:]} (likes:{dp.get('likes_text')} comments:{dp.get('comments_text')})")
    print(f"   📦 Posts total: {len(posts)}")

    # Comments — posts con comments_text>0, o posts del DOM suplemento (su conteo
    # del DOM puede fallar aunque sí tengan comentarios reales, mejor intentar)
    posts_con_comments = [
        p for p in posts
        if p.get("comments_text", "0") not in ("", "0") or p.get("_origen_dom_suplemento")
    ]
    if posts_con_comments:
        print(f"   💬 Posts a revisar comments: {len(posts_con_comments)}")
        for post in posts_con_comments:
            comments = extraer_comments_post(driver, post, forzar=post.get("_origen_dom_suplemento", False))
            if comments:
                post["comments"] = comments
                if not post.get("comments_text"):
                    post["comments_text"] = str(len(comments))
            pausa(2, 4)
        # Volver al feed
        driver.get(url_base)
        pausa(3, 5)

    print(f"   👤 {nombre} | Followers: {followers} | Posts: {len(posts)}")

    return {
        "id":           info["id"],
        "usuario":      usuario,
        "url":          url_base,
        "f_inicio":     info["f_inicio"],
        "f_fin":        info["f_fin"],
        "username":     usuario,
        "nombre":       nombre,
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
# CSV
# ==========================================

def cargar_csv(ruta):
    usuarios = []
    try:
        with open(ruta, mode="r", encoding="utf-8-sig") as f:
            for fila in csv.DictReader(f):
                fb = fila.get("facebook", "").strip()
                if not fb: continue
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
# ANONIMIZACIÓN
# ==========================================

def anonimizar_resultados(resultados):
    """
    Genera una versión anonimizada lista para análisis de texto/emociones.

    Se elimina:  nombres reales, URLs de perfil/posts, foto de perfil,
                 usuario/username, author_id de FB.
    Se conserva: textos (caption, comentarios), fechas, métricas de engagement,
                 jerarquía padre-hijo de comentarios, rango de fechas.

    Consistencia cross-post: si la misma persona (detectada por su FB ID real
    o por nombre normalizado como fallback) aparece en varios posts/comentarios,
    recibe el mismo autor_anonimo en todo el JSON anonimizado.
    """
    import string

    mapa_autores  = {}   # clave_real → "autor_A", "autor_B", ...
    autor_counter = [0]

    def id_autor_anon(author_id_real, nombre):
        """Devuelve un ID anónimo estable por persona."""
        clave = (author_id_real or "").strip() or (nombre or "").strip().lower() or "_desconocido"
        if clave not in mapa_autores:
            # Secuencia A, B, …, Z, AA, AB, …
            n = autor_counter[0]
            letras = ""
            idx = n
            while True:
                letras = string.ascii_uppercase[idx % 26] + letras
                idx = idx // 26 - 1
                if idx < 0:
                    break
            mapa_autores[clave] = f"autor_{letras}"
            autor_counter[0] += 1
        return mapa_autores[clave]

    resultados_anon = []
    for i, perfil in enumerate(resultados, 1):
        posts_anon = []
        for j, post in enumerate(perfil.get("posts_data", []), 1):
            comments_anon = []
            for cmt in post.get("comments", []):
                autor_anon = id_autor_anon(
                    cmt.get("author_id", ""),
                    cmt.get("autor", "")
                )
                comments_anon.append({
                    "comment_id":       cmt.get("comment_id", ""),
                    "es_reply":         cmt.get("es_reply", False),
                    "autor_anonimo":    autor_anon,
                    "texto":            cmt.get("texto", ""),
                    "fecha_iso":        cmt.get("fecha_iso", ""),
                    "parent_comment_id": cmt.get("parent_comment_id", ""),
                })
            posts_anon.append({
                "post_id":       f"post_{j}",
                "fecha_post":    post.get("fecha_post", ""),
                "likes_text":    post.get("likes_text", ""),
                "comments_text": post.get("comments_text", ""),
                "fecha_text":    post.get("fecha_text", ""),
                "caption":       post.get("caption", ""),
                "comments":      comments_anon,
            })

        resultados_anon.append({
            "id":               perfil.get("id", ""),
            "perfil_id":        f"perfil_{i}",
            "followers":        perfil.get("followers", ""),
            "following":        perfil.get("following", ""),
            "posts":            perfil.get("posts", ""),
            "rango_fechas":     perfil.get("rango_fechas", {}),
            "posts_data":       posts_anon,
            "fecha_extraccion": perfil.get("fecha_extraccion", ""),
        })

    return {"resultados_anonimizados": resultados_anon}


# ==========================================
# MAIN
# ==========================================

def main():
    print("=== FB SCRAPER ===")
    usuarios = cargar_csv(CSV_PATH)
    if not usuarios:
        print("❌ Sin usuarios con columna 'facebook' en CSV.")
        return
    print(f"📋 Usuarios: {len(usuarios)}")

    driver = iniciar_adspower()
    if not driver: return

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
                    "error": str(e), "posts_data": []
                })
            pausa(15, 25)
    except KeyboardInterrupt:
        print("\n🛑 Interrumpido.")
    finally:
        os.makedirs(CARPETA_OUTPUT, exist_ok=True)
        with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
            json.dump({"resultados": resultados}, f, indent=2, ensure_ascii=False)
        print(f"\n📂 Guardado: {JSON_OUTPUT}")
        datos_anon = anonimizar_resultados(resultados)
        with open(JSON_ANON, "w", encoding="utf-8") as f:
            json.dump(datos_anon, f, indent=2, ensure_ascii=False)
        print(f"🔒 Anonimizado: {JSON_ANON}")

if __name__ == "__main__":
    main()