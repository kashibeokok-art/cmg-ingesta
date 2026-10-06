"""Calendario del SEN: largo de los dias segun el cambio de hora de Chile.

Todo se DERIVA de la base de zonas horarias (`zoneinfo`). No hay ninguna fecha
escrita a mano, asi que funciona para cualquier año sin mantener una lista.

Reglas que implementa (ver CLAUDE.md 4.5.3 a 4.5.5):
    dia normal                  -> 24 horas, 96 cuartos
    primer sabado de abril      -> 25 horas, 100 cuartos (la 23:00 se repite)
    domingo DST de septiembre   -> 23 horas,  92 cuartos (la hora 0 no existe)
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
UTC = ZoneInfo("UTC")

CUARTOS_POR_HORA = 4
CUARTOS_NORMAL = 96
MINUTOS_VALIDOS = (0, 15, 30, 45)

#: Indice que recibe la SEGUNDA hora 23:00 del dia de 25 horas de abril.
#: Convencion de la base historica y de ppa-pipeline ADR-002.
HORA_EXTRA = 24


def _medianoche_utc(dia: date) -> datetime:
    """La medianoche local de `dia`, expresada en UTC.

    UTC es el unico reloj que no salta. Restar dos medianoches LOCALES daria
    siempre 24 h, porque son etiquetas de reloj de pared; en UTC la diferencia
    mide tiempo fisico transcurrido, y ahi aparecen las 23 o las 25 horas.
    """
    return datetime(dia.year, dia.month, dia.day, tzinfo=TZ).astimezone(UTC)


def horas_del_dia(dia: date) -> int:
    """Cuantas horas dura ese dia en hora local de Chile: 23, 24 o 25."""
    duracion = _medianoche_utc(dia + timedelta(days=1)) - _medianoche_utc(dia)
    return int(duracion / timedelta(hours=1))


def cuartos_esperados(dia: date) -> int:
    """Cuantos intervalos de 15 minutos debe tener ese dia: 92, 96 o 100."""
    return horas_del_dia(dia) * CUARTOS_POR_HORA


def es_dia_largo(dia: date) -> bool:
    """El dia de 25 horas: primer sabado de abril."""
    return horas_del_dia(dia) == 25


def es_dia_corto(dia: date) -> bool:
    """El dia de 23 horas: domingo del cambio de septiembre."""
    return horas_del_dia(dia) == 23


def horas_inexistentes(dia: date) -> list[int]:
    """Horas de reloj que no existen ese dia en Chile.

    Solo pasa en el dia corto de septiembre, donde el reloj salta de 00:00 a
    01:00 y la hora 0 no ocurre. El truco es pedirle la hora a `zoneinfo` y
    volver: si una hora no existe, la ida y vuelta por UTC la corre a otra.

    Los datos del CEN publican esa hora igual, rellenada con 0,00 (CLAUDE.md
    4.5.4), asi que hay que descartarla.
    """
    resultado: list[int] = []
    for hora in range(24):
        local = datetime(dia.year, dia.month, dia.day, hora, tzinfo=TZ)
        vuelta = local.astimezone(UTC).astimezone(TZ)
        if vuelta.hour != hora:
            resultado.append(hora)
    return resultado


def horas_esperadas(dia: date) -> list[int]:
    """Los indices de hora que ese dia debe tener, en orden.

        dia normal   -> [0, 1, ..., 23]
        abril        -> [0, 1, ..., 23, 24]   la 24 es la SEGUNDA 23:00
        septiembre   -> [1, 2, ..., 23]       sin la hora 0

    Sirve para validar hora por hora, no solo el total del dia: un dia podria
    tener 96 cuartos y estar mal repartido (una hora con 3 y otra con 5).
    """
    faltan = set(horas_inexistentes(dia))
    horas = [hora for hora in range(24) if hora not in faltan]
    if es_dia_largo(dia):
        horas.append(HORA_EXTRA)
    return horas
