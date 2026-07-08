import re, csv, json, time, os
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# CONFIG
CSV_PATH, RESULTADOS_DIR = "formato(Sheet1).csv", "resultados_ig"
MAX_INTENTOS_SIN_CAMBIO, ESPERA_LOGIN, ESPERA_CAPTION = 3, 15, 4
# Mientras no se resuelva del todo la extracción de respuestas: guarda en
# resultados_ig/debug_dom/ el HTML de cada post YA con las respuestas
# expandidas, para poder diagnosticar contra el DOM real de Instagram
# (los selectores no se pueden validar a ciegas sin verlo). Poner en False
# una vez que las respuestas salgan bien y ya no se necesite depurar.
DEBUG_GUARDAR_DOM = True

# UTILIDADES
def save_json(data, filename):
    os.makedirs(RESULTADOS_DIR, exist_ok=True)
    ruta = os.path.join(RESULTADOS_DIR, f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Guardado: {ruta}")
    return ruta

def limpiar_nombre_archivo(t):
    return re.sub(r'[\\/*?:"<>|\s]', "_", str(t).strip()) if t else "sin_nombre"

def _walk_json(obj):
    """
    Recorre recursivamente cualquier estructura JSON (dict/list) y devuelve
    cada dict encontrado, sin importar la profundidad ni el orden de las
    claves internas. Usado para leer los datos de IG embebidos en <script
    type="application/json">, que son JSON válido pero con estructura y
    orden de campos que varía entre posts.
    """
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json(v)


def _extraer_shortcode(url):
    m = re.search(r"/(?:p|reel)/([^/?]+)", url or "")
    return m.group(1) if m else None


def obtener_fecha_post(driver, url_post=None):
    """
    Extrae la fecha real de publicación leyendo el campo 'taken_at'
    (timestamp Unix) del JSON embebido en <script type="application/json">.
    NO se usa el <time> del DOM como fuente principal: la página tiene un
    <time> por cada comentario visible, y el primero en orden de documento
    no siempre es el del post — eso hacía que a veces se tomara la fecha de
    un comentario en vez de la del post, descuadrando el orden final.
    """
    shortcode = _extraer_shortcode(url_post)
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        candidatos = []
        for script in soup.find_all("script", attrs={"type": "application/json"}):
            try:
                data = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            for nodo in _walk_json(data):
                ta = nodo.get("taken_at")
                code = nodo.get("code")
                if isinstance(ta, (int, float)) and isinstance(code, str):
                    candidatos.append((code, ta))
        if candidatos:
            if shortcode:
                for code, ta in candidatos:
                    if code == shortcode:
                        return datetime.fromtimestamp(ta)
            # Si no hay match exacto de shortcode, usar el primero encontrado
            return datetime.fromtimestamp(candidatos[0][1])
    except Exception:
        pass

    # Fallback: <time> restringido al header del article (evita <time> de comentarios)
    try:
        t = driver.find_element(By.XPATH, "//article//header//time | //article//a[time]/time")
        dt = t.get_attribute("datetime") or ""
        if dt and len(dt) >= 10:
            return datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    return None

def construir_resultado_anonimo(res):
    """
    Anonimiza comentarios manteniendo jerarquia (comment_id/parent_comment_id).
    El mapa de autor->anonimo es GLOBAL para todo el perfil (todos sus posts),
    asi que si la misma persona comenta en varios posts, recibe la misma
    etiqueta "Anónimo N" en todos ellos.
    """
    mapa_anonimos = {}
    contador_anon = 1

    def anon_de(autor_real):
        nonlocal contador_anon
        clave = (autor_real or "").strip().lower() or "_desconocido"
        if clave not in mapa_anonimos:
            mapa_anonimos[clave] = f"Anónimo {contador_anon}"
            contador_anon += 1
        return mapa_anonimos[clave]

    posts_anonimizados = []
    for p in res.get("posts_data", []):
        originales = p.get("comments", [])
        comentarios_anonimos = []

        for cmt in originales:
            # Compatibilidad: si por algún motivo llega un string suelto
            # (formato viejo), lo dejamos pasar tal cual sin romper.
            if isinstance(cmt, str):
                comentarios_anonimos.append(cmt)
                continue

            comentarios_anonimos.append({
                "comment_id":        cmt.get("comment_id"),
                "es_reply":          cmt.get("es_reply", False),
                "parent_comment_id": cmt.get("parent_comment_id", ""),
                "autor_anonimo":     anon_de(cmt.get("autor")),
                "texto":             cmt.get("texto"),
            })

        p_anon = p.copy()
        p_anon["comments"] = comentarios_anonimos
        posts_anonimizados.append(p_anon)

    return {
        "id": res.get("id"),
        "paciente": str(res.get("id")),
        "followers": res.get("followers"),
        "following": res.get("following"),
        "posts": res.get("posts"),
        "rango_fechas": res.get("rango_fechas"),
        "posts_data": posts_anonimizados,
        **({"error": res["error"]} if "error" in res else {})
    }

# LOGIN / CSV
def iniciar_sesion(driver, wait):
    driver.get("https://www.instagram.com/accounts/login/")
    # RESTAURADO: Nombres originales de los campos
    wait.until(EC.presence_of_element_located((By.NAME, "email"))).send_keys("mindtrack_test")
    pw = wait.until(EC.presence_of_element_located((By.NAME, "pass")))
    pw.send_keys("Prueba26" + Keys.RETURN)
    time.sleep(5)
    try: wait.until(EC.element_to_be_clickable((By.XPATH, "//*[text()='Ahora no']"))).click()
    except: pass

def limpiar_usuario(v):
    if not v: return None
    v = re.sub(r'https?://(www\.)?instagram\.com/|@', '', str(v).strip()).strip('/')
    return v if v else None

def cargar_usuarios(ruta):
    usuarios = []
    with open(ruta, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            u = limpiar_usuario(r.get("instagram"))
            if not u: continue
            
            try:
                f_i = datetime.strptime(r.get("F. Inicio", "").strip(), "%Y-%m-%d")
                f_f = datetime.strptime(r.get("F. Fin", "").strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                
                usuarios.append({
                    "id": r.get("id"), 
                    "usuario": u, 
                    "url": f"https://www.instagram.com/{u}/",
                    "f_inicio": f_i,
                    "f_fin": f_f
                })
            except:
                print(f"Error en formato de fecha para usuario: {u}")
    return usuarios

# SCRAPING LOGIC
def hacer_scroll(driver):
    last_h, attempts = driver.execute_script("return document.body.scrollHeight"), 0
    while attempts < MAX_INTENTOS_SIN_CAMBIO:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2.5)
        new_h = driver.execute_script("return document.body.scrollHeight")
        attempts, last_h = (attempts + 1, new_h) if new_h == last_h else (0, new_h)

def parsear_html_perfil(html_path):
    with open(html_path, "r", encoding="utf-8") as f: soup = BeautifulSoup(f.read(), "html.parser")
    get_meta = lambda p: (tag.get("content") if (tag := soup.find("meta", attrs={"property": p})) else None)
    title, desc = get_meta("og:title"), get_meta("og:description")
    
    m_t = re.search(r"^(.*?)\s+\(@(.*?)\)", title) if title else None
    # IG a veces devuelve el og:description en ingles (Followers/Following/Posts)
    # en vez de español (seguidores/seguidos/publicaciones) segun la cuenta o
    # la sesion — la regex anterior exigia literalmente las palabras en
    # español y por eso followers/following/posts quedaban en None cuando IG
    # respondia en ingles. Ahora se capturan solo los NUMEROS de cada uno de
    # los primeros 3 segmentos separados por coma, sin importar el idioma.
    m_d = re.search(
        r"([\d.,]+\s*[kKmM]?)\s+[^,]+,\s*([\d.,]+\s*[kKmM]?)\s+[^,]+,\s*([\d.,]+\s*[kKmM]?)\s+[^,]+",
        desc
    ) if desc else None
    
    links_raw = ["https://www.instagram.com" + a["href"].split("?")[0] if a["href"].startswith("/") else a["href"].split("?")[0]
                 for a in soup.find_all("a", href=True) if "/p/" in a["href"] or "/reel/" in a["href"]]
    links = list(dict.fromkeys(links_raw))  # dedup preservando orden de aparición
    
    return {
        "username": m_t.group(2) if m_t else None, "nombre": m_t.group(1) if m_t else None,
        "followers": m_d.group(1) if m_d else None, "following": m_d.group(2) if m_d else None,
        "posts": m_d.group(3) if m_d else None, "profile_pic": get_meta("og:image"), "links_posts": links
    }

def extraer_caption_post(driver):
    """
    Antes, si el DOM encontraba un texto >=15 caracteres (típico cuando SÍ
    hay caption), la función retornaba de inmediato forzando likes_text,
    comments_text, username_post y fecha_text a None — nunca llegaba a leer
    el og:description que sí los tenía. Y cuando NO había caption, el
    og:description no trae el bloque ': "caption"' final, así que la regex
    completa (que exigía ese bloque para matchear) fallaba entera y también
    dejaba likes/comments en None, aunque el texto sí los incluyera.
    Ahora likes/comments/username/fecha y caption se extraen con dos
    regex independientes sobre el mismo og:description: una cubre el
    encabezado ("X likes, Y comments - usuario el FECHA"), que siempre está
    presente tenga o no caption, y la otra solo el caption entre comillas,
    que puede faltar sin afectar a la primera.
    """
    caption_dom = None
    for xpath in ["//article//h1", "//article//ul//span", "//article//div//span"]:
        try:
            texts = [el.text.strip() for el in driver.find_elements(By.XPATH, xpath) if len(el.text.strip()) >= 15]
            if texts:
                caption_dom = texts[0]
                break
        except:
            pass

    try:
        desc = driver.find_element(By.XPATH, "//meta[@property='og:description']").get_attribute("content")
        desc = re.sub(r"\s+", " ", desc)
        m_head = re.search(r"^(.*?),\s*(.*?)\s*-\s*(.*?)\s+el\s+([^:.]*)", desc)
        m_cap = re.search(r'"(.*)"\s*\.?\s*$', desc)
        return {
            "likes_text":    m_head.group(1).strip() if m_head else None,
            "comments_text": m_head.group(2).strip() if m_head else None,
            "username_post": m_head.group(3).strip() if m_head else None,
            "fecha_text":    m_head.group(4).strip() if m_head else None,
            "caption":       caption_dom or (m_cap.group(1) if m_cap else None),
            "caption_raw":   desc,
        }
    except:
        return {
            "likes_text": None, "comments_text": None, "username_post": None,
            "fecha_text": None, "caption": caption_dom, "caption_raw": None,
        }

def expandir_respuestas(driver):
    """
    Hace clic en todos los botones 'Ver las X respuestas' del post actual.
    NO hace scroll — el scroll de página entera saca al scraper del post de IG.
    Usa execute_script click para evitar problemas de elementos fuera del viewport.
    """
    try:
        intentos = 0
        while intentos < 8:
            botones = driver.find_elements(
                By.XPATH,
                "//span[starts-with(normalize-space(.), 'Ver las') and "
                "contains(normalize-space(.), 'respuesta')]"
                " | //span[starts-with(normalize-space(.), 'Ver') and "
                "contains(normalize-space(.), 'respues')]"
            )
            visibles = [b for b in botones if b.is_displayed()]
            if not visibles:
                break
            clicks = 0
            for b in visibles:
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", b)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", b)
                    clicks += 1
                    time.sleep(1.0)
                except:
                    pass
            if clicks == 0:
                break
            intentos += 1
            time.sleep(1.0)
    except Exception as e:
        print(f"Aviso expandiendo respuestas: {e}")


def parsear_comentarios_html(page_source):
    """
    Extrae comentarios del JSON embebido en <script type="application/json">
    del HTML del post (IG los guarda ahí como JSON válido, no como texto
    suelto). Antes esto se hacía con una regex que buscaba "username" -> "pk"
    -> "text" -> "parent_comment_id" en ese orden y a una distancia máxima de
    caracteres: si el orden de las claves cambiaba (algo muy común en
    replies, que traen campos extra como "child_comment_count") o el texto
    superaba el límite de caracteres esperado, la regex simplemente no
    matcheaba y esa respuesta se perdía en silencio.
    Ahora se usa json.loads() + recorrido recursivo (_walk_json), que
    encuentra cada nodo-comentario sin importar el orden de sus claves ni la
    profundidad de anidamiento. Como bonus, json.loads ya decodifica
    correctamente los \\uXXXX de IG (emojis, tildes, etc.), así que ya no
    hace falta ningún hack manual de doble-decodificación.
    """
    soup = BeautifulSoup(page_source, "html.parser")

    vistos_pk      = set()
    comentarios_raw = []

    for script in soup.find_all("script", attrs={"type": "application/json"}):
        try:
            data = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue

        for nodo in _walk_json(data):
            user = nodo.get("user")
            # Un comentario real de IG siempre trae user.username + pk + text
            # + created_at + comment_like_count. Antes solo se exigían los
            # primeros 3, y eso hacía match con algún nodo que NO es un
            # comentario (ej. contexto del usuario logueado) — se detectó
            # en un post que en realidad tiene 0 comentarios ("Todavía no
            # hay comentarios") pero el script igual reportaba 1.
            if not (
                isinstance(user, dict) and "username" in user
                and "pk" in nodo and "text" in nodo
                and "created_at" in nodo and "comment_like_count" in nodo
            ):
                continue

            pk = str(nodo["pk"])
            if pk in vistos_pk:
                continue
            vistos_pk.add(pk)

            texto = nodo.get("text") or ""
            parent = nodo.get("parent_comment_id")
            comentarios_raw.append({
                "username": user["username"],
                "pk":       pk,
                "text":     texto.strip() if texto.strip() else "[imagen/gif]",
                "parent":   str(parent) if parent not in (None, "", "null") else None,
            })

    return comentarios_raw


def _extraer_replies_dom(driver):
    """
    Extrae las RESPUESTAS (replies) a comentarios directamente del DOM ya
    renderizado, después de expandir_respuestas().

    Por qué el método anterior (parsear_comentarios_html sobre el script
    JSON) nunca las capturaba: ese <script type="application/json"> es el
    payload estático que Instagram genera al cargar la página (SSR) y solo
    trae los comentarios raíz visibles de entrada. Al hacer clic en 'Ver
    respuestas', IG pide las replies por AJAX y solo actualiza la vista (el
    DOM real que ve el usuario) — nunca vuelve a escribir esos datos en el
    script original.

    IMPORTANTE (corregido tras revisar un HTML real): un post sin ningún
    comentario ("Todavía no hay comentarios") reportaba igual 1 "respuesta"
    falsa, porque la heurística de respaldo aceptaba cualquier bloque con 2+
    enlaces de usuario — y esos existen en cualquier post (avatares de "Me
    gusta", cuentas sugeridas, nav). Ahora TODO bloque candidato debe
    contener además la palabra "Responder"/"Reply" (el botón que Instagram
    pone en cada comentario/respuesta real); si esa palabra no aparece en
    ningún lado del HTML, no hay comentarios reales que procesar y la
    función corta de inmediato.

    Se usan DOS estrategias, en este orden, ambas exigiendo "Responder":
    A) Mención visible: se busca el símbolo "@" ya sea DENTRO del enlace de
       usuario (<a>@fulano</a>) o como texto suelto justo antes del enlace
       (fulano @<a>fulano</a>), porque no sabemos con certeza cuál de las
       dos usa Instagram sin ver el HTML real.
    B) Respaldo estructural: si (A) no encuentra nada, se asume que todo
       bloque de comentario con 2 o más enlaces de usuario propios (sin
       contar los que están dentro de una sub-lista anidada) Y que contenga
       "Responder"/"Reply" en su texto, es una respuesta.
    """
    soup = BeautifulSoup(driver.page_source, "html.parser")
    patron_usuario_raw = re.compile(r"^/[A-Za-z0-9_.]{1,30}/$")
    patron_responder = re.compile(r"\bResponder\b|\bReply\b")
    replies, vistos = [], set()

    # Rutas reservadas de Instagram que matchean el mismo patrón que un
    # username (ej. /reels/) pero NO son cuentas de usuario. Se detectó que
    # "/reels/" (el ícono de navegación) se colaba como si fuera un autor.
    RUTAS_RESERVADAS = {
        "reels", "reel", "explore", "accounts", "direct", "stories", "tv",
        "p", "legal", "about", "developer", "challenge", "emails",
        "session", "graphql", "api", "directory", "hashtag", "location",
        "tags", "ads", "lite", "web", "settings", "create",
    }

    def patron_usuario(href):
        m = patron_usuario_raw.match(href)
        if not m:
            return False
        return href.strip("/").lower() not in RUTAS_RESERVADAS

    def _enlaces_usuario(tag):
        return [a for a in tag.find_all("a", href=True) if patron_usuario(a["href"])]

    if not patron_responder.search(soup.get_text(" ")):
        # No hay ni un solo botón "Responder"/"Reply" en toda la página =>
        # el post no tiene comentarios reales (o aún no cargaron). Evita
        # que cualquier heurística busque "respuestas" donde no las hay.
        print("    [debug respuestas] no se encontró 'Responder/Reply' en la página — no hay comentarios reales")
        return []

    def _limpiar_texto(bloque, autor, parent_username):
        texto = bloque.get_text(" ", strip=True)
        texto = re.sub(rf"^{re.escape(autor)}\s*", "", texto, count=1)
        # quitar el sello de tiempo que IG pone entre el autor y el texto (ej. "5 sem")
        texto = re.sub(r"^\d+\s*(?:sem|semanas?|w|d|días?|h|horas?|min)\b\s*", "", texto, count=1)
        texto = re.sub(rf"^@?{re.escape(parent_username)}\s*", "", texto, count=1).strip()
        # quitar el contador de "me gusta" (ej. "milagros 1 Me gusta" -> "milagros Me gusta")
        texto = re.sub(r"\s+\d+\s+(?=Me gusta\b|Like\b|Responder\b|Reply\b)", " ", texto)
        texto = re.split(r"\s+(?:\d+\s*(?:sem|w|semanas?|d|días?|h|horas?|min)\b|Responder|Reply|Me gusta|Like)\b", texto)[0]
        return texto.strip()

    def _agregar(autor, parent_username, bloque):
        if autor == parent_username:
            return
        texto_bloque = bloque.get_text(" ", strip=True)
        if len(texto_bloque) > 400:
            # Un comentario/respuesta real de IG es corto. Un bloque tan
            # largo significa que el ascenso por ancestros se pasó de nivel
            # y fusionó el comentario con otra sección de la página (nav,
            # personas etiquetadas en la foto, cuentas sugeridas, etc.) —
            # se detectó justo así con el enlace de navegación "/reels/".
            return
        if not patron_responder.search(texto_bloque):
            return  # el bloque candidato no tiene botón Responder/Reply -> no es un comentario
        texto = _limpiar_texto(bloque, autor, parent_username)
        if not texto:
            return
        clave = (autor, parent_username, texto)
        if clave in vistos:
            return
        vistos.add(clave)
        replies.append({"autor": autor, "texto": texto, "parent_username": parent_username})

    # --- Estrategia A: mención visible con "@" ---
    # En el DOM real de Instagram el enlace del AUTOR y el enlace de la
    # MENCIÓN casi nunca están en el mismo <div> inmediato (hay ~4 niveles
    # de <div> intermedios por las clases atómicas de Meta). Por eso ya no
    # se usa find_parent (1 solo nivel): se sube por los ancestros hasta
    # encontrar el primer nivel que contenga AMBOS enlaces de usuario Y la
    # palabra "Responder"/"Reply" — ese es el contenedor real de ese
    # comentario/respuesta, sin importar cuántos <div> de por medio haya.
    for enlace in soup.find_all("a", href=patron_usuario):
        texto_enlace = enlace.get_text(strip=True)
        antes = enlace.previous_sibling
        texto_antes = antes.strip() if isinstance(antes, str) else ""
        es_mencion = texto_enlace.startswith("@") or texto_antes.endswith("@")
        if not es_mencion:
            continue

        bloque = None
        nivel = enlace.parent
        for _ in range(10):  # tope de seguridad para no subir hasta la raíz del documento
            if nivel is None:
                break
            hrefs_aqui = {a["href"] for a in nivel.find_all("a", href=patron_usuario)}
            if len(hrefs_aqui) >= 2 and patron_responder.search(nivel.get_text(" ")):
                bloque = nivel
                break
            nivel = nivel.parent
        if not bloque:
            continue

        enlaces = bloque.find_all("a", href=patron_usuario)
        autor_candidatos = [a for a in enlaces if a["href"] != enlace["href"]]
        if not autor_candidatos:
            continue

        autor = autor_candidatos[0]["href"].strip("/")
        parent_username = enlace["href"].strip("/")
        _agregar(autor, parent_username, bloque)

    # --- Estrategia B (respaldo): 2+ enlaces de usuario propios en el bloque ---
    if not replies:
        candidatos = soup.find_all("li") or soup.find_all("div")
        for bloque in candidatos:
            sub_listas = bloque.find_all(["ul", "ol"])
            enlaces_anidados = {a for sub in sub_listas for a in sub.find_all("a", href=patron_usuario)}
            enlaces = [a for a in bloque.find_all("a", href=patron_usuario) if a not in enlaces_anidados]
            if len(enlaces) < 2:
                continue
            autor = enlaces[0]["href"].strip("/")
            parent_username = enlaces[1]["href"].strip("/")
            _agregar(autor, parent_username, bloque)

    print(f"    [debug respuestas] estrategia A/B encontró {len(replies)} respuesta(s) en el DOM")
    return replies


def _guardar_dom_debug(driver, shortcode):
    if not DEBUG_GUARDAR_DOM:
        return
    try:
        carpeta = os.path.join(RESULTADOS_DIR, "debug_dom")
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, f"{shortcode or 'post'}_{datetime.now().strftime('%H%M%S')}.html")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"    [debug] DOM guardado en: {ruta}")
    except Exception as e:
        print(f"    [debug] no se pudo guardar DOM: {e}")


def extraer_comentarios_post(driver, caption_actual, usuario, url_post=None):
    """
    Extrae comentarios raíz desde el script JSON SSR (parsear_comentarios_html)
    y respuestas desde el DOM renderizado tras expandir_respuestas() (ver
    _extraer_replies_dom para el porqué de esta separación).
    """
    comentarios_raw = parsear_comentarios_html(driver.page_source)

    expandir_respuestas(driver)
    time.sleep(1.5)  # dar tiempo a que el DOM termine de pintar las replies

    _guardar_dom_debug(driver, _extraer_shortcode(url_post))

    # Refrescar raíces por si el SSR inicial no traía todas (algunos posts
    # solo muestran "Ver los N comentarios" y cargan las raíces también por
    # AJAX) — nos quedamos con el set más completo.
    comentarios_raw_2 = parsear_comentarios_html(driver.page_source)
    if len(comentarios_raw_2) > len(comentarios_raw):
        comentarios_raw = comentarios_raw_2

    # Filtrar el caption del owner — mismo texto que caption_actual
    if caption_actual:
        cap_norm = caption_actual.strip()
        comentarios_raw = [
            c for c in comentarios_raw
            if c["text"] != cap_norm and c["text"][:40] not in cap_norm
        ]

    resultado = []
    autor_a_cmt_id = {}  # último comment_id visto por autor (para linkear replies por mención)
    cmt_counter = 0

    for c in comentarios_raw:
        cmt_counter += 1
        cmt_id = f"cmt_{cmt_counter}"
        autor_a_cmt_id[c["username"]] = cmt_id
        resultado.append({
            "comment_id":        cmt_id,
            "autor":             c["username"],
            "texto":             c["text"],
            "es_reply":          False,
            "parent_comment_id": "",
        })

    for r in _extraer_replies_dom(driver):
        cmt_counter += 1
        cmt_id = f"cmt_{cmt_counter}"
        parent_id = autor_a_cmt_id.get(r["parent_username"], "")
        resultado.append({
            "comment_id":        cmt_id,
            "autor":             r["autor"],
            "texto":             r["texto"],
            "es_reply":          True,
            "parent_comment_id": parent_id,
        })
        autor_a_cmt_id[r["autor"]] = cmt_id  # por si alguien responde a esta respuesta

    return resultado


def procesar_perfil(driver, item):
    f_i, f_f = item["f_inicio"], item["f_fin"]
    print(f"\nProcesando: {item['usuario']} (Rango: {f_i.date()} a {f_f.date()})")
    
    driver.get(item["url"])
    time.sleep(5)
    hacer_scroll(driver)
    
    h_path = os.path.join(RESULTADOS_DIR, f"perfil_{limpiar_nombre_archivo(item['usuario'])}_{datetime.now().strftime('%H%M%S')}.html")
    os.makedirs(RESULTADOS_DIR, exist_ok=True)
    with open(h_path, "w", encoding="utf-8") as f: f.write(driver.page_source)
    
    info = parsear_html_perfil(h_path)
    posts_data = []
    for i, link in enumerate(info["links_posts"], 1):
        driver.get(link)
        time.sleep(ESPERA_CAPTION)
        f_p = obtener_fecha_post(driver, url_post=link)

        # Verificar rango — el <time> de IG a veces devuelve fecha de edición.
        # Si cae fuera de rango, leer también la fecha_text del caption como
        # respaldo (que muestra la fecha original de publicación).
        if not f_p or not (f_i <= f_p <= f_f):
            cap_check = extraer_caption_post(driver)
            fecha_txt  = (cap_check.get("fecha_text") or "") if cap_check else ""
            try:
                from dateutil import parser as dp
                f_check = dp.parse(fecha_txt, fuzzy=True).replace(tzinfo=None) if fecha_txt else None
                if not f_check or not (f_i <= f_check <= f_f):
                    print(f"[{i}] Fuera de rango ({f_p} / '{fecha_txt}') — saltando")
                    continue
                f_p = f_check
                cap = cap_check
            except Exception:
                print(f"[{i}] Fuera de rango ({f_p}) — saltando")
                continue
        else:
            cap = extraer_caption_post(driver)

        caption_texto = cap.get("caption") if cap else None
        comms = extraer_comentarios_post(driver, caption_texto, item["usuario"], url_post=link)
        
        posts_data.append({
            "url": link, 
            "fecha_post_iso": f_p.isoformat(), 
            **cap,
            "comments": comms
        })
        print(f"[{i}] Post capturado: {f_p.date()} | {len(comms)} comments")

    posts_data.sort(key=lambda p: p.get("fecha_post_iso") or "", reverse=True)

    res = {
        **item, 
        **info, 
        "rango_fechas": {"inicio": f_i.strftime("%Y-%m-%d"), "fin": f_f.strftime("%Y-%m-%d")}, 
        "posts_data": posts_data,
        "f_inicio": f_i.isoformat(), 
        "f_fin": f_f.isoformat()
    }
    save_json(res, f"perfil_{limpiar_nombre_archivo(item['usuario'])}")
    return res

def main():
    usuarios = cargar_usuarios(CSV_PATH)
    if not usuarios: return print("No hay usuarios.")
    
    driver = webdriver.Chrome() 
    wait = WebDriverWait(driver, ESPERA_LOGIN)
    resultados_totales = []
    
    try:
        iniciar_sesion(driver, wait)
        for item in usuarios:
            try: resultados_totales.append(procesar_perfil(driver, item))
            except Exception as e:
                print(f"Error en {item['usuario']}: {e}")
                resultados_totales.append({**item, "error": str(e), "posts_data": []})
        
        save_json({"resultados": resultados_totales}, "global")
        # Aquí se aplica la anonimización únicamente para el JSON anónimo
        save_json({"resultados": [construir_resultado_anonimo(r) for r in resultados_totales]}, "global_anonimo")
        input("Terminado. Enter para cerrar...")
    finally: driver.quit()

if __name__ == "__main__": main()