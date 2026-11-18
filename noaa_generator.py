"""Official NOAA NOMADS GFS 0.25 / GFS Wave GRIB2 to ecCodes GRIB1.

No interpolation or invented values. The output grid is the model's native
subset grid, not a synthetic 5x5 grid. NOMADS requests paced >=10 seconds.
"""
import io
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

try:
    import eccodes as ec
except ImportError:
    ec = None

from grib_generator import ForecastError, _message

ALLOWED_RADIUS = (30, 60, 100, 150, 200)
ALLOWED_HOURS = (24, 48, 72, 96, 120, 168)
BASE = 'https://nomads.ncep.noaa.gov/cgi-bin/'
_LAST_REQUEST = 0.0
_COOLDOWN_UNTIL = 0.0
_LOCK = threading.Lock()


def bounds(lat, lon, radius):
    if not (-65 <= lat <= 15 and -85 <= lon <= -25):
        raise ForecastError('Posição fora da região configurada para o Atlântico Sul', 422)
    if radius not in ALLOWED_RADIUS:
        raise ForecastError('Raio inválido', 422)
    arc = radius * 1852 / 6371008.8
    dlat = math.degrees(arc)
    dlon = math.degrees(math.asin(min(1., math.sin(arc) / math.cos(math.radians(lat)))))
    # NOAA's grid is 0.25 degrees: expand conservatively so >= 2x2 cells
    # while preserving actual geographic coordinates of downloaded cells.
    dlat = max(dlat, .35)
    dlon = max(dlon, .35)
    return {'leftlon': round(lon-dlon, 4), 'rightlon': round(lon+dlon, 4),
            'toplat': round(lat+dlat, 4), 'bottomlat': round(lat-dlat, 4)}


def cycle_candidates(now=None):
    now = now or datetime.now(timezone.utc)
    anchor = now.replace(hour=(now.hour//6)*6, minute=0, second=0, microsecond=0)
    # model dissemination typically lags the clock; probe completed cycles
    return [anchor-timedelta(hours=6*i) for i in (1, 2, 3)]


def make_url(cycle, lead, bounds_, wave=False):
    hh = cycle.strftime('%H')
    date = cycle.strftime('%Y%m%d')
    if wave:
        endpoint = 'filter_gfswave.pl'
        filename = f'gfswave.t{hh}z.global.0p25.f{lead:03d}.grib2'
        directory = f'/gfs.{date}/{hh}/wave/gridded'
        vars_ = {'var_HTSGW': 'on', 'lev_surface': 'on'}
    else:
        endpoint = 'filter_gfs_0p25.pl'
        filename = f'gfs.t{hh}z.pgrb2.0p25.f{lead:03d}'
        directory = f'/gfs.{date}/{hh}/atmos'
        vars_ = {'var_UGRD': 'on', 'var_VGRD': 'on', 'lev_10_m_above_ground': 'on'}
    params = {'file': filename, 'dir': directory, 'subregion': '', **vars_, **bounds_}
    return BASE + endpoint + '?' + urllib.parse.urlencode(params)


def get_grib2(url):
    global _LAST_REQUEST, _COOLDOWN_UNTIL
    with _LOCK:
        if time.monotonic() < _COOLDOWN_UNTIL:
            raise ForecastError("NOAA em intervalo de bloqueio HTTP 429; reutilize previsão salva.",429)
        wait = 10.1 - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST = time.monotonic()
    request = urllib.request.Request(url, headers={'User-Agent':'PainelDeBordo-NOAA-GRIB/1.0 (responsible request spacing)'})
    try:
        with urllib.request.urlopen(request, timeout=45) as resp:
            if resp.status != 200:
                raise ForecastError(f'NOAA HTTP {resp.status}', 502)
            data = resp.read(15_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry = exc.headers.get('Retry-After')
            delay = 120
            if retry:
                try: delay = max(10, min(3600, int(retry)))
                except ValueError:
                    try: delay = max(10, min(3600, int((parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())))
                    except (TypeError, ValueError, OverflowError): pass
            with _LOCK:
                _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.monotonic()+delay)
            raise ForecastError(f'NOAA limitou as consultas (429). Retente após {delay} s.', 429) from exc
        raise ForecastError(f'NOAA indisponível (HTTP {exc.code})', 503) from exc
    except (TimeoutError, OSError) as exc:
        raise ForecastError('Não foi possível consultar o servidor NOAA', 503) from exc
    if len(data)>15_000_000 or not data.startswith(b'GRIB') or data[7] != 2:
        raise ForecastError('NOAA não retornou GRIB2 válido para o campo solicitado', 502)
    return data


def native_grid(gid):
    """Reorder values using ecCodes point coordinates, without interpolation."""
    ni = ec.codes_get(gid, 'Ni')
    nj = ec.codes_get(gid, 'Nj')
    if ni < 2 or nj < 2 or ni*nj > 25000:
        raise ForecastError('Grade NOAA pequena ou grande demais para GRIB1', 422)
    lats = ec.codes_get_array(gid, 'latitudes')
    lons = ec.codes_get_array(gid, 'longitudes')
    vals = ec.codes_get_values(gid)
    if len(lats) != ni*nj or len(lons) != len(lats):
        raise ForecastError('Coordenadas incompatíveis com a grade NOAA')
    # Normalize longitude to -180..180 for sorting, keeping western Atlantic.
    triples = [(round(float(a), 6), round((float(o)+180)%360-180, 6), float(v)) for a,o,v in zip(lats,lons,vals)]
    ys = sorted(set(t[0] for t in triples), reverse=True)
    xs = sorted(set(t[1] for t in triples))
    if len(ys) != nj or len(xs) != ni:
        raise ForecastError('Grade NOAA não é regular lat/lon')
    lookup = {(a,o):v for a,o,v in triples}
    if len(lookup) != ni*nj:
        raise ForecastError('Grade NOAA contém coordenadas duplicadas')
    grid = [[(y,x) for x in xs] for y in ys]
    missing = ec.codes_get(gid, 'missingValue')
    values = [lookup[(y,x)] for y in ys for x in xs]
    values = [None if not math.isfinite(v) or abs(v-missing)<1e-8 else v for v in values]
    return grid, values


def decode_fields(raw, expected, level=None):
    if ec is None:
        raise ForecastError('ecCodes não instalado', 503)
    result = {}
    stream = io.BytesIO(raw)
    while True:
        gid = ec.codes_grib_new_from_file(stream)
        if gid is None: break
        try:
            short = str(ec.codes_get(gid, 'shortName'))
            if short not in expected: continue
            if level is not None and (ec.codes_get(gid, 'typeOfLevel') != level[0] or int(ec.codes_get(gid, 'level')) != level[1]): continue
            result[short] = native_grid(gid)
        finally:
            ec.codes_release(gid)
    return result


def verify_grib1(data):
    if not data.startswith(b'GRIB'): raise ForecastError('GRIB1 vazio ou inválido')
    stream = io.BytesIO(data)
    count = 0
    while True:
        gid = ec.codes_grib_new_from_file(stream)
        if gid is None: break
        try:
            if ec.codes_get(gid,'edition') != 1: raise ForecastError('Mensagem diferente de GRIB1')
            if ec.codes_get(gid,'numberOfPoints') < 4: raise ForecastError('Grade GRIB1 inválida')
            count += 1
        finally: ec.codes_release(gid)
    if count < 2: raise ForecastError('Componentes de vento GRIB1 ausentes')
    return count


def generate(lat, lon, radius, hours, on_progress=None, now=None):
    if ec is None: raise ForecastError('ecCodes indisponível', 503)
    if radius not in ALLOWED_RADIUS or hours not in ALLOWED_HOURS: raise ForecastError('Área ou período inválido',422)
    bbox = bounds(lat,lon,radius)
    emit = on_progress or (lambda **kw: None)
    # Probe until the first existing and complete atmospheric cycle.
    ref = None
    first = None
    for cycle in cycle_candidates(now):
        try:
            candidate = get_grib2(make_url(cycle, 0, bbox))
            parsed = decode_fields(candidate, {'10u','10v','u','v'}, ('heightAboveGround', 10))
            if ('10u' in parsed or 'u' in parsed) and ('10v' in parsed or 'v' in parsed):
                ref, first = cycle, candidate
                break
        except ForecastError as exc:
            if exc.status == 429: raise
            continue
    if ref is None: raise ForecastError('Nenhum ciclo GFS válido disponível no NOMADS', 503)
    # Export only future valid times, not past timestamps from a previous cycle.
    current = now or datetime.now(timezone.utc)
    elapsed = max(0, (current-ref).total_seconds()/3600)
    first_lead = int(math.ceil(elapsed/3))*3
    last_lead = int(math.floor((elapsed+hours)/3))*3
    steps = list(range(first_lead, last_lead+1, 3))
    if not steps: raise ForecastError('Não há previsão futura no intervalo solicitado', 503)
    emit(stage='Baixando NOAA GFS', cycle=ref.isoformat(), progress=0, total=len(steps))
    messages = []
    warnings = []
    for index, lead in enumerate(steps):
        wind = decode_fields(first if lead == 0 else get_grib2(make_url(ref,lead,bbox)),
                             {'10u','10v','u','v'}, ('heightAboveGround',10))
        u = wind.get('10u') or wind.get('u')
        v = wind.get('10v') or wind.get('v')
        if not u or not v or u[0] != v[0]:
            raise ForecastError(f'Vento U/V NOAA ausente ou em grades diferentes para +{lead}h', 502)
        valid = ref+timedelta(hours=lead)
        for code, values in ((33,u[1]),(34,v[1])):
            msg = _message(code,105,10,valid,ref,u[0],values)
            if msg is None: raise ForecastError('Campo de vento sem valores reais',502)
            messages.append(msg)
        # WW3 optional. Missing wave source does NOT alter the wind export.
        # To avoid 57+ additional NOMADS requests during rate limiting, the
        # operator can enable explicit wave collection after validating coverage.
        if os.getenv('NOAA_ENABLE_WAVES','false').lower() == 'true':
            try:
                wave = decode_fields(get_grib2(make_url(ref,lead,bbox,True)), {'swh','htsgw'})
                values = wave.get('swh') or wave.get('htsgw')
                if values:
                    msg = _message(100,1,0,valid,ref,values[0],values[1])
                    if msg: messages.append(msg)
                else:
                    warnings.append(f'Onda ausente +{lead}h')
            except ForecastError as exc:
                if exc.status == 429: raise
                warnings.append(f'Onda indisponível +{lead}h')
        emit(stage='Baixando e convertendo NOAA', cycle=ref.isoformat(), progress=index+1, total=len(steps))
    grib = b''.join(messages)
    count = verify_grib1(grib)
    emit(stage='Validado GRIB1', cycle=ref.isoformat(), progress=len(steps), total=len(steps))
    return grib, count, warnings, ref


def pack(grib, fmt):
    if fmt == 'grb': return grib
    if fmt != 'grbzip': raise ForecastError('Formato inválido',422)
    buff = io.BytesIO()
    with zipfile.ZipFile(buff,'w',zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('grib.grb',grib)
    return buff.getvalue()
