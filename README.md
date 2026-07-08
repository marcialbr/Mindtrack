# MindTrack - Scrapers

Herramienta de automatización para la extracción de publicaciones y comentarios de Instagram y Facebook dentro de rangos de fechas específicos.

---

# Requisitos

- Python 3
- Google Chrome y ChromeDriver

### Dependencias

```bash
pip install selenium beautifulsoup4 requests undetected-chromedriver
```

---

# Configuración de Entrada

El archivo `formato(Sheet1).csv` debe contener las siguientes columnas según la plataforma objetivo:

| Columna | Descripción |
|---------|-------------|
| `id` | Identificador único del registro. |
| `instagram` | Nombre de usuario o URL del perfil (requerido para el scraper de Instagram). |
| `facebook` | Nombre de usuario o identificador de la cuenta (requerido para el scraper de Facebook). |
| `F. Inicio` | Fecha de inicio del rango de extracción (`AAAA-MM-DD`). |
| `F. Fin` | Fecha de fin del rango de extracción (`AAAA-MM-DD`). |

---

# Procesos Principales (Instagram)

- **Autenticación:** Inicio de sesión automático mediante Selenium.
- **Escaneo:** Scroll en el perfil para recopilar enlaces de publicaciones y reels.
- **Filtrado:** Validación de la fecha de cada publicación según el rango especificado en el CSV.
- **Limpieza:** Uso de expresiones regulares para eliminar ruido de la interfaz (botones de respuesta, marcas de tiempo, contadores de likes y captions duplicados).

---

# Procesos Principales (Facebook)

### Perfil de Chrome Persistente y Antidetección

Utiliza `undetected-chromedriver` para reducir las señales de automatización que detecta Facebook. El script genera un directorio persistente (`.fb_scraper_profile`) en la carpeta de usuario del sistema operativo para almacenar las cookies de sesión de forma local, evitando la necesidad de herramientas externas de gestión de perfiles.

### Autenticación Inicial Única

Durante la primera ejecución, si el script detecta que no hay una sesión activa, suspende el proceso de forma segura e instruye al usuario a realizar el login manual y resolver cualquier sistema de verificación (2FA/checkpoint) directamente sobre la interfaz de Chrome abierta. Las siguientes ejecuciones reutilizan las credenciales almacenadas de manera automática.

### Interceptación XHR/Fetch (CDP)

Mediante Chrome DevTools Protocol (CDP), se inyecta un escuchador en el navegador antes de cargar la plataforma. Este interceptor captura las respuestas de red asíncronas de Facebook (`Fetch` y `XMLHttpRequest`), extrayendo los datos puros de publicaciones (*Story*) y metadatos sin depender exclusivamente del DOM.

### Escaneo Progresivo (Scroll Humano)

El script emula el desplazamiento de un usuario mediante coordenadas y tiempos de espera aleatorios. Debido a que Facebook destruye las publicaciones que salen de pantalla (virtualización de elementos), la recolección de posts se realiza de forma interactiva y acumulativa en cada ronda del scroll.

### Carga Guiada de Hilos de Comentarios

Para las publicaciones que registran actividad, el navegador accede de manera directa a la URL del post, ejecuta un scroll controlado y cliquea sistemáticamente los selectores de expansión de respuestas ("Ver más comentarios") para forzar la inyección de los textos en el buffer de red interceptado.

### Procesamiento de Fechas y Filtros

Las fechas se convierten al estándar ISO desde sus marcas de tiempo nativas. El script discrimina eventos del sistema e interacciones no deseadas (como actualizaciones de fotos de portada o perfiles) antes de validar si el contenido está dentro de las fechas estipuladas por el CSV.

---

# Salida de Datos

## Carpeta `resultados_ig/` (Instagram)

- **JSON por perfil:** Datos detallados de cada usuario de forma individual.
- **Global:** Resumen consolidado de toda la ejecución.
- **Anónimo:** Versión filtrada sin datos identificables para análisis general.

## Carpeta `resultados_fb/` (Facebook)

### `resultados_fb.json`

Almacena el árbol completo de datos crudos extraídos de los perfiles:

- Nombres de autores.
- Cantidad de seguidores.
- Métricas de engagement (likes y comentarios totales).
- Textos de las publicaciones.
- Estructura jerárquica de comentarios vinculados a su identificador raíz (`parent_comment_id`).

### `resultados_fb_anonimizado.json`

Genera de forma paralela una estructura ideal para procesamiento de lenguaje natural o análisis de sentimientos.

Características:

- Elimina nombres de usuarios.
- Elimina identificadores numéricos reales de la plataforma.
- Elimina fotos de perfil.
- Elimina enlaces.
- Reemplaza las identidades con alias persistentes (`autor_A`, `autor_B`, etc.).
- Conserva las relaciones conversacionales.
- Conserva las marcas de tiempo numéricas.
- Conserva el contenido íntegro de los mensajes.