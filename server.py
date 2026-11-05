#!/usr/bin/env python3
"""AFM Gestion Victoire — HTTPS reverse-proxy hosted, single-instance SQLite app."""
import base64, datetime as dt, hashlib, hmac, http.cookies, http.server, json, os, secrets, sqlite3, threading, time, urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('AFM_DATA_DIR',str(ROOT/'data'))).expanduser(); DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'afm.sqlite3'
HOST=os.environ.get('AFM_HOST','0.0.0.0' if os.environ.get('RENDER') else '127.0.0.1'); PORT=int(os.environ.get('PORT',os.environ.get('AFM_PORT','8765'))); PRODUCTION=bool(os.environ.get('RENDER') or os.environ.get('AFM_PRODUCTION')=='1')
KINDS=['joueurs','coachs','dirigeants','cotisations','apports','depenses','salaires','primes']
FINANCE={'cotisations','apports','depenses','salaires','primes'}
ALLOWED={'joueurs':{'nom','categorie','naissance','poste','telephone','photo','note'}, 'coachs':{'nom','fonction','telephone','photo','note'},'dirigeants':{'nom','fonction','telephone','photo','note'}}
for kind in FINANCE: ALLOWED[kind]={'nom','categorie','montant','date','note'}
DB_LOCK=threading.RLock(); sessions={}; failures={}; BOOT_TOKEN=None

def connect():
 c=sqlite3.connect(DB, timeout=20); c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON');return c

def init():
 with connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, role TEXT NOT NULL, salt TEXT NOT NULL, pw TEXT NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, actor TEXT NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, moment TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, kind TEXT, record_id TEXT)')
  c.execute('CREATE INDEX IF NOT EXISTS idx_kind ON records(kind)')
  return c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0

def hash_pw(password,salt):return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),260000).hex()
def new_user(c,name,role,pw):
 salt=secrets.token_hex(16);c.execute('INSERT INTO users VALUES(?,?,?,?)',(name,role,salt,hash_pw(pw,salt)))
def stamp():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def log(c,actor,action,kind=None,rid=None):c.execute('INSERT INTO audit(moment,actor,action,kind,record_id) VALUES(?,?,?,?,?)',(stamp(),actor,action,kind,rid))
def clean(kind,vals):
 if not isinstance(vals,dict):raise ValueError('Données invalides')
 v={k:vals[k] for k in ALLOWED[kind] if k in vals}
 for k,val in v.items():
  if k=='montant':
   if isinstance(val,bool):raise ValueError('Montant invalide')
   try:n=float(val)
   except:raise ValueError('Montant invalide')
   if not n.is_integer() or n<=0 or n>10**12:raise ValueError('Montant doit être un entier positif en FCFA')
   v[k]=int(n)
  elif not isinstance(val,str):raise ValueError('Champ invalide : '+k)
  elif k=='photo':
   if val and (not val.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,')) or len(val)>480000):raise ValueError('Photo JPG/PNG/WEBP de moins de 350 Ko requise')
  else:
   v[k]=val.strip()[:500]
 if not v.get('nom'):raise ValueError('Le nom ou libellé est obligatoire')
 if kind in FINANCE:
  if not v.get('date'):raise ValueError('Date obligatoire')
  try:dt.date.fromisoformat(v['date'])
  except:raise ValueError('Date invalide')
  if not v.get('montant'):raise ValueError('Montant obligatoire')
 if kind=='joueurs' and v.get('categorie') not in ('U13','U15','U17','U20'):raise ValueError('Catégorie obligatoire')
 return v

class Handler(http.server.BaseHTTPRequestHandler):
 server_version='AFM/1.0'
 def log_message(self,fmt,*args):print('%s %s'%(self.address_string(),fmt%args))
 def send(self,code,obj,extra=None):
  raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Length',str(len(raw)))
  for k,v in (extra or {}).items():self.send_header(k,v)
  if PRODUCTION:self.send_header('Strict-Transport-Security','max-age=31536000; includeSubDomains')
  self.end_headers();self.wfile.write(raw)
 def asset(self,path):
  name='index.html' if path=='/' else path.lstrip('/')
  if name not in ('index.html','logo-afm.jpg'):self.send(404,{'error':'Introuvable'});return
  p=ROOT/'static'/name
  if not p.exists():self.send(404,{'error':'Fichier manquant'});return
  content=p.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if name.endswith('.html') else 'image/jpeg');self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store' if name.endswith('.html') else 'public, max-age=86400');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'");self.end_headers();self.wfile.write(content)
 def body(self):
  length=int(self.headers.get('Content-Length','0'))
  if length>2500000:raise ValueError('Fichier trop volumineux')
  return json.loads(self.rfile.read(length) or b'{}')
 def user(self):
  cookie=http.cookies.SimpleCookie()
  try:cookie.load(self.headers.get('Cookie',''))
  except:return None
  s=cookie.get('afm_session'); token=s.value if s else None
  o=sessions.get(token)
  if not o:return None
  if o['expires']<time.time():sessions.pop(token,None);return None
  return o
 def auth(self,write=False,president=False):
  u=self.user()
  if not u:self.send(401,{'error':'Veuillez vous connecter'});return None
  if president and u['role']!='president':self.send(403,{'error':'Réservé au président'});return None
  if write and self.headers.get('X-AFM-CSRF')!=u['csrf']:self.send(403,{'error':'Jeton de sécurité manquant'});return None
  return u
 def do_GET(self):
  path=urllib.parse.urlparse(self.path).path
  if not path.startswith('/api/'):
   self.asset(path);return
  if path=='/api/status':
   with connect() as c:ready=c.execute('SELECT COUNT(*) FROM users').fetchone()[0]>0
   self.send(200,{'ready':ready});return
  u=self.auth()
  if not u:return
  if path=='/api/me':self.send(200,{'username':u['username'],'role':u['role'],'csrf':u['csrf']});return
  with connect() as c:
   if path=='/api/data':
    rows=c.execute('SELECT * FROM records ORDER BY created DESC').fetchall();out={k:[] for k in KINDS}
    for row in rows:
     rec=json.loads(row['data']);rec['id']=row['id'];rec['_actor']=row['actor'];rec['_created']=row['created'];out[row['kind']].append(rec)
    self.send(200,{'records':out});return
   if path=='/api/audit':
    if u['role']!='president':self.send(403,{'error':'Réservé au président'});return
    self.send(200,{'events':[dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 150')]});return
   if path=='/api/backup':
    if u['role']!='president':self.send(403,{'error':'Réservé au président'});return
    rows=c.execute('SELECT * FROM records').fetchall();out={k:[] for k in KINDS}
    for row in rows:
     rec=json.loads(row['data']);rec['id']=row['id'];out[row['kind']].append(rec)
    self.send(200,{'app':'AFM Gestion Victoire','version':2,'exportedAt':stamp(),'records':out});return
  self.send(404,{'error':'Route introuvable'})
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path
  try:payload=self.body()
  except Exception as e:self.send(400,{'error':str(e)});return
  if path=='/api/setup':
   global BOOT_TOKEN
   if not PRODUCTION and self.client_address[0] not in ('127.0.0.1','::1'):self.send(403,{'error':'Configuration locale uniquement'});return
   if not BOOT_TOKEN or not hmac.compare_digest(str(payload.get('token','')).encode(),BOOT_TOKEN.encode()):self.send(403,{'error':'Code installation invalide'});return
   p1=payload.get('president','');p2=payload.get('gestionnaire','')
   if not isinstance(p1,str) or not isinstance(p2,str) or min(len(p1),len(p2))<12 or p1==p2:self.send(400,{'error':'Deux mots de passe distincts de 12 caractères minimum sont nécessaires'});return
   with DB_LOCK,connect() as c:
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:self.send(409,{'error':'Déjà configurée'});return
    new_user(c,'president','president',p1);new_user(c,'gestionnaire','gestionnaire',p2);log(c,'system','installation')
   BOOT_TOKEN=None;self.send(200,{'ok':True});return
  if path=='/api/login':
   ip=self.client_address[0]; count,until=failures.get(ip,(0,0))
   if until>time.time():self.send(429,{'error':'Trop de tentatives : réessayez plus tard'});return
   username=payload.get('username','');pw=payload.get('password','')
   with connect() as c:r=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
   valid=bool(r and isinstance(pw,str) and hmac.compare_digest(hash_pw(pw,r['salt']),r['pw']))
   if not valid:
    count+=1; failures[ip]=(count,time.time()+120 if count>=8 else 0);self.send(401,{'error':'Identifiant ou mot de passe incorrect'});return
   failures.pop(ip,None);token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24);sessions[token]={'username':username,'role':r['role'],'csrf':csrf,'expires':time.time()+8*3600}
   self.send(200,{'username':username,'role':r['role'],'csrf':csrf},{'Set-Cookie':f'afm_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'+('; Secure' if PRODUCTION else '')});return
  u=self.auth(write=True,president=path in ('/api/import','/api/password'))
  if not u:return
  if path=='/api/logout':
   cookie=http.cookies.SimpleCookie();cookie.load(self.headers.get('Cookie',''));token=cookie.get('afm_session');sessions.pop(token.value if token else '',None)
   self.send(200,{'ok':True},{'Set-Cookie':'afm_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'+('; Secure' if PRODUCTION else '')});return
  if path=='/api/password':
   target=payload.get('username');new=payload.get('password')
   if target not in ('president','gestionnaire') or not isinstance(new,str) or len(new)<12:self.send(400,{'error':'Mot de passe de 12 caractères minimum obligatoire'});return
   with DB_LOCK,connect() as c:
    salt=secrets.token_hex(16);c.execute('UPDATE users SET salt=?, pw=? WHERE username=?',(salt,hash_pw(new,salt),target));log(c,u['username'],'changement mot de passe',target)
   sessions.clear();self.send(200,{'ok':True});return
  if path=='/api/import':
   records=payload.get('records')
   if not isinstance(records,dict) or any(not isinstance(records.get(k),list) for k in KINDS):self.send(400,{'error':'Sauvegarde incompatible'});return
   if sum(len(records[k]) for k in KINDS)>10000:self.send(400,{'error':'Sauvegarde trop volumineuse'});return
   try:
    new=[]
    for k in KINDS:
     for r in records[k]:new.append((secrets.token_hex(16),k,clean(k,r)))
   except ValueError as e:self.send(400,{'error':str(e)});return
   with DB_LOCK,connect() as c:
    c.execute('DELETE FROM records')
    for rid,k,r in new:c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(rid,k,json.dumps(r,ensure_ascii=False),stamp(),stamp(),u['username']))
    log(c,u['username'],'restauration',None,str(len(new)))
   self.send(200,{'ok':True,'count':len(new)});return
  if path=='/api/records':
   kind=payload.get('kind');val=payload.get('data')
   if kind not in KINDS:self.send(400,{'error':'Rubrique inconnue'});return
   if kind=='apports' and u['role']!='president':self.send(403,{'error':'Seul le président peut approvisionner la caisse'});return
   try:val=clean(kind,val)
   except ValueError as e:self.send(400,{'error':str(e)});return
   rid=payload.get('id');rid=str(rid) if rid else None
   with DB_LOCK,connect() as c:
    if rid:
     existing=c.execute('SELECT kind FROM records WHERE id=?',(rid,)).fetchone()
     if not existing or existing['kind']!=kind:self.send(404,{'error':'Enregistrement introuvable'});return
     c.execute('UPDATE records SET data=?,updated=?,actor=? WHERE id=?',(json.dumps(val,ensure_ascii=False),stamp(),u['username'],rid));action='modification'
    else:
     rid=secrets.token_hex(16);c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(rid,kind,json.dumps(val,ensure_ascii=False),stamp(),stamp(),u['username']));action='création'
    log(c,u['username'],action,kind,rid)
   self.send(200,{'ok':True,'id':rid});return
  if path=='/api/delete':
   rid=str(payload.get('id',''));kind=payload.get('kind')
   if kind not in KINDS:self.send(400,{'error':'Rubrique inconnue'});return
   if kind in FINANCE and u['role']!='president':self.send(403,{'error':'Seul le président peut supprimer une opération financière'});return
   with DB_LOCK,connect() as c:
    r=c.execute('SELECT kind FROM records WHERE id=?',(rid,)).fetchone()
    if not r or r['kind']!=kind:self.send(404,{'error':'Introuvable'});return
    c.execute('DELETE FROM records WHERE id=?',(rid,));log(c,u['username'],'suppression',kind,rid)
   self.send(200,{'ok':True});return
  self.send(404,{'error':'Route introuvable'})

if __name__=='__main__':
 need_setup=init()
 if need_setup:
  BOOT_TOKEN=os.environ.get('AFM_SETUP_TOKEN') if PRODUCTION else secrets.token_urlsafe(12)
  if PRODUCTION and (not BOOT_TOKEN or len(BOOT_TOKEN)<24):
   raise SystemExit('ERREUR : definir AFM_SETUP_TOKEN (au moins 24 caracteres aleatoires) dans les variables de Render.')
 print('\nAFM Gestion Victoire — serveur local\nAdresse : http://localhost:%d\n'%PORT)
 if need_setup:print('PREMIERE INSTALLATION : utiliser la valeur de AFM_SETUP_TOKEN dans Render.' if PRODUCTION else 'PREMIERE INSTALLATION : code à saisir sur la page : '+BOOT_TOKEN)
 if HOST!='127.0.0.1':print('Serveur accessible via %s:%s : la terminaison HTTPS doit etre assuree par Render ou un reverse proxy.'%(HOST,PORT))
 server=http.server.ThreadingHTTPServer((HOST,PORT),Handler);server.daemon_threads=True
 try:server.serve_forever()
 except KeyboardInterrupt:print('\nArrêt du serveur.')
