# Ciclo automatico autor / revisores

Un agente **autor** con todo el contexto del proyecto escribe una version del
manuscrito. Dos **revisores independientes**, cada uno en un modelo distinto y
sin ningun contexto previo, la revisan por separado. El autor atiende las dos
revisiones, produce la siguiente version y una carta de respuesta. Se repite el
numero de rondas que se pida (por defecto 2) y al final queda `FINAL.md` para
que lo revises tu.

Todo corre contra los despliegues de Microsoft Foundry del proyecto
`mf-mz-mindtrack`, asi que el gasto cae en la suscripcion **Academia -
Mindtrack (S.807)** y se ve en Cost Management junto con el resto del grupo de
recursos.

## Por que dos modelos distintos

Dos revisores del mismo modelo se equivocan igual: comparten sesgos, puntos
ciegos y manias de redaccion, asi que la segunda revision confirma la primera en
vez de contrastarla. Por eso el revisor A y el revisor B salen de laboratorios
distintos:

| Papel | Despliegue por defecto | Proveedor |
|---|---|---|
| Autor | `gpt-6.2-astra` | OpenAI |
| Revisor A | `gpt-6.2-astra` | OpenAI |
| Revisor B | `Kimi-K3.1-Pro` | Moonshot AI |

Los dos son modelos frontera de la coleccion **Direct from Azure**, que se
factura con el resto del consumo de Azure. Los modelos de Anthropic quedan
fuera a proposito: su ficha en Foundry advierte que *"Azure credits cannot be
applied to Claude models"*, de modo que no sirven para ejercer el credito del
convenio.

Para cambiar de modelo basta pasar otro despliegue:

```
--modelo-revisor-b DeepSeek-V4-Pro
```

## Que quiere decir "revisor independiente" aqui

No es una forma de hablar. Cada revision es una llamada nueva cuyo contenido
completo es la consigna de revisor mas el texto del manuscrito. El revisor no
recibe:

- el contexto del proyecto que si tiene el autor,
- las versiones anteriores del manuscrito,
- la revision del otro revisor,
- su propia revision de la ronda anterior,
- la carta de respuesta del autor.

Si algo no esta en el manuscrito, para el revisor no existe, que es justo lo
que hace util la revision. `prueba_offline.py` comprueba esas cinco condiciones
en cada corrida de prueba.

## Preparacion

Una sola vez, guarda la llave de la API en el `.env` de la raiz del
repositorio. El comando no la muestra en pantalla ni la deja en el historial:

```bash
read -rs -p 'Llave: ' K && printf '\nAZURE_FOUNDRY_API_KEY=%s\n' "$K" >> .env && unset K
```

La llave esta en la pagina del proyecto en Foundry, en el campo *API key*, con
su boton de copiar. No hace falta instalar nada: los scripts usan solo la
biblioteca estandar de Python 3.

Comprueba que los tres despliegues responden:

```bash
python3 revision_manuscrito/ciclo_revision.py --probar
```

## Uso

Pon en `revision_manuscrito/contexto/` lo que el autor deba conocer y nadie
mas: el protocolo, el registro del convenio, la revista a la que vas, los
criterios del comite, notas de coautores. Admite `.md`, `.txt`, `.csv` y
`.json`.

```bash
python3 revision_manuscrito/ciclo_revision.py \
    --manuscrito borrador.md \
    --contexto revision_manuscrito/contexto \
    --rondas 2
```

Opciones que suelen hacer falta:

| Opcion | Para que |
|---|---|
| `--rondas N` | Mas o menos rondas de ida y vuelta. |
| `--usar-borrador-tal-cual` | Que la ronda 1 revise tu texto sin que el autor lo reescriba antes. |
| `--modelo-autor`, `--modelo-revisor-a`, `--modelo-revisor-b` | Cambiar de despliegue. |
| `--salida RUTA` | Otra carpeta para la corrida. |

Sin `--manuscrito`, el autor redacta desde cero con el contexto.

## Que queda al terminar

Cada corrida crea `corridas/AAAAMMDD_HHMMSS/` con todo el rastro, no solo el
resultado:

```
00_contexto_del_autor.md          lo que recibio el autor
v1.md                             version que fue a la ronda 1
ronda1_revisor_A_gpt-6.2-astra.md
ronda1_revisor_B_Kimi-K3.1-Pro.md
ronda1_carta_de_respuesta.md      punto por punto, que cambio y que no
v2.md
ronda2_...                        lo mismo para la ronda 2
v3.md
FINAL.md                          lo que revisas tu
resumen.json                      modelos, tokens, tiempos por paso
```

Cada archivo se escribe en cuanto se produce, asi que si la corrida se corta a
la mitad lo anterior no se pierde.

## Cuanto tarda y cuanto gasta

Con dos rondas son siete llamadas: una para la version 1, y por ronda dos
revisiones y una respuesta del autor. Para un manuscrito de unas 8,000 palabras
la corrida completa suele quedar en el orden de **10 a 25 minutos** y de unos
**100,000 tokens de entrada y 50,000 de salida** en total. Minutos, no horas:
puedes lanzarla, ir por un cafe y volver.

`resumen.json` trae los tokens reales de cada paso. Las tarifas por token estan
en la ficha de cada modelo en Foundry, en *View pricing*, y el gasto agregado en
Cost Management con alcance al grupo de recursos `rg-mf-mz-mindtrack`.

## Limites que conviene tener presentes

- **Esto no sustituye la revision por pares.** Son modelos de lenguaje leyendo
  un texto, no expertos del campo con responsabilidad sobre el dictamen. Sirve
  para llegar a la revision real con menos defectos evidentes.
- **El autor no inventa datos, pero puede equivocarse.** Si le falta un dato que
  solo tienes tu, lo marca como `[PENDIENTE: ...]`. Busca esas marcas en
  `FINAL.md` antes de hacer nada con el texto.
- **Compara `v1.md` con `FINAL.md`** antes de dar por bueno el resultado. En dos
  rondas el texto cambia bastante y conviene ver que no se perdio nada tuyo.
- **Gobernanza de datos.** Los despliegues son Global Standard: la peticion
  puede procesarse fuera de la geografia de Azure del recurso, aunque el
  almacenamiento se queda en ella. Para un manuscrito propio no hay problema;
  para texto del corpus clinico aplica la nota de gobernanza del proyecto y
  habria que usar un despliegue Data Zone.
- Las corridas quedan fuera del control de versiones (`.gitignore`), porque
  contienen el texto completo del manuscrito.

## Archivos

| Archivo | Que hace |
|---|---|
| `ciclo_revision.py` | El ciclo y las consignas de autor y revisor. |
| `foundry.py` | Cliente HTTP minimo con reintentos y conteo de tokens. |
| `prueba_offline.py` | Prueba el ciclo sin red ni gasto; verifica la independencia. |
