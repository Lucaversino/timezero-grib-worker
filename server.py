"""Authenticated asynchronous NOAA job server for Render.

Jobs are volatile while the free Render container runs. The Vercel API stores
completed output in private Supabase Storage; never advertise volatile jobs as
persistent files.
"""
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from hmac import compare_digest
import noaa_generator as noaa

JOBS = {}
LOCK = threading.RLock()
EXEC = ThreadPoolExecutor(max_workers=1)
MAX_JOBS = 24
TTL = 3600


def safe_job_id(owner, lat, lon, radius, hours):
    stamp = time.strftime('%Y-%m-%d', time.gmtime())
    return hashlib.sha256(f'{owner}:{lat:.4f}:{lon:.4f}:{radius}:{hours}:{stamp}'.encode()).hexdigest()[:36]


def run_job(jobid, lat, lon, radius, hours):
    def progress(**kwargs):
        with LOCK:
            if jobid in JOBS: JOBS[jobid].update(kwargs)
    with LOCK:
        JOBS[jobid]['status'] = 'running'
    try:
        data, count, warnings, cycle = noaa.generate(lat,lon,radius,hours,on_progress=progress)
        with LOCK:
            JOBS[jobid].update(status='complete', data=data, messages=count, warnings=warnings,
                               cycle=cycle.isoformat(), stage='GRIB1 pronto e validado')
        print(f'NOAA job complete: messages={count}, bytes={len(data)}',flush=True)
    except Exception as exc:
        message = str(exc) if isinstance(exc,noaa.ForecastError) else 'Falha interna no processamento GRIB1'
        with LOCK:
            JOBS[jobid].update(status='failed',error=message,stage='Falha na geração')
        print(f'NOAA job failed: {type(exc).__name__}: {message}',flush=True)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, obj):
        payload=json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        req=urlsplit(self.path)
        if req.path in ('/health','/healthz'):
            return self.send_json(200 if noaa.ec else 503, {'status':'ok' if noaa.ec else 'unavailable','service':'timezero-grib-worker','eccodes':bool(noaa.ec),'source':'NOAA GFS 0.25','jobs':len(JOBS)})
        if req.path != '/api/timezero-jobs':
            return self.send_json(404,{'error':'Endpoint não encontrado'})
        secret=os.environ.get('TIMEZERO_WORKER_SECRET','')
        if not secret or not compare_digest(self.headers.get('X-Worker-Secret',''),secret):
            return self.send_json(401,{'error':'Não autorizado'})
        q=parse_qs(req.query)
        owner=q.get('owner',[''])[0]
        if len(owner)<5 or len(owner)>160: return self.send_json(422,{'error':'Usuário inválido'})
        if 'id' in q:
            jobid=q['id'][0]
            with LOCK:
                job=JOBS.get(jobid)
                if not job or job['owner'] != owner: return self.send_json(404,{'error':'Trabalho não encontrado ou reiniciado'})
                result={k:v for k,v in job.items() if k not in ('data','owner','created')}
                data=job.get('data')
            if q.get('download',['0'])[0] != '1': return self.send_json(200, result)
            if result['status'] != 'complete' or not data: return self.send_json(409,{'error':'Arquivo ainda não está pronto'})
            fmt=q.get('format',['grb'])[0]
            if fmt not in ('grb','grbzip'):return self.send_json(422,{'error':'Formato inválido'})
            payload=noaa.pack(data,fmt)
            self.send_response(200)
            self.send_header('Content-Type','application/zip' if fmt=='grbzip' else 'application/octet-stream')
            self.send_header('Content-Disposition',f'attachment; filename="noaa-gfs-timezero.{fmt}"')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(payload)
            return
        try:
            lat=float(q['lat'][0]);lon=float(q['lon'][0]);radius=int(q['radius'][0]);hours=int(q['hours'][0])
            if not all(map(__import__('math').isfinite,(lat,lon))) or radius not in noaa.ALLOWED_RADIUS or hours not in noaa.ALLOWED_HOURS:
                raise ValueError('Parâmetros inválidos')
            noaa.bounds(lat,lon,radius)
        except (KeyError,ValueError,IndexError,noaa.ForecastError):
            return self.send_json(422,{'error':'Coordenadas ou área inválidas'})
        jobid=safe_job_id(owner,lat,lon,radius,hours)
        with LOCK:
            if jobid not in JOBS:
                if len(JOBS)>=MAX_JOBS:
                    expired=sorted(JOBS,key=lambda x:JOBS[x]['created'])
                    for key in expired:
                        if JOBS[key]['status'] not in ('queued','running'):
                            del JOBS[key]
                            if len(JOBS)<MAX_JOBS:break
                if len(JOBS)>=MAX_JOBS:return self.send_json(503,{'error':'Fila cheia; tente depois'})
                JOBS[jobid]={'owner':owner,'created':time.time(),'status':'queued','stage':'Na fila NOAA','progress':0,'total':hours//3+1}
                EXEC.submit(run_job,jobid,lat,lon,radius,hours)
            job=JOBS[jobid]
            # Failure remains visible to the user; a new request is permitted
            # only after a cooldown, preventing retry storms.
            if job['status']=='failed' and time.time()-job['created']>900:
                JOBS[jobid]={'owner':owner,'created':time.time(),'status':'queued','stage':'Nova tentativa NOAA','progress':0,'total':hours//3+1}
                EXEC.submit(run_job,jobid,lat,lon,radius,hours)
                job=JOBS[jobid]
            result={k:v for k,v in job.items() if k not in ('data','owner','created')}
        self.send_json(202 if result['status'] in ('queued','running') else 200,{'id':jobid,**result})

    def log_message(self, fmt, *args):
        # Don't log coordinates, authorization, or user identifiers.
        print('HTTP request processed',flush=True)


def main():
    if len(os.getenv('TIMEZERO_WORKER_SECRET','')) <32:raise RuntimeError('TIMEZERO_WORKER_SECRET precisa de pelo menos 32 caracteres')
    if noaa.ec is None:raise RuntimeError('ecCodes indisponível')
    port=int(os.getenv('PORT','10000'))
    print(f'NOAA TimeZero worker listening on :{port}',flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()

if __name__=='__main__':main()
