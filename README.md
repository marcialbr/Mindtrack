# MindTrack - Scrapers

Herramienta de automatización para la extracción de publicaciones y comentarios de Instagram y Facebook dentro de rangos de fechas específicos.

## Requisitos
- Python 3[cite: 1, 2]
- Google Chrome y ChromeDriver[cite: 1]
- AdsPower Browser (requerido para el módulo de Facebook)[cite: 2]
- Dependencias:
  ```bash
  pip install selenium beautifulsoup4 requests
  ```[cite: 1, 2]

## Configuración de Entrada
El archivo `formato(Sheet1).csv` debe contener las siguientes columnas según la plataforma objetivo:[cite: 1, 2]
- **id**: Identificador único del registro.[cite: 1, 2]
- **instagram**: Nombre de usuario o URL del perfil (requerido para el scraper de Instagram).[cite: 1]
- **facebook**: Nombre de usuario o identificador de la cuenta (requerido para el scraper de Facebook).[cite: 2]
- **F. Inicio**: Fecha de inicio del rango de extracción (AAAA-MM-DD).[cite: 1, 2]
- **F. Fin**: Fecha de fin del rango de extracción (AAAA-MM-DD).[cite: 1, 2]

## Procesos Principales (Instagram)
1. **Autenticación**: Inicio de sesión automático mediante Selenium.[cite: 1]
2. **Escaneo**: Scroll en el perfil para recopilar enlaces de posts y reels.[cite: 1]
3. **Filtrado**: Validación de la fecha de cada publicación según el rango del CSV.[cite: 1]
4. **Limpieza**: Uso de expresiones regulares para eliminar ruido de la interfaz (botones de respuesta, marcas de tiempo, contadores de likes y captions duplicados).[cite: 1]

## Procesos Principales (Facebook)
1. **Conexión con AdsPower**: El script se conecta a la API local de AdsPower (`http://localhost:50325`) utilizando una clave de acceso (`ADS_API_KEY`) y un identificador de perfil (`ADS_PROFILE_ID`)[cite: 2]. Esto permite levantar de forma automatizada un navegador que ya cuenta con la sesión de Facebook iniciada, evitando bloqueos y desafíos de inicio de sesión[cite: 2].
2. **Interceptación XHR/Fetch (CDP)**: Se registra un script inyector a través de Chrome DevTools Protocol (CDP)[cite: 2]. Este interceptor captura en tiempo real las respuestas de red (Fetch y XMLHttpRequest) emitidas por la plataforma, permitiendo extraer los datos crudos de las publicaciones (`Story`) y comentarios directamente desde el flujo de datos[cite: 2].
3. **Escaneo con Scroll Humano**: Realiza desplazamientos emulando el comportamiento humano con intervalos y distancias aleatorias[cite: 2]. Debido a que Facebook utiliza virtualización de elementos (windowing) y destruye del DOM los elementos que salen de la pantalla, la captura de posts se realiza de manera progresiva ronda por ronda durante el scroll[cite: 2].
4. **Extracción Dirigida de Comentarios**: El scraper navega individualmente a la URL de cada publicación que posea interacciones[cite: 2]. Una vez ahí, realiza scrolls dedicados y hace clic automáticamente en los botones de expansión ("Ver más comentarios") para forzar la carga de todo el hilo de respuestas en el buffer de red[cite: 2].
5. **Normalización y Filtrado**: Convierte las marcas de tiempo (`creation_time`) a formato ISO, descarta publicaciones irrelevantes (como eventos de vida o actualizaciones de fotos de perfil) y filtra los resultados para conservar únicamente lo que esté dentro del rango de fechas estipulado[cite: 2].

## Salida de Datos
Los resultados se guardan en directorios separados dependiendo del scraper ejecutado:[cite: 1, 2]

### Carpeta `resultados_ig/` (Instagram)
- **JSON por perfil**: Datos detallados de cada usuario de forma individual.[cite: 1]
- **Global**: Resumen consolidado de toda la ejecución.[cite: 1]
- **Anónimo**: Versión filtrada sin datos identificables para análisis general.[cite: 1]

### Carpeta `resultados_fb/` (Facebook)
- **resultados_fb.json**: Contiene la extracción íntegra y estructurada de los perfiles procesados, incluyendo el nombre del autor, número de seguidores, datos de engagement, texto de la publicación y el árbol completo de comentarios con su respectiva jerarquía de respuestas (`parent_comment_id`).[cite: 2]
- **resultados_fb_anonimizado.json**: Una versión diseñada para análisis de texto o minería de emociones[cite: 2]. Elimina nombres reales, identificadores únicos de Facebook, URLs y fotos de perfil[cite: 2]. En su lugar, genera alias estables y secuenciales (`autor_A`, `autor_B`) para los usuarios a lo largo de toda la ejecución, preservando intactos los contenidos de los comentarios, las fechas y las métricas métricas numéricas[cite: 2].