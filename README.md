# MindTrack - Scrapers

Herramienta de automatización para la extracción de publicaciones y comentarios de Instagram y Facebook dentro de rangos de fechas específicos.

## Requisitos
- Python 3
- Google Chrome y ChromeDriver
- AdsPower Browser (requerido para el módulo de Facebook)
- Dependencias:
  ```bash
  pip install selenium beautifulsoup4 requests
  ```

## Configuración de Entrada
El archivo `formato(Sheet1).csv` debe contener las siguientes columnas según la plataforma objetivo:
- **id**: Identificador único del registro.
- **instagram**: Nombre de usuario o URL del perfil (requerido para el scraper de Instagram).
- **facebook**: Nombre de usuario o identificador de la cuenta (requerido para el scraper de Facebook).
- **F. Inicio**: Fecha de inicio del rango de extracción (AAAA-MM-DD).
- **F. Fin**: Fecha de fin del rango de extracción (AAAA-MM-DD).

## Procesos Principales (Instagram)
1. **Autenticación**: Inicio de sesión automático mediante Selenium.
2. **Escaneo**: Scroll en el perfil para recopilar enlaces de posts y reels.
3. **Filtrado**: Validación de la fecha de cada publicación según el rango del CSV.
4. **Limpieza**: Uso de expresiones regulares para eliminar ruido de la interfaz (botones de respuesta, marcas de tiempo, contadores de likes y captions duplicados).

## Procesos Principales (Facebook)
1. **Conexión con AdsPower**: El script se conecta a la API local de AdsPower (`http://localhost:50325`) utilizando una clave de acceso (`ADS_API_KEY`) y un identificador de perfil (`ADS_PROFILE_ID`) Esto permite levantar de forma automatizada un navegador que ya cuenta con la sesión de Facebook iniciada, evitando bloqueos y desafíos de inicio de sesión
2. **Interceptación XHR/Fetch (CDP)**: Se registra un script inyector a través de Chrome DevTools Protocol (CDP) Este interceptor captura en tiempo real las respuestas de red (Fetch y XMLHttpRequest) emitidas por la plataforma, permitiendo extraer los datos crudos de las publicaciones (`Story`) y comentarios directamente desde el flujo de datos
3. **Escaneo con Scroll Humano**: Realiza desplazamientos emulando el comportamiento humano con intervalos y distancias aleatorias Debido a que Facebook utiliza virtualización de elementos (windowing) y destruye del DOM los elementos que salen de la pantalla, la captura de posts se realiza de manera progresiva ronda por ronda durante el scroll
4. **Extracción Dirigida de Comentarios**: El scraper navega individualmente a la URL de cada publicación que posea interacciones Una vez ahí, realiza scrolls dedicados y hace clic automáticamente en los botones de expansión ("Ver más comentarios") para forzar la carga de todo el hilo de respuestas en el buffer de red
5. **Normalización y Filtrado**: Convierte las marcas de tiempo (`creation_time`) a formato ISO, descarta publicaciones irrelevantes (como eventos de vida o actualizaciones de fotos de perfil) y filtra los resultados para conservar únicamente lo que esté dentro del rango de fechas estipulado

## Salida de Datos
Los resultados se guardan en directorios separados dependiendo del scraper ejecutado:

### Carpeta `resultados_ig/` (Instagram)
- **JSON por perfil**: Datos detallados de cada usuario de forma individual.
- **Global**: Resumen consolidado de toda la ejecución.
- **Anónimo**: Versión filtrada sin datos identificables para análisis general.

### Carpeta `resultados_fb/` (Facebook)
- **resultados_fb.json**: Contiene la extracción íntegra y estructurada de los perfiles procesados, incluyendo el nombre del autor, número de seguidores, datos de engagement, texto de la publicación y el árbol completo de comentarios con su respectiva jerarquía de respuestas (`parent_comment_id`).
- **resultados_fb_anonimizado.json**: Una versión diseñada para análisis de texto o minería de emociones Elimina nombres reales, identificadores únicos de Facebook, URLs y fotos de perfil En su lugar, genera alias estables y secuenciales (`autor_A`, `autor_B`) para los usuarios a lo largo de toda la ejecución, preservando intactos los contenidos de los comentarios, las fechas y las métricas métricas numéricas