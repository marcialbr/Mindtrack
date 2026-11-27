"""Cliente minimo para los despliegues de Microsoft Foundry del proyecto.

Solo biblioteca estandar: no hace falta instalar nada ni activar un entorno.
Funciona igual en Linux y en Windows.

Autenticacion: llave de API en la variable AZURE_FOUNDRY_API_KEY (se lee del
archivo .env de la raiz del repositorio si no esta en el ambiente). La llave
nunca se imprime ni se guarda en los artefactos de una corrida.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENDPOINT_POR_DEFECTO = "https://mf-mz-mindtrack-resource.services.ai.azure.com/openai/v1"

# Errores que vale la pena reintentar: limite de tokens por minuto y fallas
# transitorias del servicio.
CODIGOS_REINTENTABLES = {408, 409, 429, 500, 502, 503, 504}


class ErrorFoundry(RuntimeError):
    """Falla al llamar a un despliegue."""


def cargar_dotenv(ruta: Path | None = None) -> None:
    """Mete en os.environ las claves de .env que aun no esten definidas."""
    ruta = ruta or (RAIZ / ".env")
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


@dataclass
class Uso:
    """Tokens consumidos por una llamada."""

    entrada: int = 0
    salida: int = 0

    def __add__(self, otro: "Uso") -> "Uso":
        return Uso(self.entrada + otro.entrada, self.salida + otro.salida)


@dataclass
class Respuesta:
    texto: str
    modelo: str
    uso: Uso
    segundos: float
    intentos: int = 1


@dataclass
class Cliente:
    """Llama a un despliegue de Foundry por la ruta compatible con OpenAI."""

    endpoint: str = ""
    llave: str = ""
    timeout: int = 900
    max_intentos: int = 5
    uso_total: Uso = field(default_factory=Uso)

    def __post_init__(self) -> None:
        cargar_dotenv()
        self.endpoint = (
            self.endpoint
            or os.environ.get("AZURE_FOUNDRY_ENDPOINT")
            or ENDPOINT_POR_DEFECTO
        ).rstrip("/")
        self.llave = self.llave or os.environ.get("AZURE_FOUNDRY_API_KEY", "")
        if not self.llave:
            raise ErrorFoundry(
                "Falta AZURE_FOUNDRY_API_KEY.\n"
                "Copia la llave de la pagina del proyecto en Foundry (campo 'API key')\n"
                "y guardala en el .env de la raiz del repositorio sin que quede en el\n"
                "historial de la terminal:\n\n"
                "    read -rs -p 'Llave: ' K && printf '\\nAZURE_FOUNDRY_API_KEY=%s\\n' \"$K\" >> .env && unset K\n"
            )

    def completar(
        self,
        modelo: str,
        mensajes: list[dict[str, str]],
        max_tokens_salida: int | None = None,
    ) -> Respuesta:
        """Una llamada de chat. `mensajes` es la conversacion completa y unica.

        No hay estado entre llamadas: lo que el modelo ve es exactamente lo que
        va en `mensajes`. De eso depende que los revisores sean independientes.
        """
        cuerpo: dict[str, object] = {"model": modelo, "messages": mensajes}
        if max_tokens_salida:
            cuerpo["max_completion_tokens"] = max_tokens_salida
        datos = json.dumps(cuerpo).encode("utf-8")

        ultimo_error: str = ""
        for intento in range(1, self.max_intentos + 1):
            peticion = urllib.request.Request(
                f"{self.endpoint}/chat/completions",
                data=datos,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.llave}",
                    "api-key": self.llave,
                },
            )
            inicio = time.monotonic()
            try:
                with urllib.request.urlopen(peticion, timeout=self.timeout) as r:
                    carga = json.loads(r.read().decode("utf-8"))
                segundos = time.monotonic() - inicio
                return self._leer(carga, modelo, segundos, intento)
            except urllib.error.HTTPError as e:
                detalle = e.read().decode("utf-8", errors="replace")[:800]
                ultimo_error = f"HTTP {e.code}: {detalle}"
                if e.code not in CODIGOS_REINTENTABLES or intento == self.max_intentos:
                    raise ErrorFoundry(
                        f"{modelo}: la llamada fallo. {ultimo_error}"
                    ) from None
                espera = self._espera(e, intento)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                ultimo_error = f"{type(e).__name__}: {e}"
                if intento == self.max_intentos:
                    raise ErrorFoundry(f"{modelo}: la llamada fallo. {ultimo_error}") from None
                espera = self._espera(None, intento)
            print(
                f"    reintento {intento}/{self.max_intentos - 1} en {espera:.0f}s "
                f"({ultimo_error.splitlines()[0][:120]})",
                flush=True,
            )
            time.sleep(espera)
        raise ErrorFoundry(f"{modelo}: la llamada fallo. {ultimo_error}")

    @staticmethod
    def _espera(error: urllib.error.HTTPError | None, intento: int) -> float:
        """Respeta Retry-After si viene; si no, retroceso exponencial con jitter."""
        if error is not None:
            cabecera = error.headers.get("Retry-After") if error.headers else None
            if cabecera:
                try:
                    return min(float(cabecera), 120.0)
                except ValueError:
                    pass
        return min(2.0**intento, 60.0) + random.uniform(0, 1.5)

    def _leer(
        self, carga: dict, modelo: str, segundos: float, intentos: int
    ) -> Respuesta:
        try:
            texto = carga["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ErrorFoundry(
                f"{modelo}: respuesta inesperada: {json.dumps(carga)[:500]}"
            ) from None
        datos_uso = carga.get("usage") or {}
        uso = Uso(
            entrada=int(datos_uso.get("prompt_tokens", 0) or 0),
            salida=int(datos_uso.get("completion_tokens", 0) or 0),
        )
        self.uso_total = self.uso_total + uso
        return Respuesta(
            texto=texto.strip(), modelo=modelo, uso=uso, segundos=segundos, intentos=intentos
        )

    def probar(self, modelo: str) -> tuple[bool, str]:
        """Ping corto a un despliegue. Devuelve (sirve, detalle)."""
        try:
            r = self.completar(
                modelo,
                [{"role": "user", "content": "Responde unicamente: listo"}],
                max_tokens_salida=2048,
            )
        except ErrorFoundry as e:
            return False, str(e).splitlines()[0][:200]
        return True, f"{r.segundos:.1f}s, {r.uso.entrada + r.uso.salida} tokens"
