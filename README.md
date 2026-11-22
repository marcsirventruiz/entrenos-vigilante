# Vigilante de intervals.icu

Cada 5 minutos (de 5:00 a 24:00, hora de Madrid) mira intervals.icu y avisa a dos tareas programadas de Claude por su disparador de API:

- **Recovery:** con la recovery más reciente del día (VFC, FC en reposo, recovery, sueño), desde las 6:00, cuando los valores llevan 2 pasadas sin cambiar. Antes de las 7:30, si el sueño es de menos de 6 h, la trata como provisional y espera. Si después llega otra recovery distinta, vuelve a avisar como ACTUALIZACIÓN (hasta que sale a entrenar o las 13:00; como mucho 4 avisos al día). Si a las 9:15 entre semana o a las 10:30 el fin de semana no hay datos, avisa igual.
- **Análisis:** cuando entra una salida en bici nueva (más de 35' o 10 km; los trayectos no cuentan) y intervals ya la ha procesado.

No usa ningún servidor ni ordenador propio: corre en GitHub Actions.

## Configuración

En *Settings → Secrets and variables → Actions*:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `INTERVALS_API_KEY` | API key de intervals.icu (Settings → Developer Settings) |
| Secret | `RECOVERY_FIRE_TOKEN` | Token de API de la tarea de recovery (claude.ai/code/routines) |
| Secret | `ANALISIS_FIRE_TOKEN` | Token de API de la tarea de análisis |
| Variable | `RECOVERY_TRIGGER_ID` | Opcional: ID `trig_…` de la tarea de recovery (por defecto, el del workflow) |
| Variable | `ANALISIS_TRIGGER_ID` | Opcional: ID `trig_…` de la tarea de análisis (por defecto, el del workflow) |
| Variable | `INTERVALS_ATHLETE_ID` | Opcional (por defecto `0`, el dueño de la API key) |

Mientras falte algo, el vigilante no hace nada.

## Prueba

*Actions → Vigilante intervals.icu → Run workflow* con "Prueba" marcado: lee intervals y dice qué dispararía, sin disparar nada.

## Privacidad

El repositorio es público, así que el código no imprime ningún dato personal en los registros, y `state/state.json` solo guarda huellas HMAC (con la API key como clave), no fechas ni IDs.
