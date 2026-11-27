#!/usr/bin/env python3
"""Ciclo automatico autor / revisores para un manuscrito.

Un agente autor, con todo el contexto del proyecto, produce una version.
Dos revisores independientes, sin ningun contexto previo ni memoria entre
llamadas, la revisan por separado. El autor atiende las dos revisiones y
produce la siguiente version. Se repite el numero de rondas que se pida.

Los revisores son independientes de verdad: cada revision es una llamada
nueva cuyo contenido es solo la consigna de revisor mas el texto del
manuscrito. No reciben el contexto del proyecto, ni las versiones previas,
ni la revision del otro revisor, ni su propia revision anterior.

Uso tipico:

    python3 revision_manuscrito/ciclo_revision.py \\
        --manuscrito borrador.md \\
        --contexto revision_manuscrito/contexto \\
        --rondas 2

Verificar que los despliegues responden, sin gastar casi nada:

    python3 revision_manuscrito/ciclo_revision.py --probar
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from foundry import Cliente, ErrorFoundry, Respuesta  # noqa: E402

AUTOR_POR_DEFECTO = "gpt-6.2-astra"
REVISOR_A_POR_DEFECTO = "gpt-6.2-astra"
REVISOR_B_POR_DEFECTO = "Kimi-K3.1-Pro"

EXTENSIONES_CONTEXTO = {".md", ".txt", ".markdown", ".rst", ".csv", ".json"}

MARCA_MANUSCRITO = "===MANUSCRITO==="
MARCA_FIN_MANUSCRITO = "===FIN MANUSCRITO==="
MARCA_CARTA = "===CARTA==="
MARCA_FIN_CARTA = "===FIN CARTA==="


# --------------------------------------------------------------------------
# Consignas
# --------------------------------------------------------------------------

AUTOR_SISTEMA = """Eres el autor principal de un manuscrito cientifico. Conoces \
el proyecto a fondo: su pregunta, sus metodos, sus datos y sus limitaciones.

Escribes prosa academica sobria: afirmaciones proporcionadas a la evidencia, \
sin adjetivos de relleno, sin repetir en la discusion lo que ya dijeron los \
resultados. Cuando un dato no da para una conclusion, lo dices en el texto en \
vez de taparlo.

Devuelves siempre el manuscrito completo en Markdown, no un resumen ni una \
lista de cambios, salvo que se te pida otra cosa de forma explicita. No \
inventas resultados, citas, cifras ni referencias: si falta algo que solo los \
autores pueden aportar, lo marcas en el texto como [PENDIENTE: ...] y sigues."""

REVISOR_SISTEMA = """Eres un revisor por pares independiente para una revista \
cientifica. No conoces a los autores, no has visto antes este trabajo y no \
tienes mas informacion que el manuscrito que se te entrega. Si algo no esta \
en el manuscrito, para ti no existe, y esa ausencia es en si misma un hallazgo \
de la revision.

Evalua con el rigor de una revista seria y en este orden:

1. **Recomendacion**: aceptar / revision menor / revision mayor / rechazar, en \
una linea, con una frase de justificacion.
2. **Resumen del trabajo** en tus propias palabras, dos o tres frases, para que \
los autores vean que entendiste.
3. **Comentarios mayores**, numerados. Cada uno: que esta mal, donde (cita \
textual breve del manuscrito), por que importa, y que haria falta para \
resolverlo. Aqui van los problemas de diseno, de inferencia, de analisis, de \
afirmaciones no sostenidas por los datos, de limitaciones ausentes, de \
inconsistencias entre resumen, resultados y discusion.
4. **Comentarios menores**, numerados: claridad, estructura, terminologia, \
unidades, figuras y tablas, referencias.
5. **Lo que el manuscrito hace bien**, breve y solo si es cierto.

No elogies sin evidencia. No inventes defectos para parecer exigente, pero \
tampoco dejes pasar un problema real por cortesia. Escribe en espanol."""

REVISOR_USUARIO = """Revisa el siguiente manuscrito.

---
{manuscrito}
---"""

AUTOR_REDACTA = """Con el contexto del proyecto que ya tienes, produce la \
version para someter a revision.

{instruccion_borrador}

Devuelve unicamente el manuscrito completo en Markdown."""

AUTOR_ATIENDE = """Dos revisores independientes evaluaron tu manuscrito por \
separado. Ninguno conocia el proyecto ni vio la revision del otro.

Tu trabajo ahora:

1. Atender cada comentario mayor y cada comentario menor de los dos informes.
2. Donde el revisor tenga razon, corregir el manuscrito de verdad, no suavizar \
la redaccion.
3. Donde el revisor se equivoque o pida algo imposible con los datos \
disponibles, mantener tu posicion y defenderla con un argumento concreto en la \
carta de respuesta.
4. Donde los dos revisores se contradigan, decidir y explicar por que.

Devuelve exactamente dos bloques, con estas marcas literales y nada fuera de \
ellos:

{marca_manuscrito}
(el manuscrito completo y revisado, en Markdown)
{marca_fin_manuscrito}
{marca_carta}
(la carta de respuesta a los revisores, punto por punto, indicando para cada
comentario que cambiaste y donde, o por que no lo cambiaste)
{marca_fin_carta}

MANUSCRITO ACTUAL
---
{manuscrito}
---

INFORME DEL REVISOR 1
---
{revision_a}
---

INFORME DEL REVISOR 2
---
{revision_b}
---"""


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def leer_contexto(rutas: list[Path]) -> tuple[str, list[str]]:
    """Junta los archivos de contexto en un solo bloque de texto."""
    partes: list[str] = []
    usados: list[str] = []
    for ruta in rutas:
        archivos = (
            sorted(p for p in ruta.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONES_CONTEXTO)
            if ruta.is_dir()
            else [ruta]
        )
        for archivo in archivos:
            try:
                texto = archivo.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as e:
                print(f"  aviso: no se pudo leer {archivo}: {e}", file=sys.stderr)
                continue
            if not texto:
                continue
            partes.append(f"### {archivo.name}\n\n{texto}")
            usados.append(str(archivo))
    return "\n\n".join(partes), usados


def partir_respuesta(texto: str) -> tuple[str, str]:
    """Separa manuscrito y carta. Tolera que el modelo omita las marcas."""

    def entre(inicio: str, fin: str) -> str:
        patron = re.escape(inicio) + r"\s*(.*?)\s*" + re.escape(fin)
        m = re.search(patron, texto, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Marca de apertura sin cierre: tomar hasta la siguiente marca o el final.
        m = re.search(re.escape(inicio) + r"\s*(.*)", texto, re.DOTALL)
        if not m:
            return ""
        resto = m.group(1)
        for otra in (MARCA_MANUSCRITO, MARCA_CARTA, MARCA_FIN_MANUSCRITO, MARCA_FIN_CARTA):
            if otra != inicio:
                resto = resto.split(otra)[0]
        return resto.strip()

    manuscrito = entre(MARCA_MANUSCRITO, MARCA_FIN_MANUSCRITO)
    carta = entre(MARCA_CARTA, MARCA_FIN_CARTA)
    if not manuscrito:
        # Sin marcas utilizables: conservar todo como manuscrito para no perder
        # el trabajo, y dejar constancia en la carta.
        manuscrito = texto.strip()
        carta = carta or (
            "[El modelo no devolvio la carta con las marcas esperadas. "
            "La salida completa quedo guardada como manuscrito; revisala a mano.]"
        )
    return manuscrito, carta


def escribir(ruta: Path, texto: str) -> Path:
    ruta.write_text(texto.rstrip() + "\n", encoding="utf-8")
    return ruta


def registrar(paso: str, r: Respuesta) -> dict:
    print(
        f"  {paso}: {r.modelo}, {r.segundos:.0f}s, "
        f"{r.uso.entrada:,} entrada / {r.uso.salida:,} salida",
        flush=True,
    )
    return {
        "paso": paso,
        "modelo": r.modelo,
        "segundos": round(r.segundos, 1),
        "tokens_entrada": r.uso.entrada,
        "tokens_salida": r.uso.salida,
        "intentos": r.intentos,
    }


# --------------------------------------------------------------------------
# Ciclo
# --------------------------------------------------------------------------


def revisar(cliente: Cliente, modelo: str, manuscrito: str) -> Respuesta:
    """Una revision independiente.

    La conversacion que ve el modelo empieza y termina aqui: consigna de
    revisor y manuscrito. Nada mas.
    """
    return cliente.completar(
        modelo,
        [
            {"role": "system", "content": REVISOR_SISTEMA},
            {"role": "user", "content": REVISOR_USUARIO.format(manuscrito=manuscrito)},
        ],
    )


def ejecutar(args: argparse.Namespace) -> int:
    cliente = Cliente()

    contexto, archivos_contexto = leer_contexto([Path(p) for p in args.contexto])
    borrador = ""
    if args.manuscrito:
        borrador = Path(args.manuscrito).read_text(encoding="utf-8", errors="replace").strip()
    if not contexto and not borrador:
        print(
            "Hace falta al menos --manuscrito o --contexto con algun archivo.",
            file=sys.stderr,
        )
        return 2

    marca_tiempo = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    salida = Path(args.salida) / marca_tiempo
    salida.mkdir(parents=True, exist_ok=True)
    print(f"Corrida en {salida}\n", flush=True)

    sistema_autor = AUTOR_SISTEMA
    if contexto:
        sistema_autor += "\n\nCONTEXTO DEL PROYECTO\n\n" + contexto
        escribir(salida / "00_contexto_del_autor.md", contexto)

    bitacora: list[dict] = []
    artefactos: list[str] = []

    # --- Version 1 -------------------------------------------------------
    if borrador and args.usar_borrador_tal_cual:
        print("v1: se usa el borrador tal cual (--usar-borrador-tal-cual)", flush=True)
        manuscrito = borrador
    else:
        instruccion = (
            "Partes de este borrador. Mejoralo sin cambiar su argumento central "
            f"ni inventar resultados:\n\n---\n{borrador}\n---"
            if borrador
            else "No hay borrador previo: redacta el manuscrito desde el contexto."
        )
        print("v1: redactando con el agente autor...", flush=True)
        r = cliente.completar(
            args.modelo_autor,
            [
                {"role": "system", "content": sistema_autor},
                {
                    "role": "user",
                    "content": AUTOR_REDACTA.format(instruccion_borrador=instruccion),
                },
            ],
        )
        bitacora.append(registrar("v1", r))
        manuscrito = r.texto
    artefactos.append(str(escribir(salida / "v1.md", manuscrito)))

    # --- Rondas ----------------------------------------------------------
    for ronda in range(1, args.rondas + 1):
        print(f"\nRonda {ronda} de {args.rondas}", flush=True)

        print("  revisor A...", flush=True)
        ra = revisar(cliente, args.modelo_revisor_a, manuscrito)
        bitacora.append(registrar(f"ronda{ronda}_revisor_A", ra))
        artefactos.append(
            str(escribir(salida / f"ronda{ronda}_revisor_A_{args.modelo_revisor_a}.md", ra.texto))
        )

        print("  revisor B...", flush=True)
        rb = revisar(cliente, args.modelo_revisor_b, manuscrito)
        bitacora.append(registrar(f"ronda{ronda}_revisor_B", rb))
        artefactos.append(
            str(escribir(salida / f"ronda{ronda}_revisor_B_{args.modelo_revisor_b}.md", rb.texto))
        )

        print("  el autor atiende las dos revisiones...", flush=True)
        rc = cliente.completar(
            args.modelo_autor,
            [
                {"role": "system", "content": sistema_autor},
                {
                    "role": "user",
                    "content": AUTOR_ATIENDE.format(
                        marca_manuscrito=MARCA_MANUSCRITO,
                        marca_fin_manuscrito=MARCA_FIN_MANUSCRITO,
                        marca_carta=MARCA_CARTA,
                        marca_fin_carta=MARCA_FIN_CARTA,
                        manuscrito=manuscrito,
                        revision_a=ra.texto,
                        revision_b=rb.texto,
                    ),
                },
            ],
        )
        bitacora.append(registrar(f"ronda{ronda}_autor", rc))
        manuscrito, carta = partir_respuesta(rc.texto)
        artefactos.append(str(escribir(salida / f"v{ronda + 1}.md", manuscrito)))
        artefactos.append(
            str(escribir(salida / f"ronda{ronda}_carta_de_respuesta.md", carta))
        )

    final = escribir(salida / "FINAL.md", manuscrito)
    artefactos.append(str(final))

    resumen = {
        "corrida": marca_tiempo,
        "endpoint": cliente.endpoint,
        "modelos": {
            "autor": args.modelo_autor,
            "revisor_A": args.modelo_revisor_a,
            "revisor_B": args.modelo_revisor_b,
        },
        "rondas": args.rondas,
        "archivos_de_contexto": archivos_contexto,
        "manuscrito_de_entrada": args.manuscrito or None,
        "tokens_totales": {
            "entrada": cliente.uso_total.entrada,
            "salida": cliente.uso_total.salida,
        },
        "pasos": bitacora,
        "artefactos": artefactos,
    }
    (salida / "resumen.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    total_s = sum(p["segundos"] for p in bitacora)
    print(
        f"\nListo. Version final: {final}\n"
        f"{len(bitacora)} llamadas, {total_s / 60:.1f} min, "
        f"{cliente.uso_total.entrada:,} tokens de entrada y "
        f"{cliente.uso_total.salida:,} de salida.",
        flush=True,
    )
    return 0


def probar(args: argparse.Namespace) -> int:
    cliente = Cliente()
    print(f"Endpoint: {cliente.endpoint}\n")
    fallas = 0
    for papel, modelo in (
        ("autor", args.modelo_autor),
        ("revisor A", args.modelo_revisor_a),
        ("revisor B", args.modelo_revisor_b),
    ):
        sirve, detalle = cliente.probar(modelo)
        print(f"  {'ok  ' if sirve else 'FALLA'}  {papel:<9} {modelo:<16} {detalle}")
        fallas += 0 if sirve else 1
    print()
    return 1 if fallas else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--manuscrito", help="Borrador de partida (.md o .txt).")
    p.add_argument(
        "--contexto",
        nargs="*",
        default=[str(Path(__file__).resolve().parent / "contexto")],
        help="Archivos o carpetas con el contexto que solo ve el autor.",
    )
    p.add_argument("--rondas", type=int, default=2, help="Rondas de revision (por defecto 2).")
    p.add_argument(
        "--salida",
        default=str(Path(__file__).resolve().parent / "corridas"),
        help="Carpeta donde se guarda la corrida.",
    )
    p.add_argument("--modelo-autor", default=AUTOR_POR_DEFECTO)
    p.add_argument("--modelo-revisor-a", default=REVISOR_A_POR_DEFECTO)
    p.add_argument("--modelo-revisor-b", default=REVISOR_B_POR_DEFECTO)
    p.add_argument(
        "--usar-borrador-tal-cual",
        action="store_true",
        help="No dejar que el autor reescriba antes de la ronda 1.",
    )
    p.add_argument("--probar", action="store_true", help="Solo verificar los despliegues.")
    args = p.parse_args(argv)

    if args.rondas < 1:
        print("--rondas debe ser 1 o mas.", file=sys.stderr)
        return 2

    try:
        return probar(args) if args.probar else ejecutar(args)
    except ErrorFoundry as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido. Lo que ya se escribio sigue en la carpeta de la corrida.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
