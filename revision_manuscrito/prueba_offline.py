#!/usr/bin/env python3
"""Prueba del ciclo sin tocar la red ni gastar tokens.

Sustituye la llamada al modelo por una funcion falsa y verifica lo que
importa: que el ciclo produzca todos los artefactos y, sobre todo, que los
revisores sean de verdad independientes, es decir que en su llamada no
aparezca ni el contexto del proyecto, ni la revision del otro revisor, ni
nada de las rondas anteriores.

    python3 revision_manuscrito/prueba_offline.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import foundry  # noqa: E402
import ciclo_revision as ciclo  # noqa: E402

SECRETO_DE_CONTEXTO = "DATO-SOLO-DEL-AUTOR-884213"
MARCA_REVISOR_A = "HUELLA-REVISOR-A-001"
MARCA_REVISOR_B = "HUELLA-REVISOR-B-002"

llamadas: list[dict] = []


def completar_falso(self, modelo, mensajes, max_tokens_salida=None):
    """Responde como responderia el servicio, y deja constancia de la llamada."""
    texto_entrante = "\n".join(m["content"] for m in mensajes)
    llamadas.append({"modelo": modelo, "mensajes": mensajes, "texto": texto_entrante})

    es_revisor = ciclo.REVISOR_SISTEMA[:60] in texto_entrante
    if es_revisor:
        huella = MARCA_REVISOR_A if modelo == "modelo-A" else MARCA_REVISOR_B
        cuerpo = f"Recomendacion: revision mayor.\n\n{huella}\n\n1. Falta el tamano de muestra."
    elif ciclo.MARCA_MANUSCRITO in texto_entrante:
        cuerpo = (
            f"{ciclo.MARCA_MANUSCRITO}\n"
            f"# Manuscrito revisado\n\nTexto atendiendo a los revisores.\n"
            f"{ciclo.MARCA_FIN_MANUSCRITO}\n"
            f"{ciclo.MARCA_CARTA}\n"
            f"R1.1 Corregido en Metodos.\nR2.1 No procede, y esta es la razon.\n"
            f"{ciclo.MARCA_FIN_CARTA}"
        )
    else:
        cuerpo = "# Manuscrito v1\n\nPrimera version redactada por el autor."

    self.uso_total = self.uso_total + foundry.Uso(10, 20)
    return foundry.Respuesta(
        texto=cuerpo, modelo=modelo, uso=foundry.Uso(10, 20), segundos=0.01
    )


def main() -> int:
    foundry.Cliente.completar = completar_falso
    foundry.Cliente.__post_init__ = lambda self: setattr(self, "endpoint", "falso") or None

    fallas: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "contexto").mkdir()
        (tmp / "contexto" / "proyecto.md").write_text(
            f"El proyecto mide X con Y. {SECRETO_DE_CONTEXTO}", encoding="utf-8"
        )
        (tmp / "borrador.md").write_text("# Borrador\n\nTexto inicial.", encoding="utf-8")

        codigo = ciclo.main(
            [
                "--manuscrito", str(tmp / "borrador.md"),
                "--contexto", str(tmp / "contexto"),
                "--rondas", "2",
                "--salida", str(tmp / "corridas"),
                "--modelo-autor", "modelo-autor",
                "--modelo-revisor-a", "modelo-A",
                "--modelo-revisor-b", "modelo-B",
            ]
        )
        if codigo != 0:
            fallas.append(f"el ciclo devolvio codigo {codigo}")

        corrida = next((tmp / "corridas").iterdir())
        esperados = [
            "00_contexto_del_autor.md", "v1.md", "v2.md", "v3.md", "FINAL.md",
            "ronda1_revisor_A_modelo-A.md", "ronda1_revisor_B_modelo-B.md",
            "ronda1_carta_de_respuesta.md",
            "ronda2_revisor_A_modelo-A.md", "ronda2_revisor_B_modelo-B.md",
            "ronda2_carta_de_respuesta.md", "resumen.json",
        ]
        for nombre in esperados:
            if not (corrida / nombre).is_file():
                fallas.append(f"falta el artefacto {nombre}")

        # --- lo importante: independencia de los revisores ----------------
        revisiones = [c for c in llamadas if ciclo.REVISOR_SISTEMA[:60] in c["texto"]]
        if len(revisiones) != 4:
            fallas.append(f"se esperaban 4 revisiones y hubo {len(revisiones)}")
        for i, c in enumerate(revisiones, 1):
            if SECRETO_DE_CONTEXTO in c["texto"]:
                fallas.append(f"revision {i}: recibio el contexto del proyecto")
            if len(c["mensajes"]) != 2:
                fallas.append(f"revision {i}: la conversacion traia historial")
            otra = MARCA_REVISOR_B if c["modelo"] == "modelo-A" else MARCA_REVISOR_A
            if otra in c["texto"]:
                fallas.append(f"revision {i}: vio la revision del otro revisor")
            propia = MARCA_REVISOR_A if c["modelo"] == "modelo-A" else MARCA_REVISOR_B
            if propia in c["texto"]:
                fallas.append(f"revision {i}: vio su propia revision anterior")
            if "carta de respuesta" in c["texto"].lower():
                fallas.append(f"revision {i}: vio la carta de respuesta")

        # --- el autor si debe tener el contexto en todas sus llamadas -----
        del_autor = [c for c in llamadas if c["modelo"] == "modelo-autor"]
        if len(del_autor) != 3:
            fallas.append(f"se esperaban 3 llamadas del autor y hubo {len(del_autor)}")
        for i, c in enumerate(del_autor, 1):
            if SECRETO_DE_CONTEXTO not in c["texto"]:
                fallas.append(f"llamada {i} del autor: le falto el contexto del proyecto")

        # --- separacion de manuscrito y carta -----------------------------
        final = (corrida / "FINAL.md").read_text(encoding="utf-8")
        carta = (corrida / "ronda2_carta_de_respuesta.md").read_text(encoding="utf-8")
        if ciclo.MARCA_MANUSCRITO in final or "R1.1" in final:
            fallas.append("FINAL.md quedo contaminado con marcas o con la carta")
        if "R1.1" not in carta:
            fallas.append("la carta de respuesta no se separo bien")

        resumen = json.loads((corrida / "resumen.json").read_text(encoding="utf-8"))
        if resumen["rondas"] != 2 or len(resumen["pasos"]) != 7:
            fallas.append("el resumen.json no cuadra con la corrida")

    print()
    if fallas:
        for f in fallas:
            print(f"  FALLA: {f}")
        print(f"\n{len(fallas)} fallas.")
        return 1
    print("  Todo bien: artefactos completos, revisores independientes,")
    print("  autor con contexto, manuscrito y carta separados.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
