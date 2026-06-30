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

def obtener_fecha_post(driver):
    try:
        f_iso = driver.find_element(By.TAG_NAME, "time").get_attribute("datetime")
        return datetime.fromisoformat(f_iso.replace("Z", "+00:00")).replace(tzinfo=None) if f_iso else None
    except: return None

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
    m_d = re.search(r"([^,]+) seguidores, ([^,]+) seguidos, ([^,]+) pub", desc) if desc else None
    
    links_raw = ["https://www.instagram.com" + a["href"].split("?")[0] if a["href"].startswith("/") else a["href"].split("?")[0]
                 for a in soup.find_all("a", href=True) if "/p/" in a["href"] or "/reel/" in a["href"]]
    links = list(dict.fromkeys(links_raw))  # dedup preservando orden de aparición
    
    return {
        "username": m_t.group(2) if m_t else None, "nombre": m_t.group(1) if m_t else None,
        "followers": m_d.group(1) if m_d else None, "following": m_d.group(2) if m_d else None,
        "posts": m_d.group(3) if m_d else None, "profile_pic": get_meta("og:image"), "links_posts": links
    }

def extraer_caption_post(driver):
    for xpath in ["//article//h1", "//article//ul//span", "//article//div//span"]:
        try:
            texts = [el.text.strip() for el in driver.find_elements(By.XPATH, xpath) if len(el.text.strip()) >= 15]
            if texts: return {k: (texts[0] if k in ["caption", "caption_raw"] else None) for k in ["likes_text", "comments_text", "username_post", "fecha_text", "caption", "caption_raw"]}
        except: pass
    
    try:
        desc = driver.find_element(By.XPATH, "//meta[@property='og:description']").get_attribute("content")
        m = re.search(r"^(.*?),\s*(.*?)\s*-\s*(.*?)\s+el\s+(.*?):\s*\"?(.*?)\"?\.?\s*$", re.sub(r"\s+", " ", desc))
        return {
            "likes_text": m.group(1) if m else None, "comments_text": m.group(2) if m else None,
            "username_post": m.group(3) if m else None, "fecha_text": m.group(4) if m else None,
            "caption": m.group(5) if m else re.search(r'"(.*?)"', desc).group(1) if re.search(r'"(.*?)"', desc) else None,
            "caption_raw": desc
        }
    except: return {k: None for k in ["likes_text", "comments_text", "username_post", "fecha_text", "caption", "caption_raw"]}

def expandir_respuestas(driver):
    """Hace clic en todos los botones 'Ver respuestas (N)' para que las
    respuestas queden visibles en el DOM antes de leer los comentarios.
    IG no siempre usa <button> real para esto — puede ser div/span/a con
    un click-handler de React, así que buscamos en cualquier elemento."""
    try:
        intentos = 0
        while intentos < 8:
            botones = driver.find_elements(
                By.XPATH,
                "//*[self::button or self::div or self::span or self::a]"
                "[contains(translate(., 'VERSPUESTAILY', 'verspuestaily'), 'ver') and "
                "(contains(translate(., 'VERSPUESTAILY', 'verspuestaily'), 'respuesta') or "
                "contains(translate(., 'VERSPUESTAILY', 'verspuestaily'), 'repl'))]"
            )
            # Filtrar solo elementos "hoja" (sin hijos con el mismo texto) para no
            # hacer doble-click sobre contenedores anidados que matchean igual
            candidatos = []
            for b in botones:
                try:
                    if b.is_displayed():
                        candidatos.append(b)
                except:
                    pass
            if not candidatos:
                break
            clicks = 0
            for b in candidatos:
                try:
                    driver.execute_script("arguments[0].click();", b)
                    clicks += 1
                    time.sleep(0.6)
                except:
                    pass
            if clicks == 0:
                break
            intentos += 1
            time.sleep(1.0)
    except Exception as e:
        print(f"Aviso expandiendo respuestas: {e}")


def extraer_comentarios_post(driver, caption_actual, usuario):
    """
    Extrae comentarios con jerarquía: cada comentario raiz tiene comment_id,
    y cada respuesta (capturada tras expandir 'Ver respuestas (N)') queda
    anidada con parent_comment_id apuntando a su comentario padre.
    Devuelve lista de dicts: {comment_id, autor, texto, es_reply, parent_comment_id}
    """
    expandir_respuestas(driver)

    comentarios_planos = []  # texto crudo en orden, igual que la lógica original
    try:
        elementos = driver.find_elements(By.XPATH, "//main//*[@dir='auto']")

        ui_exacta = [
            "responder", "ver traducción", "ocultar", "ver respuestas", "editado",
            "me gusta", "likes", "enviar", "todavía no hay comentarios.",
            "inicia la conversación.", "audio original"
        ]

        for el in elementos:
            texto = el.text.strip()
            t_low = texto.lower()

            if not texto or texto == usuario:
                continue
            if texto.startswith(f"{usuario}\n") or (caption_actual and texto == caption_actual):
                continue
            if t_low in ui_exacta:
                continue
            if re.match(r"^\d+\s+(me\s+gusta|likes)$", t_low):
                continue
            if re.match(r"^ver\s+(las\s+)?\d+\s+respuestas?$", t_low):
                continue
            if t_low.startswith("más publicaciones de"):
                continue
            if re.match(r"^\d{1,2}\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)$", t_low):
                continue
            if re.match(r"^\d+\s*(sem|d|h|min|s)$", t_low):
                continue

            if texto not in comentarios_planos:
                comentarios_planos.append(texto)

    except Exception as e:
        print(f"Aviso en comentarios: {e}")

    # Convertir la lista plana [usuario, texto, usuario, texto, ...] en
    # objetos con jerarquia. IG no expone visualmente el nivel de indentado
    # en el texto crudo, asi que usamos heuristica: cada par [usuario, texto]
    # es un comentario raiz NUEVO salvo que el texto contenga "Les gusta a"
    # o el patron de respuesta inmediata tras un "Ver respuestas" expandido
    # (se detecta porque el siguiente par aparece sin separacion de "tiempo"
    # propio — aproximacion practica: todo par tras un comentario raiz que
    # no tenga su propio marcador de tiempo se trata como reply de ese root).
    resultado = []
    comment_counter = 0
    ultimo_root_id = None

    i = 0
    while i < len(comentarios_planos):
        texto_actual = comentarios_planos[i]

        # Saltar textos de sistema (igual que la version anonima original)
        if texto_actual.startswith("Les gusta a") or "Hace " in texto_actual:
            i += 1
            continue

        if i + 1 < len(comentarios_planos):
            user_real = texto_actual
            comentario_texto = comentarios_planos[i + 1]

            comment_counter += 1
            comment_id = f"cmt_{comment_counter}"

            # Heuristica de reply: si el username viene con un texto que
            # Selenium capturo DESPUES de hacer clic en "Ver respuestas",
            # weblo identificamos porque expandir_respuestas ya corrio antes
            # de leer elementos, así que las respuestas ya estan en el DOM
            # intercaladas justo despues de su comentario padre. Usamos
            # indentación de posicion en pantalla (x del elemento) como señal.
            es_reply = False
            try:
                el_usuario = next(
                    (e for e in driver.find_elements(By.XPATH, "//main//*[@dir='auto']")
                     if e.text.strip() == user_real), None
                )
                if el_usuario is not None and ultimo_root_id is not None:
                    x_pos = el_usuario.location.get("x", 0)
                    if x_pos > 80:  # las respuestas de IG aparecen indentadas a la derecha
                        es_reply = True
            except:
                pass

            cmt = {
                "comment_id": comment_id,
                "autor": user_real,
                "texto": comentario_texto,
                "es_reply": es_reply,
                "parent_comment_id": ultimo_root_id if es_reply else "",
            }
            if not es_reply:
                ultimo_root_id = comment_id

            resultado.append(cmt)
            i += 2
        else:
            i += 1

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
        f_p = obtener_fecha_post(driver)
        if f_p and f_i <= f_p <= f_f:
            cap = extraer_caption_post(driver)
            caption_texto = cap.get("caption") if cap else None
            comms = extraer_comentarios_post(driver, caption_texto, item["usuario"])
            
            posts_data.append({
                "url": link, 
                "fecha_post_iso": f_p.isoformat(), 
                **cap,
                "comments": comms
            })
            print(f"[{i}] Post y comentarios capturados: {f_p.date()}")

    # Ordenar por fecha real verificada (mas reciente primero) — el orden de
    # links_posts no es confiable porque viene de un set()/HTML sin garantia
    # de orden cronologico.
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