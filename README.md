# Vigilante de intervals.icu

Cada 5 minutos (de 5:00 a 24:00, hora de Madrid) mira intervals.icu y avisa a dos tareas programadas de Claude por su disparador de API:

- **Recovery:** en cuanto llegan los datos de recuperación del día (VFC, FC en reposo, recovery). Si a las 9:15 entre semana o a las 10:30 el fin de semana aún no han llegado, avisa igual. Si llegan más tarde, antes de las 13:00, vuelve a avisar.
- **Análisis:** cuando entra una salida en bici nueva (más de 35' o 10 km; los trayectos no cuentan) y intervals ya la ha procesado.

No usa ningún servidor ni ordenador propio: corre en GitHub Actions.

## Configuración

En *Settings → Secrets and variables → Actions*:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `INTERVALS_API_KEY` | API key de intervals.icu (Settings → Developer Settings) |
| Secret | `RECOVERY_FIRE_TOKEN` | Token de API de la tarea de recovery (claude.ai/code/routines) |
| Secret | `ANALISIS_FIRE_TOKEN` | Token de API de la tarea de análisis |
| Variable | `RECOVERY_TRIGGER_ID` | ID `trig_…` de la tarea de recovery |
| Variable | `ANALISIS_TRIGGER_ID` | ID `trig_…` de la tarea de análisis |
| Variable | `INTERVALS_ATHLETE_ID` | Opcional (por defecto `0`, el dueño de la API key) |

Mientras falte algo, el vigilante no hace nada.

## Prueba

*Actions → Vigilante intervals.icu → Run workflow* con "Prueba" marcado: lee intervals y dice qué dispararía, sin disparar nada.

## Privacidad

El repositorio es público, así que el código no imprime ningún dato personal en los registros, y `state/state.json` solo guarda huellas HMAC (con la API key como clave), no fechas ni IDs.
