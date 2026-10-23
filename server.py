"""Domowe Menu 0.4.1: authenticated household API."""
import base64
import datetime as dt
import json
import os
import re
import sqlite3
import traceback
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import app as core
import family as f
import imports

VERSION = '0.4.1'
BASE_TABLES = ('profiles','weigh_ins','foods','recipes','diary','plans','shopping_checks','shopping_items')
BACKUP_TABLES = BASE_TABLES + f.TABLES[:-1]
JSON_FIELDS = {'ingredients','meal_types','seasons','months','weekdays','settings'}
NUMBERS = {'height','weight','target_weight','age_override','calories','protein','carbs','fat','rate','tolerance_value','portion_factor','servings','active_minutes','total_minutes','grams_each','grams','quantity','priority'}
FLAGS = {'pregnant','breastfeeding','individual_guidance','participates','favorite','archived','enabled'}
EDIT = {
 'profiles': 'name sex birth_date age_override height weight target_weight activity goal rate mode calories protein carbs fat tolerance_type tolerance_value start_date deadline pregnant breastfeeding individual_guidance restrictions portion_factor participates',
 'recipes': 'name servings ingredients calories protein carbs fat notes category tags prep_time cook_time instructions seasons meal_types active_minutes total_minutes favorite archived',
 'foods': 'name calories protein carbs fat unit measure grams_each form months group_name',
 'planner_rules': 'name enabled priority start_date end_date weekdays seasons settings',
 'diary': 'profile_id date meal name calories protein carbs fat status servings food_id grams',
 'shopping_manual': 'list_id name quantity unit category',
 'shopping_lists': 'name start_date end_date',
}

@contextmanager
def connection(write=False):
    c=core.db()
    try:
        if write: c.execute('BEGIN IMMEDIATE')
        yield c
        if write: c.commit()
    except Exception:
        if write: c.rollback()
        raise
    finally: c.close()

def date(value):
    return dt.date.fromisoformat(str(value)).isoformat()

def require(value, message):
    if not value: raise ValueError(message)

def rules(c):
    out=core.rows(c.execute('SELECT * FROM planner_rules ORDER BY priority,name'))
    for r in out:
        for k in ('weekdays','seasons','settings'): r[k]=f.unpack(r[k], {} if k=='settings' else [])
    return out

def defaults(c):
    r=c.execute("SELECT value FROM settings WHERE key='generator_defaults'").fetchone()
    return f.merge_settings(f.DEFAULTS,f.unpack(r[0],{}) if r else {})

def valid_settings(d):
    require(isinstance(d,dict),'Niepoprawne ustawienia generatora')
    allowed=set(f.DEFAULTS)
    require(not set(d)-allowed,'Nieznane ustawienie generatora')
    for k in ('repeat_days','active_limits','total_limits'):
        if k in d:
            require(isinstance(d[k],dict),'Niepoprawne limity posiłków')
            for meal,v in d[k].items():
                require(meal in f.MAIN_MEALS,'Nieznany typ posiłku')
                require(v is None or 0<=float(v)<=1440,'Limit poza zakresem')
    for k in ('exclude_ingredients','exclude_tags','preferred_tags'):
        if k in d: require(isinstance(d[k],list),'Tagi muszą być listą')
    require(d.get('season_mode','prefer') in ('prefer','strict','off'),'Nieznany tryb sezonu')
    return d

def save(c,table,data):
    ident=data.get('id'); existing=c.execute('SELECT * FROM '+table+' WHERE id=?',(ident,)).fetchone() if ident else None
    if ident: require(existing is not None,'Nie znaleziono wpisu')
    fields={k:v for k,v in data.items() if k in EDIT[table].split()}
    if 'name' in fields:
        fields['name']=str(fields['name']).strip()[:240]
        require(fields['name'],'Podaj nazwę')
    elif not existing: require(False,'Podaj nazwę')
    for k,v in list(fields.items()):
        if k in NUMBERS:
            if v in ('',None): fields[k]=None if k in ('height','weight','target_weight','age_override','calories','active_minutes','total_minutes','grams_each') else 0
            else:
                fields[k]=float(v)
                require(0<=fields[k]<1000000,'Niepoprawna wartość: '+k)
        if k in FLAGS: fields[k]=int(bool(v))
        if k in JSON_FIELDS:
            require(isinstance(v,dict if k=='settings' else list),'Niepoprawne pole: '+k)
            if k=='settings': valid_settings(v)
            fields[k]=f.encode(v)
        if k in ('date','start_date','end_date','birth_date','deadline'):
            fields[k]=date(v) if v else None
    if table=='profiles':
        p=dict(existing) if existing else {'portion_factor':1,'participates':1,'tolerance_type':'percent','tolerance_value':5}
        p.update(fields)
        require(p.get('tolerance_type') in ('percent','fixed'),'Niepoprawna tolerancja')
        require(f.numeric(p.get('tolerance_value'))<=1000,'Tolerancja poza zakresem')
        require(.25<=f.numeric(p.get('portion_factor'),1)<=3,'Porcja powinna wynosić od 0,25 do 3')
        for k in ('portion_factor','participates','tolerance_type','tolerance_value'): fields.setdefault(k,p[k])
    if table=='recipes':
        require(f.numeric(fields.get('servings',existing['servings'] if existing else 1),1)>0,'Liczba porcji musi być dodatnia')
        if 'ingredients' in fields:
            for ingredient in data['ingredients']:
                require(isinstance(ingredient,dict) and str(ingredient.get('name','')).strip(),'Podaj nazwę każdego składnika')
                require(f.numeric(ingredient.get('qty'),-1)>=0,'Niepoprawna ilość składnika')
                require(ingredient.get('form','fresh') in ('fresh','frozen','canned','jar','dried'),'Niepoprawna postać składnika')
                require(all(isinstance(m,int) and 1<=m<=12 for m in ingredient.get('months',[])),'Niepoprawne miesiące składnika')
    if table=='foods' and 'months' in data:
        require(all(isinstance(m,int) and 1<=m<=12 for m in data['months']),'Niepoprawne miesiące dostępności')
    if table=='diary':
        require(fields.get('status','eaten') in ('eaten','planned','skipped'),'Niepoprawny status')
        require(fields.get('profile_id') and fields.get('date'),'Wybierz domownika i datę')
    if table=='planner_rules':
        if fields.get('start_date') and fields.get('end_date'): require(fields['end_date']>=fields['start_date'],'Termin reguły musi przypadać po jej początku')
        require(all(isinstance(x,int) and 0<=x<=6 for x in data.get('weekdays',[])),'Niepoprawne dni tygodnia')
    if table=='shopping_lists':
        require(fields.get('start_date') and fields.get('end_date'),'Wybierz zakres dat')
        require(fields['end_date']>=fields['start_date'],'Niepoprawny zakres dat')
    if existing:
        c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),ident))
    else:
        if table in ('planner_rules','shopping_lists','shopping_manual'): fields['id']=ident=f.uid()
        cur=c.execute('INSERT INTO '+table+' ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',tuple(fields.values()))
        if not ident: ident=cur.lastrowid
    if table in ('recipes','profiles','foods','planner_rules'): f.bump(c)
    return ident

def add_meal(c,d,batch_id=None):
    r=c.execute('SELECT * FROM recipes WHERE id=?',(d.get('recipe_id'),)).fetchone()
    require(r is not None,'Wybierz przepis')
    day=date(d['date']); meal=d.get('meal','Obiad'); pid=d.get('profile_id')
    require(meal in (*f.MAIN_MEALS,'Przekąska'),'Niepoprawny posiłek')
    require((meal=='Przekąska')==bool(pid),'Przekąskę przypisz do osoby; główne posiłki są wspólne')
    portions=d.get('portions',{})
    require(isinstance(portions,dict) and portions,'Ustaw porcje domowników')
    valid={str(x[0]) for x in c.execute('SELECT id FROM profiles')}
    require(set(portions)<=valid and all(0<float(v)<=6 for v in portions.values()),'Niepoprawne porcje')
    if pid: require(set(portions)=={str(pid)},'Przekąska dotyczy jednej osoby')
    else: require(not c.execute('SELECT 1 FROM family_meals WHERE date=? AND meal=? AND profile_id IS NULL',(day,meal)).fetchone(),'Ten wspólny posiłek już istnieje. Najpierw go usuń lub zamień.')
    bid=batch_id or f.uid()
    if not batch_id:
        prepare=date(d.get('prepare_date') or day)
        require(prepare<=day,'Data gotowania powinna poprzedzać jedzenie')
        c.execute('INSERT INTO batches(id,recipe_id,prepare_date,cook_name,note,snapshot) VALUES(?,?,?,?,?,?)',(bid,r['id'],prepare,str(d.get('cook_name','')),str(d.get('note','')),f.encode(f.recipe_data(r))))
    mid=f.uid()
    c.execute('INSERT INTO family_meals VALUES(?,?,?,?,?,?)',(mid,bid,day,meal,pid,f.encode(portions)))
    return mid,bid

class Handler(core.Handler):
    server_version='DomoweMenu/'+VERSION
    def send(self,status,body,ctype='application/json; charset=utf-8',headers=None):
        headers=dict(headers or {})
        if urlparse(self.path).path=='/static/sw.js': headers['Service-Worker-Allowed']='/'
        return super().send(status,body,ctype,headers)
    def json(self,status,obj,headers=None):
        if getattr(self,'defer_json',False):
            self.pending_json=(status,obj,headers)
            return
        return super().json(status,obj,headers)
    def body(self):
        size=int(self.headers.get('Content-Length','0'))
        require(0<=size<=100_000_000,'Nieprawidłowy rozmiar żądania (maks. 100 MB)')
        def invalid(value): raise ValueError('Nieprawidłowa liczba JSON: '+value)
        value=json.loads(self.rfile.read(size) or b'{}',parse_constant=invalid)
        require(isinstance(value,dict),'Żądanie musi być obiektem JSON')
        return value
    def safe(self,method):
        try:
            if method!='GET':
                origin=self.headers.get('Origin')
                if origin and urlparse(origin).netloc!=self.headers.get('Host'):
                    return self.json(403,{'error':'Niedozwolone pochodzenie żądania'})
            path=urlparse(self.path).path
            if method=='POST' and path=='/api/import-mealie':
                if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
                size=int(self.headers.get('Content-Length','0'))
                require(0<size<=200_000_000,'Wybierz kopię ZIP do 200 MB')
                raw=self.rfile.read(size)
                try:
                    with connection(write=True) as c: result=imports.mealie(raw,c)
                except core.zipfile.BadZipFile: raise ValueError('To nie jest prawidłowy plik ZIP')
                return self.json(200,result)
            if path.startswith('/api/v1/'):
                if not self.authenticated(): return self.json(401,{'error':'Zaloguj się'})
                self.defer_json=True
                self.pending_json=None
                try: self.v1(method,path.removeprefix('/api/v1/'))
                finally: self.defer_json=False
                if self.pending_json: return self.json(*self.pending_json)
                return
            if method=='POST' and path not in ('/api/login','/api/logout','/api/import-mealie','/api/import-url','/api/recipes/image'):
                return self.json(404,{'error':'Nieznany adres API'})
            if method in ('PATCH','DELETE'): return self.json(404,{'error':'Nieznany adres API'})
            return getattr(super(), 'do_'+method)()
        except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as exc:
            self.json(400,{'error':str(exc)[:250] or 'Sprawdź wpisane dane'})
        except Exception:
            traceback.print_exc()
            self.json(500,{'error':'Wystąpił błąd. Szczegóły znajdują się w logach kontenera.'})
    def do_GET(self): self.safe('GET')
    def do_POST(self): self.safe('POST')
    def do_PATCH(self): self.safe('PATCH')
    def do_DELETE(self): self.safe('DELETE')
    def v1(self,method,path):
        query=parse_qs(urlparse(self.path).query)
        data=self.body() if method in ('POST','PATCH') else {k:v[0] for k,v in query.items()}
        if method=='GET' and path=='state':
            day=date(data.get('date',dt.date.today().isoformat()))
            with connection() as c:
                profiles=core.rows(c.execute('SELECT * FROM profiles ORDER BY id'))
                for p in profiles: p['calculation']=core.calculate(p)
                result={'version':VERSION,'date':day,'profiles':profiles,
                    'recipes':[f.recipe_data(r) for r in c.execute('SELECT * FROM recipes ORDER BY name')],
                    'foods':core.rows(c.execute('SELECT * FROM foods ORDER BY name')),
                    'weights':core.rows(c.execute('SELECT * FROM weigh_ins ORDER BY date,id')),
                    'diary':core.rows(c.execute('SELECT * FROM diary WHERE date=? ORDER BY id',(day,))),
                    'meals':f.meals(c), 'rules':rules(c),'defaults':defaults(c),
                    'lists':core.rows(c.execute('SELECT * FROM shopping_lists ORDER BY start_date DESC')),'revision':f.revision(c)}
            return self.json(200,result)
        if method=='GET' and path=='export':
            with connection() as c:
                result={'format':'domowe-menu','version':VERSION,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                        'tables':{t:core.rows(c.execute('SELECT * FROM '+t)) for t in BACKUP_TABLES},
                        'defaults':defaults(c),'images':{}}
                for r in result['tables']['recipes']:
                    name=Path(r.get('image_path') or '').name
                    if re.fullmatch(r'[a-f0-9]{32}\.(jpg|png|webp)',name) and (core.DATA/'images'/name).is_file():
                        result['images'][name]=base64.b64encode((core.DATA/'images'/name).read_bytes()).decode()
            return self.json(200,result,{'Content-Disposition':'attachment; filename="domowe-menu-kopia-'+dt.date.today().isoformat()+'.json"'})
        if method=='GET' and path=='shopping':
            with connection() as c: result=f.shopping_items(c,data.get('id'))
            return self.json(200,result)
        with connection(write=True) as c:
            if method=='POST' and path in EDIT:
                return self.json(200,{'id':save(c,path,data)})
            if method=='DELETE' and path in (*EDIT,'weigh_ins','family_meals'):
                require(data.get('id'),'Brak identyfikatora')
                if path=='profiles':
                    require(c.execute('SELECT count(*) FROM profiles').fetchone()[0]>1,'Nie można usunąć ostatniego domownika')
                    for m in c.execute('SELECT id,portions FROM family_meals WHERE profile_id IS NULL').fetchall():
                        portions=f.unpack(m['portions'],{}); portions.pop(str(data['id']),None)
                        c.execute('UPDATE family_meals SET portions=? WHERE id=?',(f.encode(portions),m['id']))
                if path=='family_meals': c.execute("DELETE FROM diary WHERE source_meal_id=? AND status='planned'",(data['id'],))
                if path=='weigh_ins':
                    removed=c.execute('SELECT profile_id FROM weigh_ins WHERE id=?',(data['id'],)).fetchone()
                c.execute('DELETE FROM '+path+' WHERE id=?',(data['id'],))
                if path=='weigh_ins' and removed:
                    latest=c.execute('SELECT weight FROM weigh_ins WHERE profile_id=? ORDER BY date DESC,id DESC LIMIT 1',(removed[0],)).fetchone()
                    if latest: c.execute('UPDATE profiles SET weight=? WHERE id=?',(latest[0],removed[0]))
                f.bump(c)
                return self.json(200,{'ok':True})
            if method=='POST' and path=='weight':
                weight=float(data['weight']); require(1<=weight<=500,'Podaj masę ciała w kg')
                c.execute('INSERT INTO weigh_ins(profile_id,date,weight,note) VALUES(?,?,?,?)',(data['profile_id'],date(data['date']),weight,str(data.get('note',''))))
                latest=c.execute('SELECT weight FROM weigh_ins WHERE profile_id=? ORDER BY date DESC,id DESC LIMIT 1',(data['profile_id'],)).fetchone()
                c.execute('UPDATE profiles SET weight=? WHERE id=?',(latest[0],data['profile_id']))
                return self.json(200,{'ok':True})
            if method=='POST' and path=='estimate':
                p=dict(c.execute('SELECT * FROM profiles WHERE id=?',(data['id'],)).fetchone() or {})
                p.update({k:v for k,v in data.items() if k in EDIT['profiles'].split()})
                return self.json(200,core.calculate(p))
            if method=='POST' and path=='defaults':
                v=valid_settings(data)
                c.execute("INSERT OR REPLACE INTO settings VALUES('generator_defaults',?)",(f.encode(f.merge_settings(f.DEFAULTS,v)),)); f.bump(c)
                return self.json(200,{'ok':True})
            if method=='POST' and path=='meal':
                first=None; bid=None
                for i in range(max(1,min(31,int(data.get('days',1))))):
                    d=dict(data,date=(dt.date.fromisoformat(data['date'])+dt.timedelta(days=i)).isoformat())
                    first,bid=add_meal(c,d,bid)
                f.bump(c); return self.json(200,{'id':first})
            if method=='POST' and path=='copy-day':
                source=date(data['source']); target=date(data['target']); require(source!=target,'Wybierz inny dzień')
                for m in f.meals(c,source,source):
                    if not m['recipe_id']: continue
                    # Copy as a new cooking batch, not undeclared leftovers.
                    add_meal(c,{'recipe_id':m['recipe_id'],'date':target,'meal':m['meal'],'profile_id':m['profile_id'],'portions':m['portions']})
                f.bump(c); return self.json(200,{'ok':True})
            if method=='POST' and path=='swap':
                old=c.execute('SELECT * FROM family_meals WHERE id=?',(data['id'],)).fetchone(); require(old,'Nie znaleziono posiłku')
                old=dict(old); r=c.execute('SELECT * FROM recipes WHERE id=?',(data['recipe_id'],)).fetchone(); require(r,'Nie znaleziono przepisu')
                # Swapping one day splits it from the original multi-day batch.
                c.execute('DELETE FROM family_meals WHERE id=?',(old['id'],))
                mid,bid=add_meal(c,{'recipe_id':r['id'],'date':old['date'],'meal':old['meal'],'profile_id':old['profile_id'],'portions':f.unpack(old['portions'],{})})
                for diary in c.execute('SELECT * FROM diary WHERE source_meal_id=?',(old['id'],)).fetchall():
                    if diary['status']=='planned': c.execute('DELETE FROM diary WHERE id=?',(diary['id'],))
                f.bump(c); return self.json(200,{'id':mid})
            if method=='POST' and path=='batch':
                b=c.execute('SELECT * FROM batches WHERE id=?',(data['id'],)).fetchone(); require(b,'Nie znaleziono gotowania')
                values={k:data[k] for k in ('cook_name','note','prepared','cook_state','prepare_date') if k in data}
                if 'cook_state' in values: values['cook_state']=f.encode(values['cook_state'])
                if 'prepare_date' in values:
                    values['prepare_date']=date(values['prepare_date'])
                    first=c.execute('SELECT min(date) FROM family_meals WHERE batch_id=?',(data['id'],)).fetchone()[0]
                    require(not first or values['prepare_date']<=first,'Gotowanie powinno poprzedzać jedzenie')
                if values: c.execute('UPDATE batches SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),data['id']))
                if 'prepare_date' in values: f.bump(c)
                return self.json(200,{'ok':True})
            if method=='POST' and path=='log-meal':
                m=next((m for m in f.meals(c) if m['id']==data['id']),None); require(m,'Nie znaleziono posiłku')
                pid=str(data['profile_id']); require(pid in m['portions'],'Ta osoba nie ma porcji w tym posiłku')
                amount=f.numeric(m['portions'][pid]); r=m['recipe']; status=data.get('status','eaten')
                require(status in ('eaten','planned','skipped'),'Niepoprawny status')
                vals=(int(pid),m['date'],m['meal'],r['name'],*[f.numeric(r.get(k))*amount for k in ('calories','protein','carbs','fat')],status,amount,m['id'])
                c.execute('INSERT INTO diary(profile_id,date,meal,name,calories,protein,carbs,fat,status,servings,source_meal_id) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_meal_id,profile_id) WHERE source_meal_id IS NOT NULL DO UPDATE SET status=excluded.status',vals)
                return self.json(200,{'ok':True})
            if method=='POST' and path=='diary-status':
                require(data['status'] in ('eaten','planned','skipped'),'Niepoprawny status')
                c.execute('UPDATE diary SET status=? WHERE id=?',(data['status'],data['id'])); return self.json(200,{'ok':True})
            if method=='POST' and path=='generate':
                valid_settings(data.get('overrides',{}))
                profiles=core.rows(c.execute('SELECT * FROM profiles')); recipes=[f.recipe_data(r) for r in c.execute('SELECT * FROM recipes')]
                proposal=f.generate(profiles,recipes,rules(c),defaults(c),data,core.rows(c.execute('SELECT * FROM foods')),f.meals(c))
                ident=f.uid(); c.execute('DELETE FROM generator_drafts WHERE created_at < ?',((dt.datetime.now()-dt.timedelta(days=2)).isoformat(),))
                c.execute('INSERT INTO generator_drafts(id,payload,revision,created_at) VALUES(?,?,?,?)',(ident,f.encode(proposal),f.revision(c),dt.datetime.now().isoformat()))
                return self.json(200,{'id':ident,**proposal})
            if method=='POST' and path=='approve':
                draft=c.execute('SELECT * FROM generator_drafts WHERE id=?',(data['id'],)).fetchone(); require(draft,'Podgląd wygasł. Wygeneruj nowy.')
                require(not draft['applied'],'Ten plan już zatwierdzono')
                require(draft['revision']==f.revision(c),'Dane zmieniły się od podglądu. Wygeneruj plan ponownie.')
                batches={}
                for entry in f.unpack(draft['payload'],{})['entries']:
                    mid,bid=add_meal(c,entry,batches.get(entry['batch_key'])); batches[entry['batch_key']]=bid
                c.execute('UPDATE generator_drafts SET applied=1 WHERE id=?',(data['id'],)); f.bump(c)
                return self.json(200,{'ok':True})
            if method=='POST' and path=='shopping-flag':
                require(data['status'] in ('needed','purchased','home'),'Niepoprawny status zakupów')
                current=f.shopping_items(c,data['list_id']); item=next((x for x in current['items'] if x['key']==data['key']),None); require(item,'Nie znaleziono produktu')
                c.execute('INSERT OR REPLACE INTO shopping_flags VALUES(?,?,?,?)',(data['list_id'],data['key'],data['status'],item['quantity']))
                return self.json(200,{'ok':True})
            if method=='POST' and path=='shopping-sync':
                current=f.shopping_items(c,data['id'])
                c.execute('UPDATE shopping_lists SET snapshot=?,updated_at=? WHERE id=?',(f.encode(current['items']),dt.datetime.now().isoformat(),data['id']))
                return self.json(200,{'ok':True})
            if method=='POST' and path=='restore':
                require(data.get('format')=='domowe-menu' and isinstance(data.get('tables'),dict),'Wybierz kopię Domowego Menu 0.4')
                tables=data['tables']; require(tables.get('profiles') is not None and tables.get('recipes') is not None,'Niepełna kopia')
                # Keep the current data as an independent recovery database first.
                backup=sqlite3.connect(core.DATA/'przed-przywroceniem.sqlite')
                with connection() as source: source.backup(backup)
                backup.close()
                for name,encoded in data.get('images',{}).items():
                    require(re.fullmatch(r'[a-f0-9]{32}\.(jpg|png|webp)',name),'Niepoprawny obraz w kopii')
                    raw=base64.b64decode(encoded,validate=True); require(len(raw)<=8_000_000,'Za duży obraz')
                    folder=core.DATA/'images'; folder.mkdir(exist_ok=True); (folder/name).write_bytes(raw)
                for table in reversed(BACKUP_TABLES): c.execute('DELETE FROM '+table)
                c.execute('DELETE FROM generator_drafts')
                for table in BACKUP_TABLES:
                    allowed={x['name'] for x in c.execute('PRAGMA table_info('+table+')')}
                    for row in tables.get(table,[]):
                        values={k:v for k,v in row.items() if k in allowed}
                        c.execute('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
                c.execute("INSERT OR REPLACE INTO settings VALUES('generator_defaults',?)",(f.encode(valid_settings(data.get('defaults',{}))),))
                f.bump(c); return self.json(200,{'ok':True})
        return self.json(404,{'error':'Nieznany adres API'})

def init():
    core.init()
    with connection() as c: f.init(c)

if __name__=='__main__':
    if len(core.PASSWORD)<16 or any(x in core.PASSWORD for x in ('ZMIEŃ','WSTAW_TUTAJ')):
        raise SystemExit('Ustaw APP_PASSWORD na własne, unikalne hasło o długości co najmniej 16 znaków.')
    init()
    print('Domowe Menu '+VERSION+' · http://0.0.0.0:'+os.getenv('PORT','8080'),flush=True)
    core.ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),Handler).serve_forever()
