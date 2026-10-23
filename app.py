#!/usr/bin/env python3
"""Domowe Menu: small, local-first family food planner."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import os, json, sqlite3, secrets, hashlib, hmac, time, datetime, mimetypes, io, zipfile, re, html, socket, ipaddress, base64
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.request import HTTPRedirectHandler, build_opener
from html.parser import HTMLParser

DATA = Path(os.getenv("DATA_DIR", "/data")); DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "domowe-menu.sqlite"
STATIC = Path(__file__).resolve().parent / "static"
PASSWORD = os.getenv("APP_PASSWORD", "")
COOKIE = "domowe_menu_session"
LOGIN_FAILURES = {}

def db():
    # Autocommit writes before an API response is sent. This matters for
    # follow-up requests such as uploading a recipe photo immediately after
    # creating the recipe.
    c=sqlite3.connect(DB,isolation_level=None); c.row_factory=sqlite3.Row; c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON"); return c

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY, name TEXT NOT NULL, sex TEXT, birth_date TEXT, age_override INTEGER, height REAL, weight REAL, target_weight REAL, activity TEXT DEFAULT 'light', goal TEXT DEFAULT 'maintain', rate REAL DEFAULT 0.25, mode TEXT DEFAULT 'manual', calories INTEGER, protein INTEGER, carbs INTEGER, fat INTEGER, tolerance_type TEXT DEFAULT 'percent', tolerance_value REAL DEFAULT 5, start_date TEXT, deadline TEXT, pregnant INTEGER DEFAULT 0, breastfeeding INTEGER DEFAULT 0, individual_guidance INTEGER DEFAULT 0, restrictions TEXT DEFAULT '', updated_at TEXT);
        CREATE TABLE IF NOT EXISTS weigh_ins(id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, date TEXT NOT NULL, weight REAL NOT NULL, note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS diary(id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, date TEXT NOT NULL, meal TEXT DEFAULT 'Posiłek', name TEXT NOT NULL, calories REAL DEFAULT 0, protein REAL DEFAULT 0, carbs REAL DEFAULT 0, fat REAL DEFAULT 0, status TEXT DEFAULT 'eaten', servings REAL DEFAULT 1, food_id INTEGER, grams REAL);
        CREATE TABLE IF NOT EXISTS foods(id INTEGER PRIMARY KEY, name TEXT NOT NULL, calories REAL DEFAULT 0, protein REAL DEFAULT 0, carbs REAL DEFAULT 0, fat REAL DEFAULT 0, unit TEXT DEFAULT '100 g', updated_at TEXT);
        CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, servings REAL DEFAULT 1, ingredients TEXT DEFAULT '[]', calories REAL DEFAULT 0, protein REAL DEFAULT 0, carbs REAL DEFAULT 0, fat REAL DEFAULT 0, notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, date TEXT NOT NULL, meal TEXT DEFAULT 'Obiad', recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL, name TEXT NOT NULL, servings REAL DEFAULT 1, calories REAL DEFAULT 0, protein REAL DEFAULT 0, carbs REAL DEFAULT 0, fat REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS shopping_checks(date TEXT NOT NULL, item_key TEXT NOT NULL, checked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(date,item_key));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS shopping_items(id INTEGER PRIMARY KEY, date TEXT NOT NULL, name TEXT NOT NULL, quantity REAL DEFAULT 1, unit TEXT DEFAULT '', category TEXT DEFAULT 'Inne', checked INTEGER DEFAULT 0);
        ''')
        for col,defn in [('category',"TEXT DEFAULT 'Inne'"),('tags',"TEXT DEFAULT ''"),('prep_time',"TEXT DEFAULT ''"),('cook_time',"TEXT DEFAULT ''"),('instructions',"TEXT DEFAULT ''"),('image_path',"TEXT DEFAULT ''")]:
            if col not in [r['name'] for r in c.execute('PRAGMA table_info(recipes)')]: c.execute(f'ALTER TABLE recipes ADD COLUMN {col} {defn}')
        # Small in-place migrations keep earlier local builds usable.
        for col,defn in [('age_override','INTEGER'),('pregnant','INTEGER DEFAULT 0'),('breastfeeding','INTEGER DEFAULT 0'),('individual_guidance','INTEGER DEFAULT 0'),('restrictions',"TEXT DEFAULT ''")]:
            if col not in [r['name'] for r in c.execute('PRAGMA table_info(profiles)')]: c.execute(f'ALTER TABLE profiles ADD COLUMN {col} {defn}')
        for col,defn in [('food_id','INTEGER'),('grams','REAL')]:
            if col not in [r['name'] for r in c.execute('PRAGMA table_info(diary)')]: c.execute(f'ALTER TABLE diary ADD COLUMN {col} {defn}')
        for col in ('calories','protein','carbs','fat'):
            if col not in [r['name'] for r in c.execute('PRAGMA table_info(plans)')]: c.execute(f'ALTER TABLE plans ADD COLUMN {col} REAL DEFAULT 0')
        if c.execute('SELECT COUNT(*) FROM profiles').fetchone()[0] == 0:
            c.execute("INSERT INTO profiles(name,mode,goal,tolerance_type,tolerance_value) VALUES('Ja','manual','maintain','percent',5)")
        if not c.execute("SELECT 1 FROM settings WHERE key='secret'").fetchone():
            c.execute("INSERT INTO settings VALUES('secret',?)", (secrets.token_hex(32),))

def secret():
    with db() as c: return c.execute("SELECT value FROM settings WHERE key='secret'").fetchone()[0]

def sign(payload): return hmac.new(secret().encode(),payload.encode(),hashlib.sha256).hexdigest()
def token():
    payload=str(int(time.time()+60*60*24*14)); return payload+'.'+sign(payload)
def valid_cookie(value):
    try:
        payload,sig=value.split('.',1)
        return int(payload)>time.time() and hmac.compare_digest(sig,sign(payload))
    except Exception: return False
def row(r): return dict(r) if r else None
def rows(rs): return [dict(r) for r in rs]
def as_int(v):
    try: return int(v) if v not in (None,'') else None
    except: return None
def as_float(v):
    try: return float(v) if v not in (None,'') else None
    except: return None
def calculate(p):
    out={'bmi':None,'bmr':None,'tdee':None,'suggested':None,'warning':None}
    h=as_float(p.get('height')); w=as_float(p.get('weight'))
    if h and w: out['bmi']=round(w/((h/100)**2),1)
    if p.get('birth_date'):
        try:
            b=datetime.date.fromisoformat(p['birth_date']); today=datetime.date.today()
            age=today.year-b.year-((today.month,today.day)<(b.month,b.day))
        except: age=None
    else: age=as_int(p.get('age_override'))
    if p.get('pregnant') or p.get('breastfeeding') or p.get('individual_guidance'):
        out['warning']='Automatyczne wyliczenie wyłączone dla tego profilu. Skorzystaj z indywidualnych zaleceń specjalisty.'
        return out
    if age is None or age<18:
        out['warning']='Automatyczne wyliczenie jest dostępne wyłącznie dla dorosłych. Wiek poniżej 18 lat wymaga indywidualnej oceny.'
        return out
    if age>80:
        out['warning']='Kalkulator nie podaje limitu dla osób powyżej 80 lat. Skorzystaj z indywidualnych zaleceń specjalisty.'
        return out
    if not (h and w and p.get('sex') in ('female','male')): return out
    # Mifflin–St Jeor. Adult screening estimate only.
    out['bmr']=round(10*w+6.25*h-5*age+(-161 if p['sex']=='female' else 5))
    factors={'sedentary':1.2,'light':1.375,'moderate':1.55,'high':1.725,'very_high':1.9}
    out['tdee']=round(out['bmr']*factors.get(p.get('activity'),1.375))
    goal=p.get('goal','maintain'); pace=as_float(p.get('rate')) or .25
    adjustment=min(750,max(0,pace*7700/7))
    target=out['tdee']-adjustment if goal=='lose' else out['tdee']+adjustment if goal=='gain' else out['tdee']
    out['suggested']=max(1200,round(target/10)*10)
    return out

def calorie_balance(profile, entries):
    target=float(profile.get('calories') or 0)
    amount=sum(float(x.get('calories') or 0) for x in entries if x.get('profile_id')==profile.get('id') and x.get('status')!='skipped')
    value=float(profile.get('tolerance_value') or 0)
    tolerance=target*value/100 if profile.get('tolerance_type')=='percent' else value
    low=max(0,round(target-tolerance)); high=round(target+tolerance)
    accepted=target>0 and low<=amount<=high
    return {'target':round(target),'planned':round(amount),'deviation':round(amount-target),'tolerance':round(tolerance),'range_low':low,'range_high':high,'accepted':accepted}

class JsonLdParser(HTMLParser):
    def __init__(self): super().__init__(); self.in_script=False; self.parts=[]; self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='script' and dict(attrs).get('type','').lower()=='application/ld+json': self.in_script=True; self.parts=[]
    def handle_data(self,data):
        if self.in_script: self.parts.append(data)
    def handle_endtag(self,tag):
        if tag.lower()=='script' and self.in_script: self.scripts.append(''.join(self.parts)); self.in_script=False

def validate_public_url(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password: raise ValueError('Podaj publiczny adres strony zaczynający się od http:// lub https://')
    if p.port not in (None,80,443): raise ValueError('Import obsługuje standardowe porty 80 i 443')
    try: addresses={x[4][0] for x in socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)}
    except OSError: raise ValueError('Nie można odnaleźć publicznego serwera pod tym adresem')
    if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses): raise ValueError('Import z adresów lokalnych lub prywatnych jest zablokowany')
    return url

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def recipe_jsonld(document):
    parser=JsonLdParser(); parser.feed(document)
    def walk(value):
        if isinstance(value,dict):
            kind=value.get('@type',[]); kind=[kind] if isinstance(kind,str) else kind
            if any(str(x).rsplit('/',1)[-1].lower()=='recipe' for x in kind): return value
            for v in value.values():
                found=walk(v)
                if found: return found
        elif isinstance(value,list):
            for v in value:
                found=walk(v)
                if found: return found
        return None
    for script in parser.scripts:
        try:
            found=walk(json.loads(script))
            if found: return found
        except (ValueError,TypeError): continue
    return None

def duration_label(value):
    m=re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?',str(value or '').strip(),re.I)
    if not m:return ''
    h,mins=(int(x or 0) for x in m.groups()); return (f'{h} godz. ' if h else '')+(f'{mins} min' if mins else '') or ''

class Handler(BaseHTTPRequestHandler):
    server_version='DomoweMenu/0.3'
    def log_message(self,fmt,*args): print('%s - %s'%(self.address_string(),fmt%args))
    def send(self,status,body,ctype='application/json; charset=utf-8',headers=None):
        b=body if isinstance(body,bytes) else body.encode(); self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','same-origin'); self.send_header('X-Frame-Options','DENY'); self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"); self.send_header('Cache-Control','no-store')
        if headers:
            for k,v in headers.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def json(self,status,obj,headers=None): self.send(status,json.dumps(obj,ensure_ascii=False).encode(),headers=headers)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>100_000_000: raise ValueError('request too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def authenticated(self):
        cookies={x.split('=',1)[0].strip():x.split('=',1)[1].strip() for x in self.headers.get('Cookie','').split(';') if '=' in x}
        return valid_cookie(cookies.get(COOKIE,''))
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/health': return self.send(200,'ok','text/plain')
        if path=='/api/session': return self.json(200,{'authenticated':self.authenticated()})
        if path=='/api/login' and self.command=='GET': return self.json(405,{'error':'method'})
        if path.startswith('/api/') and not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
        if path=='/api/state':
            d=urlparse(self.path); day=parse_qs(d.query).get('date',[datetime.date.today().isoformat()])[0]
            selected=datetime.date.fromisoformat(day); monday=selected-datetime.timedelta(days=selected.weekday()); sunday=monday+datetime.timedelta(days=7)
            with db() as c:
                profiles=rows(c.execute('SELECT * FROM profiles ORDER BY id').fetchall())
                for p in profiles: p['calculation']=calculate(p)
                diary=rows(c.execute('SELECT * FROM diary WHERE date=? ORDER BY id',(day,)).fetchall())
                for p in profiles: p['daily_balance']=calorie_balance(p,diary)
                data={'profiles':profiles,'weights':rows(c.execute('SELECT * FROM weigh_ins ORDER BY date').fetchall()),'diary':diary,'recipes':rows(c.execute('SELECT * FROM recipes ORDER BY name').fetchall()),'foods':rows(c.execute('SELECT * FROM foods ORDER BY name').fetchall()),'plans':rows(c.execute('SELECT * FROM plans WHERE date=? ORDER BY id',(day,)).fetchall()),'week_plans':rows(c.execute('SELECT * FROM plans WHERE date>=? AND date<? ORDER BY date,id',(monday.isoformat(),sunday.isoformat())).fetchall()),'shopping_checks':rows(c.execute('SELECT * FROM shopping_checks WHERE date=?',(day,)).fetchall()),'shopping_items':rows(c.execute('SELECT * FROM shopping_items WHERE date=? ORDER BY category,name',(day,)).fetchall()),'date':day}
            return self.json(200,data)
        if path=='/api/export':
            with db() as c:
                data={t:rows(c.execute('SELECT * FROM '+t).fetchall()) for t in ('profiles','weigh_ins','diary','recipes','foods','plans','shopping_checks','shopping_items')}
            data['recipe_images']=[]
            for recipe in data['recipes']:
                image_path=recipe.get('image_path') or ''
                if image_path.startswith('/media/'):
                    image=DATA/'images'/Path(image_path).name
                    if image.is_file(): data['recipe_images'].append({'recipe_id':recipe['id'],'content_type':mimetypes.guess_type(image.name)[0],'data':base64.b64encode(image.read_bytes()).decode('ascii')})
            return self.json(200,data,{'Content-Disposition':'attachment; filename="domowe-menu-backup.json"'})
        if path=='/': return self.send(200,((STATIC/'index.html').read_bytes()),'text/html; charset=utf-8')
        if path.startswith('/static/'):
            f=STATIC/path.removeprefix('/static/')
            if not f.is_file() or '..' in path: return self.send(404,'not found','text/plain')
            return self.send(200,f.read_bytes(),mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        if path.startswith('/media/'):
            if not self.authenticated(): return self.send(401,'unauthorized','text/plain')
            name=Path(path).name
            if name!=path.removeprefix('/media/') or not re.fullmatch(r'[a-f0-9]{32}\.(?:jpg|png|webp)',name): return self.send(404,'not found','text/plain')
            f=DATA/'images'/name
            if not f.is_file(): return self.send(404,'not found','text/plain')
            return self.send(200,f.read_bytes(),mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        return self.send(404,'not found','text/plain')
    def do_POST(self):
        path=urlparse(self.path).path
        if path=='/api/import-mealie':
            if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>200_000_000: return self.json(413,{'error':'Kopia jest pusta albo przekracza limit 200 MB'})
            raw=self.rfile.read(size)
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    if sum(x.file_size for x in z.infolist())>500_000_000: return self.json(413,{'error':'Rozpakowana kopia jest zbyt duża'})
                    entry=next((x for x in z.infolist() if x.filename.endswith('/database.json') or x.filename=='database.json'),None)
                    if not entry: return self.json(400,{'error':'W ZIP nie znaleziono database.json z kopii Mealie'})
                    tables=json.loads(z.read(entry))
                recipe_rows=tables.get('recipes',[]); nutrition=tables.get('recipe_nutrition',[]); ingredients=tables.get('recipes_ingredients',[]); instructions=tables.get('recipe_instructions',[])
                foods={str(x.get('id')):x.get('name') for x in tables.get('ingredient_foods',[])}
                units={str(x.get('id')):x.get('name') for x in tables.get('ingredient_units',[])}
                nutrition_by={str(x.get('recipe_id')):x for x in nutrition}; steps_by={};
                for step in instructions: steps_by.setdefault(str(step.get('recipe_id')),[]).append(str(step.get('text') or step.get('instruction') or '').strip());
                by_recipe={}
                for item in ingredients: by_recipe.setdefault(str(item.get('recipe_id')),[]).append(item)
                imported=[]
                for r in recipe_rows:
                    name=str(r.get('name') or '').strip()
                    if not name: continue
                    ing=[]
                    for it in by_recipe.get(str(r.get('id')),[]):
                        text=str(it.get('original_text') or '').strip()
                        if not text:
                            parts=[str(it.get('quantity') or '').strip(),units.get(str(it.get('unit_id')),''),foods.get(str(it.get('food_id')),'')]
                            text=' '.join(x for x in parts if x).strip()
                        if text: ing.append({'name':text,'qty':1,'unit':'porcja','grams_each':None})
                    n=nutrition_by.get(str(r.get('id')),{})
                    def num(v):
                        m=re.search(r'-?\d+(?:[.,]\d+)?',str(v or '')); return float(m.group(0).replace(',','.')) if m else 0
                    steps=[x for x in steps_by.get(str(r.get('id')),[]) if x]
                    imported.append((name,max(1,float(r.get('recipe_servings') or 1)),json.dumps(ing,ensure_ascii=False),num(n.get('calories')),num(n.get('protein_content')),num(n.get('carbohydrate_content')),num(n.get('fat_content')),str(r.get('description') or '').strip(),'Inne','', '', '', '\n'.join(steps)))
                with db() as c:
                    for values in imported: c.execute('INSERT INTO recipes(name,servings,ingredients,calories,protein,carbs,fat,notes,category,tags,prep_time,cook_time,instructions) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
                return self.json(200,{'imported':len(imported),'message':'Przepisy zaimportowano. Sprawdź wartości odżywcze, bo backup Mealie może nie zawierać danych dla każdego przepisu.'})
            except zipfile.BadZipFile: return self.json(400,{'error':'To nie jest poprawny plik ZIP'})
            except Exception as exc: return self.json(400,{'error':'Nie udało się odczytać kopii Mealie: '+str(exc)[:180]})
        if path=='/api/recipes/image':
            if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
            size=int(self.headers.get('Content-Length','0')); ctype=self.headers.get('Content-Type','').split(';',1)[0].lower()
            ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}.get(ctype)
            if not ext or size<=0 or size>8_000_000: return self.json(400,{'error':'Wybierz obraz JPG, PNG lub WebP do 8 MB'})
            image=self.rfile.read(size)
            signatures={'jpg':image.startswith(b'\xff\xd8\xff'),'png':image.startswith(b'\x89PNG\r\n\x1a\n'),'webp':len(image)>12 and image[:4]==b'RIFF' and image[8:12]==b'WEBP'}
            if not signatures[ext]: return self.json(400,{'error':'Plik nie odpowiada zadeklarowanemu formatowi obrazu'})
            recipe_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
            if not recipe_id.isdigit(): return self.json(400,{'error':'Brak identyfikatora przepisu'})
            name=secrets.token_hex(16)+'.'+ext; folder=DATA/'images'; folder.mkdir(parents=True,exist_ok=True); (folder/name).write_bytes(image)
            with db() as c:
                old=c.execute('SELECT image_path FROM recipes WHERE id=?',(recipe_id,)).fetchone()
                if not old: (folder/name).unlink(missing_ok=True); return self.json(404,{'error':'Nie znaleziono przepisu'})
                c.execute('UPDATE recipes SET image_path=? WHERE id=?',('/media/'+name,recipe_id))
                if old['image_path'] and old['image_path'].startswith('/media/'):
                    old_file=folder/Path(old['image_path']).name
                    if re.fullmatch(r'[a-f0-9]{32}\.(?:jpg|png|webp)',old_file.name): old_file.unlink(missing_ok=True)
            return self.json(200,{'image_path':'/media/'+name})
        if int(self.headers.get('Content-Length','0'))>100_000_000: return self.json(413,{'error':'Żądanie przekracza limit 100 MB'})
        data=self.body()
        if path=='/api/login':
            ip=self.client_address[0]; now=time.time(); recent=[t for t in LOGIN_FAILURES.get(ip,[]) if now-t<900]
            if len(recent)>=5: return self.json(429,{'error':'Za dużo prób logowania. Spróbuj ponownie za 15 minut.'})
            if hmac.compare_digest(str(data.get('password','')),PASSWORD):
                LOGIN_FAILURES.pop(ip,None); return self.json(200,{'ok':True},{'Set-Cookie':f'{COOKIE}={token()}; Path=/; HttpOnly; SameSite=Strict; Secure; Max-Age=1209600'})
            LOGIN_FAILURES[ip]=recent+[now]
            return self.json(401,{'error':'Nieprawidłowe hasło'})
        if path=='/api/logout': return self.json(200,{'ok':True},{'Set-Cookie':f'{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'})
        if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
        if path=='/api/import-url':
            try:
                source=validate_public_url(str(data.get('url','')).strip())
                req=Request(source,headers={'User-Agent':'DomoweMenu/0.3 recipe importer','Accept':'text/html'})
                with build_opener(SafeRedirect()).open(req,timeout=15) as response: raw=response.read(8_000_001)
                if len(raw)>8_000_000: return self.json(413,{'error':'Strona jest zbyt duża do importu'})
                document=raw.decode(response.headers.get_content_charset() or 'utf-8','replace')
                recipe=recipe_jsonld(document)
                if not recipe: return self.json(422,{'error':'Nie znaleziono danych Recipe.org na tej stronie. Dodaj przepis ręcznie albo zaimportuj ZIP Mealie.'})
                nutrition=recipe.get('nutrition') or {}
                def num(v):
                    m=re.search(r'-?\d+(?:[.,]\d+)?',str(v or '')); return float(m.group(0).replace(',','.')) if m else 0
                ingredients=recipe.get('recipeIngredient') or []
                if not isinstance(ingredients,list): ingredients=[ingredients]
                instructions=recipe.get('recipeInstructions') or []
                if isinstance(instructions,str): instructions=[instructions]
                steps=[]
                def read_steps(items):
                    for step in items:
                        if isinstance(step,str):
                            if step.strip(): steps.append(step.strip())
                        elif isinstance(step,dict):
                            if isinstance(step.get('itemListElement'),list): read_steps(step['itemListElement'])
                            else:
                                text=step.get('text') or step.get('name')
                                if text: steps.append(str(text).strip())
                read_steps(instructions)
                yield_value=recipe.get('recipeYield',1)
                if isinstance(yield_value,list): yield_value=yield_value[0] if yield_value else 1
                servings=max(1,num(yield_value) or 1)
                name=html.unescape(re.sub(r'<[^>]+>','',str(recipe.get('name') or '')).strip())
                if not name: return self.json(422,{'error':'Strona nie zawiera nazwy przepisu'})
                ingredients_json=json.dumps([{'name':html.unescape(str(x)).strip(),'qty':1,'unit':'porcja','grams_each':None} for x in ingredients if str(x).strip()],ensure_ascii=False)
                description=html.unescape(str(recipe.get('description') or '')).strip()
                category=recipe.get('recipeCategory') or 'Inne'; category=category[0] if isinstance(category,list) and category else category
                tags=recipe.get('keywords') or ''; tags=', '.join(str(x) for x in tags) if isinstance(tags,list) else str(tags)
                values=(name,servings,ingredients_json,num(nutrition.get('calories')),num(nutrition.get('proteinContent')),num(nutrition.get('carbohydrateContent')),num(nutrition.get('fatContent')),description,str(category),tags,duration_label(recipe.get('prepTime')),duration_label(recipe.get('cookTime')),'\n'.join(steps))
                with db() as c:
                    cur=c.execute('INSERT INTO recipes(name,servings,ingredients,calories,protein,carbs,fat,notes,category,tags,prep_time,cook_time,instructions) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
                return self.json(201,{'id':cur.lastrowid,'name':name,'message':'Zaimportowano dane strukturalne przepisu. Sprawdź porcje i wartości odżywcze.'})
            except (ValueError, OSError, TimeoutError) as exc: return self.json(400,{'error':str(exc)[:180] or 'Nie udało się pobrać przepisu'})
            except Exception as exc: return self.json(400,{'error':'Nie udało się odczytać przepisu: '+str(exc)[:160]})
        with db() as c:
            if path=='/api/profiles':
                fields=['name','sex','birth_date','age_override','height','weight','target_weight','activity','goal','rate','mode','calories','protein','carbs','fat','tolerance_type','tolerance_value','start_date','deadline','pregnant','breastfeeding','individual_guidance','restrictions']
                vals=[data.get(k) for k in fields]; pid=as_int(data.get('id'))
                for i,k in enumerate(fields):
                    if k in ('pregnant','breastfeeding','individual_guidance'): vals[i]=1 if str(vals[i]).lower() in ('1','true','on','yes') else 0
                if pid:
                    old=c.execute('SELECT weight FROM profiles WHERE id=?',(pid,)).fetchone(); old_weight=old['weight'] if old else None
                    c.execute('UPDATE profiles SET '+','.join(k+'=?' for k in fields)+',updated_at=? WHERE id=?',vals+[datetime.datetime.now().isoformat(),pid]);
                    if data.get('weight') is not None and (old_weight is None or float(old_weight)!=float(data.get('weight'))) and not c.execute('SELECT 1 FROM weigh_ins WHERE profile_id=? AND date=? AND weight=?',(pid,datetime.date.today().isoformat(),data.get('weight'))).fetchone(): c.execute('INSERT INTO weigh_ins(profile_id,date,weight,note) VALUES(?,?,?,?)',(pid,datetime.date.today().isoformat(),data.get('weight'),'Pomiar z profilu'))
                    return self.json(200,{'id':pid,'calculation':calculate(data)})
                cur=c.execute('INSERT INTO profiles('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',vals);
                if data.get('weight') is not None: c.execute('INSERT INTO weigh_ins(profile_id,date,weight,note) VALUES(?,?,?,?)',(cur.lastrowid,datetime.date.today().isoformat(),data.get('weight'),'Pomiar początkowy'))
                return self.json(201,{'id':cur.lastrowid})
            if path=='/api/profiles/delete': c.execute('DELETE FROM profiles WHERE id=?',(data.get('id'),)); return self.json(200,{'ok':True})
            if path=='/api/weights':
                cur=c.execute('INSERT INTO weigh_ins(profile_id,date,weight,note) VALUES(?,?,?,?)',(data.get('profile_id'),data.get('date') or datetime.date.today().isoformat(),data.get('weight'),data.get('note','')))
                c.execute('UPDATE profiles SET weight=? WHERE id=?',(data.get('weight'),data.get('profile_id'))); return self.json(201,{'id':cur.lastrowid})
            if path=='/api/diary':
                fields=['profile_id','date','meal','name','calories','protein','carbs','fat','status','servings']; vals=[data.get(k) for k in fields]
                cur=c.execute('INSERT INTO diary('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',vals); return self.json(201,{'id':cur.lastrowid})
            if path=='/api/foods':
                fields=['name','calories','protein','carbs','fat','unit']; vals=[data.get(k) for k in fields]
                food_id=as_int(data.get('id'))
                if food_id:
                    c.execute('UPDATE foods SET '+','.join(k+'=?' for k in fields)+',updated_at=? WHERE id=?',vals+[datetime.datetime.now().isoformat(),food_id]); return self.json(200,{'id':food_id})
                cur=c.execute('INSERT INTO foods('+','.join(fields)+',updated_at) VALUES('+','.join('?' for _ in fields)+',?)',vals+[datetime.datetime.now().isoformat()]); return self.json(201,{'id':cur.lastrowid})
            if path=='/api/food-log':
                food=c.execute('SELECT * FROM foods WHERE id=?',(data.get('food_id'),)).fetchone()
                if not food: return self.json(404,{'error':'Nie znaleziono produktu'})
                grams=float(data.get('grams') or 0); factor=grams/100
                cur=c.execute('INSERT INTO diary(profile_id,date,meal,name,calories,protein,carbs,fat,status,servings,food_id,grams) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(data.get('profile_id'),data.get('date'),data.get('meal'),food['name'],round(food['calories']*factor,1),round(food['protein']*factor,1),round(food['carbs']*factor,1),round(food['fat']*factor,1),data.get('status','eaten'),1,food['id'],grams)); return self.json(201,{'id':cur.lastrowid,'calories':round(food['calories']*factor,1)})
            if path=='/api/recipes':
                fields=['name','servings','ingredients','calories','protein','carbs','fat','notes','category','tags','prep_time','cook_time','instructions']; vals=[data.get(k) for k in fields]
                if isinstance(vals[2],list): vals[2]=json.dumps(vals[2],ensure_ascii=False)
                recipe_id=as_int(data.get('id'))
                if recipe_id:
                    c.execute('UPDATE recipes SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',vals+[recipe_id]); return self.json(200,{'id':recipe_id})
                cur=c.execute('INSERT INTO recipes('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',vals); return self.json(201,{'id':cur.lastrowid})
            if path=='/api/plans':
                cur=c.execute('INSERT INTO plans(profile_id,date,meal,recipe_id,name,servings,calories,protein,carbs,fat) VALUES(?,?,?,?,?,?,?,?,?,?)',(data.get('profile_id'),data.get('date') or datetime.date.today().isoformat(),data.get('meal'),data.get('recipe_id'),data.get('name'),data.get('servings',1),data.get('calories',0),data.get('protein',0),data.get('carbs',0),data.get('fat',0))); return self.json(201,{'id':cur.lastrowid})
            if path=='/api/plans/copy-day':
                source=data.get('source_date'); target=data.get('target_date')
                old=rows(c.execute('SELECT profile_id,date,meal,recipe_id,name,servings,calories,protein,carbs,fat FROM plans WHERE date=?',(source,)).fetchall())
                for x in old: c.execute('INSERT INTO plans(profile_id,date,meal,recipe_id,name,servings,calories,protein,carbs,fat) VALUES(?,?,?,?,?,?,?,?,?,?)',(x['profile_id'],target,x['meal'],x['recipe_id'],x['name'],x['servings'],x['calories'],x['protein'],x['carbs'],x['fat']))
                return self.json(200,{'copied':len(old)})
            if path=='/api/shopping-check':
                c.execute('INSERT INTO shopping_checks(date,item_key,checked) VALUES(?,?,?) ON CONFLICT(date,item_key) DO UPDATE SET checked=excluded.checked',(data.get('date'),data.get('item_key'),1 if data.get('checked') else 0)); return self.json(200,{'ok':True})
            if path=='/api/shopping-items':
                cur=c.execute('INSERT INTO shopping_items(date,name,quantity,unit,category) VALUES(?,?,?,?,?)',(data.get('date') or datetime.date.today().isoformat(),str(data.get('name','')).strip(),data.get('quantity',1),data.get('unit',''),data.get('category') or 'Inne'))
                return self.json(201,{'id':cur.lastrowid})
            if path=='/api/shopping-items/check':
                c.execute('UPDATE shopping_items SET checked=? WHERE id=?',(1 if data.get('checked') else 0,data.get('id'))); return self.json(200,{'ok':True})
            if path=='/api/import':
                tables=('profiles','recipes','foods','weigh_ins','diary','plans','shopping_checks','shopping_items')
                if not isinstance(data,dict) or any(not isinstance(data.get(t,[]),list) for t in tables): return self.json(400,{'error':'Plik nie jest kopią Domowego Menu'})
                for t in ('diary','plans','shopping_checks','shopping_items','weigh_ins','recipes','foods','profiles'): c.execute('DELETE FROM '+t)
                for t in tables:
                    cols=[r['name'] for r in c.execute('PRAGMA table_info('+t+')')]
                    for item in data.get(t,[]):
                        values={k:v for k,v in item.items() if k in cols}
                        if not values: continue
                        names=list(values); c.execute('INSERT INTO '+t+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',[values[k] for k in names])
                image_dir=DATA/'images'; image_dir.mkdir(parents=True,exist_ok=True)
                for image in data.get('recipe_images',[]):
                    try:
                        raw=base64.b64decode(image.get('data',''),validate=True); ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}.get(image.get('content_type'))
                        if not ext or len(raw)>8_000_000: continue
                        valid={'jpg':raw.startswith(b'\xff\xd8\xff'),'png':raw.startswith(b'\x89PNG\r\n\x1a\n'),'webp':len(raw)>12 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP'}
                        if not valid[ext]: continue
                        name=secrets.token_hex(16)+'.'+ext; (image_dir/name).write_bytes(raw); c.execute('UPDATE recipes SET image_path=? WHERE id=?',('/media/'+name,image.get('recipe_id')))
                    except (ValueError,TypeError): continue
                if c.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]==0: c.execute("INSERT INTO profiles(name) VALUES('Ja')")
                return self.json(200,{'ok':True})
            if path=='/api/plan-to-diary':
                row=c.execute('SELECT * FROM plans WHERE id=?',(data.get('id'),)).fetchone()
                if not row: return self.json(404,{'error':'Nie znaleziono planu'})
                recipe=c.execute('SELECT * FROM recipes WHERE id=?',(row['recipe_id'],)).fetchone() if row['recipe_id'] else None
                c.execute('INSERT INTO diary(profile_id,date,meal,name,calories,protein,carbs,fat,status,servings) VALUES(?,?,?,?,?,?,?,?,?,?)',(row['profile_id'],row['date'],row['meal'],row['name'],(recipe['calories'] if recipe else row['calories'] or 0)*row['servings'],(recipe['protein'] if recipe else row['protein'] or 0)*row['servings'],(recipe['carbs'] if recipe else row['carbs'] or 0)*row['servings'],(recipe['fat'] if recipe else row['fat'] or 0)*row['servings'],'planned',row['servings']))
                return self.json(201,{'ok':True})
        return self.send(404,'not found','text/plain')
    def do_PATCH(self):
        if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
        path=urlparse(self.path).path; d=self.body()
        if path=='/api/diary/status':
            with db() as c: c.execute('UPDATE diary SET status=? WHERE id=?',(d.get('status'),d.get('id')))
            return self.json(200,{'ok':True})
        if path=='/api/plans':
            with db() as c: c.execute('UPDATE plans SET recipe_id=?,name=?,calories=?,protein=?,carbs=?,fat=? WHERE id=?',(d.get('recipe_id'),d.get('name'),d.get('calories',0),d.get('protein',0),d.get('carbs',0),d.get('fat',0),d.get('id')))
            return self.json(200,{'ok':True})
        return self.send(404,'not found','text/plain')
    def do_DELETE(self):
        if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
        path=urlparse(self.path).path; d=parse_qs(urlparse(self.path).query)
        with db() as c:
            if path=='/api/diary': c.execute('DELETE FROM diary WHERE id=?',(d.get('id',[''])[0],))
            elif path=='/api/plans': c.execute('DELETE FROM plans WHERE id=?',(d.get('id',[''])[0],))
            elif path=='/api/weights': c.execute('DELETE FROM weigh_ins WHERE id=?',(d.get('id',[''])[0],))
            elif path=='/api/recipes': c.execute('DELETE FROM recipes WHERE id=?',(d.get('id',[''])[0],))
            elif path=='/api/foods': c.execute('DELETE FROM foods WHERE id=?',(d.get('id',[''])[0],))
            elif path=='/api/shopping-items': c.execute('DELETE FROM shopping_items WHERE id=?',(d.get('id',[''])[0],))
            else: return self.send(404,'not found','text/plain')
        return self.json(200,{'ok':True})

if __name__ == '__main__':
    if len(PASSWORD)<16 or 'ZMIEŃ' in PASSWORD or 'WSTAW_TUTAJ' in PASSWORD:
        raise SystemExit('Ustaw APP_PASSWORD na własne, unikalne hasło o długości co najmniej 16 znaków.')
    init()
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),Handler).serve_forever()
