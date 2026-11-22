#!/usr/bin/env python3
"""Vigilante de intervals.icu -> tareas programadas de Claude.

Cada pocos minutos (GitHub Actions) mira en intervals.icu:
  1. Si ya han llegado los datos de recuperación de hoy (VFC, FC en reposo,
     recovery). Dispara la tarea de recovery con la recovery más reciente y la
     vuelve a disparar si después llega otra distinta (p. ej. Whoop sube una
     provisional de madrugada). Si a la hora tope no hay datos, avisa igual.
  2. Si hay una salida nueva en bici (no trayecto) ya procesada por intervals.
     Si la hay, dispara la tarea de análisis con su ID.

El repositorio es público, así que:
  - Los registros (logs) no muestran ningún dato personal: solo recuentos.
  - El estado guardado en state/state.json solo contiene huellas HMAC
    (con la API key como clave), no fechas ni IDs legibles.

Solo usa la biblioteca estándar de Python.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
ICU = os.environ.get("ICU_BASE") or "https://intervals.icu/api/v1"
FIRE = os.environ.get("FIRE_URL_TEMPLATE") or "https://api.anthropic.com/v1/claude_code/routines/{}/fire"
STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")

# Ventanas (hora local de Madrid)
RECOVERY_DESDE = dt.time(6, 0)       # nunca antes (no despertarle con avisos)
PROVISIONAL_HASTA = dt.time(7, 30)   # antes de esta hora, <6 h de sueño = provisional
SUENO_PROVISIONAL_S = 6 * 3600
PASADAS_ESTABLES = 2                 # valores iguales en 2 pasadas seguidas (~5')
MAX_DISPAROS_RECOVERY = 4
CAMPOS_RECOVERY = ("hrv", "restingHR", "readiness", "sleepSecs", "sleepScore")
RECOVERY_HASTA = dt.time(13, 0)
FALLBACK_LABORABLE = dt.time(9, 15)   # lunes a viernes
FALLBACK_FINDE = dt.time(10, 30)      # sábado y domingo

# Qué cuenta como salida (los trayectos a la oficina son de 10-25' y 2-7 km)
MIN_MOVING_S = 35 * 60
MIN_DISTANCE_M = 10_000
TIPOS_EXCLUIDOS = {"EBikeRide"}
VISTAS_PARA_DISPARAR_SIN_CARGA = 3   # si intervals no calcula la carga


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


API_KEY = env("INTERVALS_API_KEY")
ATHLETE = env("INTERVALS_ATHLETE_ID", "0")
DRY_RUN = env("DRY_RUN").lower() in {"1", "true", "yes", "si", "sí"}
ROUTINES = {
    "recovery": (env("RECOVERY_TRIGGER_ID"), env("RECOVERY_FIRE_TOKEN")),
    "analisis": (env("ANALISIS_TRIGGER_ID"), env("ANALISIS_FIRE_TOKEN")),
}


def log(msg):
    # Nunca imprimir datos personales: el log de un repo público es público.
    print(msg, flush=True)


def huella(valor):
    return hmac.new(API_KEY.encode(), str(valor).encode(), hashlib.sha256).hexdigest()[:20]


# ---------------------------------------------------------------- intervals

def icu_get(path, **params):
    url = f"{ICU}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    token = base64.b64encode(f"API_KEY:{API_KEY}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "User-Agent": "entrenos-vigilante/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "null")


def wellness_de(fecha):
    filas = icu_get(f"/athlete/{ATHLETE}/wellness.json", oldest=fecha, newest=fecha,
                    fields="id,hrv,restingHR,readiness,sleepSecs,sleepScore")
    for fila in filas or []:
        if fila.get("id") == fecha:
            return fila
    return {}


def actividades(desde, hasta):
    return icu_get(f"/athlete/{ATHLETE}/activities", oldest=desde, newest=hasta) or []


# ---------------------------------------------------------------- Claude

def disparar(clave, texto):
    trigger_id, token = ROUTINES[clave]
    if DRY_RUN:
        log(f"[prueba] se dispararía la tarea '{clave}'")
        return True
    req = urllib.request.Request(
        FIRE.format(trigger_id),
        data=json.dumps({"text": texto}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        log(f"ERROR: la tarea '{clave}' respondió {e.code}")
        if e.code in (401, 403, 404):
            log("  -> revisa el token y el ID de la tarea en los secretos del repo")
        return False
    except Exception as e:  # red, timeout...
        log(f"ERROR: no se pudo disparar '{clave}': {type(e).__name__}")
        return False
    log(f"Tarea '{clave}' disparada" if ok else f"ERROR: '{clave}' no confirmó el disparo")
    return ok


# ---------------------------------------------------------------- formato

def fmt_dur(seg):
    if not seg:
        return "-"
    h, m = divmod(int(seg) // 60, 60)
    return f"{h}:{m:02d}"


def fmt_val(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return str(v)


def es_salida(a):
    tipo = a.get("type") or ""
    if "Ride" not in tipo or tipo in TIPOS_EXCLUIDOS:
        return False
    if a.get("commute"):
        return False
    mov = a.get("moving_time") or 0
    dist = a.get("distance") or a.get("icu_distance") or 0
    return mov >= MIN_MOVING_S or dist >= MIN_DISTANCE_M


# ---------------------------------------------------------------- lógica

def revisar_recovery(estado, ahora, inicial, salio_hoy):
    """Dispara la tarea de recovery con la recovery MÁS RECIENTE de hoy.

    - Espera a que los valores no cambien en dos pasadas seguidas (~5').
    - Antes de las 7:30, si el sueño es de menos de 6 h, lo trata como provisional
      (p. ej. Whoop sube una recovery a las 4:00 porque se despertó) y espera.
    - Si después llega una recovery distinta, vuelve a disparar como ACTUALIZACIÓN,
      hasta que salga a entrenar o hasta las 13:00 (máximo 4 disparos con datos al día).
    - Si a la hora tope no hay datos, avisa con SIN DATOS.
    """
    hoy = ahora.date().isoformat()
    h = huella("w" + hoy)
    reg = estado.setdefault("w", {}).setdefault(h, {})
    for k, v in {"firma": "", "pendiente": "", "estables": 0, "disparos": 0, "sin_datos": False}.items():
        reg.setdefault(k, v)
    if not (RECOVERY_DESDE <= ahora.time() < RECOVERY_HASTA) or salio_hoy:
        return False

    w = wellness_de(hoy)
    if any(w.get(c) is not None for c in ("hrv", "restingHR", "readiness")):
        firma = huella("v" + "|".join(str(w.get(c)) for c in CAMPOS_RECOVERY))
        if firma == reg["firma"]:
            return False
        if firma == reg["pendiente"]:
            reg["estables"] += 1
        else:
            reg["pendiente"], reg["estables"] = firma, 1
        if inicial:
            # Primera ejecución del vigilante: lo de hoy ya lo revisaron las tareas antiguas.
            reg["firma"] = firma
            return False
        if reg["estables"] < PASADAS_ESTABLES or reg["disparos"] >= MAX_DISPAROS_RECOVERY:
            return False
        sueno = w.get("sleepSecs")
        if ahora.time() < PROVISIONAL_HASTA and sueno is not None and sueno < SUENO_PROVISIONAL_S:
            return False  # probablemente provisional: esperar a que siga durmiendo o a las 7:30

        valores = (
            f"hrv={fmt_val(w.get('hrv'))}, restingHR={fmt_val(w.get('restingHR'))}, "
            f"readiness={fmt_val(w.get('readiness'))}, sueño={fmt_dur(sueno)}, "
            f"sleepScore={fmt_val(w.get('sleepScore'))}"
        )
        if reg["firma"]:
            texto = (
                f"AVISO DEL VIGILANTE · RECOVERY · ACTUALIZACIÓN · {hoy} {ahora:%H:%M} (Europe/Madrid)\n"
                f"La recovery de hoy ha cambiado en intervals.icu después de un aviso anterior. "
                f"Valores más recientes: {valores}. Decide con estos y corrige lo que se hizo antes si hace falta."
            )
        else:
            texto = (
                f"AVISO DEL VIGILANTE · RECOVERY · {hoy} {ahora:%H:%M} (Europe/Madrid)\n"
                f"Han llegado a intervals.icu los datos de recuperación de hoy: {valores}."
            )
            if reg["sin_datos"]:
                texto += " (Antes ya se envió un aviso de SIN DATOS: estos han llegado después.)"
        if disparar("recovery", texto):
            reg["firma"] = firma
            reg["disparos"] += 1
            return True
        raise RuntimeError("fallo al disparar la tarea de recovery")

    tope = FALLBACK_FINDE if ahora.weekday() >= 5 else FALLBACK_LABORABLE
    if reg["sin_datos"] or reg["firma"] or ahora.time() < tope:
        return False
    if inicial:
        reg["sin_datos"] = True
        return False
    texto = (
        f"AVISO DEL VIGILANTE · RECOVERY · SIN DATOS · {hoy} {ahora:%H:%M} (Europe/Madrid)\n"
        f"A esta hora aún no han llegado a intervals.icu la VFC, la FC en reposo ni la recovery de hoy. "
        f"Si llegan antes de las {RECOVERY_HASTA:%H:%M}, recibirás otro aviso."
    )
    if disparar("recovery", texto):
        reg["sin_datos"] = True
        return True
    raise RuntimeError("fallo al disparar la tarea de recovery (sin datos)")


def salio_hoy(lista, hoy):
    return any(es_salida(a) and str(a.get("start_date_local", "")).startswith(hoy) for a in lista)


def revisar_actividades(estado, ahora, inicial, lista):
    hoy = ahora.date()
    vistas = estado.setdefault("a", {})
    hechas = estado.setdefault("hechas", [])
    nuevas, cambio, strava = [], False, 0

    for a in lista:
        if (a.get("source") or "").upper() == "STRAVA" and a.get("_note"):
            strava += 1
            continue
        if not es_salida(a):
            continue
        h = huella("a" + str(a.get("id")))
        if h in hechas:
            continue
        if inicial:
            hechas.append(h)
            cambio = True
            continue
        vistas[h] = vistas.get(h, 0) + 1
        cambio = True
        if a.get("icu_training_load") is not None or vistas[h] >= VISTAS_PARA_DISPARAR_SIN_CARGA:
            nuevas.append((h, a))

    if strava:
        log(f"Aviso: {strava} actividad(es) vienen de Strava y la API no las enseña.")

    if nuevas:
        lineas = []
        for _, a in nuevas:
            dist = (a.get("distance") or a.get("icu_distance") or 0) / 1000
            lineas.append(
                f"- {a.get('id')} · {a.get('type')} · inicio {a.get('start_date_local')} · "
                f"{fmt_dur(a.get('moving_time'))} en movimiento · {dist:.1f} km · "
                f"carga {fmt_val(a.get('icu_training_load'))}"
            )
        texto = (
            f"AVISO DEL VIGILANTE · ACTIVIDAD NUEVA · {hoy.isoformat()} {ahora:%H:%M} (Europe/Madrid)\n"
            f"Acaban de llegar a intervals.icu y ya están procesadas:\n" + "\n".join(lineas)
        )
        if not disparar("analisis", texto):
            raise RuntimeError("fallo al disparar la tarea de análisis")
        for h, _ in nuevas:
            hechas.append(h)
            vistas.pop(h, None)
        log(f"{len(nuevas)} actividad(es) nueva(s) enviadas a análisis")
        return True
    return cambio


def main():
    falta = [n for n, v in {
        "INTERVALS_API_KEY": API_KEY,
        "RECOVERY_TRIGGER_ID": ROUTINES["recovery"][0],
        "RECOVERY_FIRE_TOKEN": ROUTINES["recovery"][1],
        "ANALISIS_TRIGGER_ID": ROUTINES["analisis"][0],
        "ANALISIS_FIRE_TOKEN": ROUTINES["analisis"][1],
    }.items() if not v]
    if falta and not (DRY_RUN and API_KEY):
        log("Vigilante sin configurar todavía. Faltan: " + ", ".join(falta))
        return 0

    try:
        with open(STATE_FILE) as f:
            estado = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        estado = {}
    antes = json.dumps(estado, sort_keys=True)
    inicial = not estado.get("iniciado")

    ahora = dt.datetime.now(TZ)
    if env("FAKE_NOW"):
        ahora = dt.datetime.fromisoformat(env("FAKE_NOW")).replace(tzinfo=TZ)

    errores = 0
    hoy = ahora.date()
    lista = None
    try:
        lista = actividades((hoy - dt.timedelta(days=1)).isoformat(), hoy.isoformat())
    except urllib.error.HTTPError as e:
        errores += 1
        log(f"ERROR leyendo actividades en intervals: HTTP {e.code}")
    except Exception as e:
        errores += 1
        log(f"ERROR leyendo actividades: {type(e).__name__}")

    try:
        if lista is None:
            raise RuntimeError("sin lista de actividades")
        disp = revisar_recovery(estado, ahora, inicial, salio_hoy(lista, hoy.isoformat()))
        log("Recovery: aviso enviado" if disp else "Recovery: nada nuevo")
    except urllib.error.HTTPError as e:
        errores += 1
        log(f"ERROR leyendo wellness en intervals: HTTP {e.code}")
    except Exception as e:
        errores += 1
        log(f"ERROR en recovery: {e if isinstance(e, RuntimeError) else type(e).__name__}")

    try:
        if lista is not None:
            revisar_actividades(estado, ahora, inicial, lista)
            log("Actividades: revisadas")
    except Exception as e:
        errores += 1
        log(f"ERROR en actividades: {e if isinstance(e, RuntimeError) else type(e).__name__}")

    if inicial and not errores:
        estado["iniciado"] = True
        log("Primera ejecución: estado inicial guardado (no se dispara nada de lo que ya había).")

    # Poda: solo los últimos días
    estado["w"] = dict(list(estado.get("w", {}).items())[-10:])
    estado["hechas"] = estado.get("hechas", [])[-300:]
    estado["a"] = dict(list(estado.get("a", {}).items())[-50:])

    # Fallos seguidos: avisar (salida con error) solo al primero y luego ~cada hora,
    # para que GitHub no mande un correo cada 5 minutos.
    fallos = estado.get("fallos", 0) + 1 if errores else 0
    estado["fallos"] = fallos

    if not DRY_RUN and json.dumps(estado, sort_keys=True) != antes:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(estado, f, indent=1, sort_keys=True)
            f.write("\n")
        log("Estado actualizado")

    return 1 if fallos == 1 or (fallos and fallos % 12 == 0) else 0


if __name__ == "__main__":
    sys.exit(main())
