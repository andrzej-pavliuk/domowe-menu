import datetime as dt
import json
import io
import zipfile
import os
from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as core
import family as f
import server
import imports

class PlannerTests(unittest.TestCase):
    def recipe(self,id,meal,cal):
        return {'id':id,'name':meal,'calories':cal,'meal_types':[meal],'active_minutes':10,'total_minutes':15,'ingredients':[],'servings':1}
    def profiles(self):
        return [{'id':1,'name':'Andrzej','calories':2500,'tolerance_type':'percent','tolerance_value':5,'portion_factor':1}, {'id':2,'name':'Żona','calories':2000,'tolerance_type':'percent','tolerance_value':5,'portion_factor':1}]
    def test_common_main_meals_and_extra_personal_snack(self):
        rs=[self.recipe(i,m,k) for i,(m,k) in enumerate(zip((*f.MAIN_MEALS,'Przekąska'),(500,1000,500,500)),1)]
        result=f.generate(self.profiles(),rs,[],f.DEFAULTS,{'start_date':'2026-10-26','end_date':'2026-10-26','seed':1})
        shared=[x for x in result['entries'] if not x['profile_id']]
        snacks=[x for x in result['entries'] if x['profile_id']]
        self.assertEqual(len(shared),3)
        self.assertTrue(all(x['portions']['1']==x['portions']['2']==1 for x in shared))
        self.assertEqual([(s['profile_id'],s['portions']) for s in snacks],[(1,{'1':1})])
        self.assertTrue(all(b['accepted'] for b in result['days'][0]['balances']))
        self.assertEqual(result['attempts'],1)
    def test_in_tolerance_does_not_search_snacks(self):
        selected,b,count=f.snack_choices(self.profiles()[0],2512,[self.recipe(1,'Przekąska',100)],3,random.Random(1))
        self.assertEqual(selected,[]);self.assertTrue(b['accepted']);self.assertEqual(count,0)
    def test_priority_weekend_seasons_and_override(self):
        rule={'name':'Jesienny weekend','priority':20,'seasons':['jesień'],'weekdays':[5,6],'settings':{'active_limits':{'Obiad':90}}}
        conf,names=f.effective_rules(dt.date(2026,10,24),f.DEFAULTS,[rule])
        self.assertEqual(conf['active_limits']['Obiad'],90);self.assertEqual(names,['Jesienny weekend'])
        self.assertEqual(f.effective_rules(dt.date(2026,10,26),f.DEFAULTS,[rule])[0]['active_limits']['Obiad'],35)
        self.assertEqual(f.effective_rules(dt.date(2026,10,24),f.DEFAULTS,[rule],{'active_limits':{'Obiad':45}})[0]['active_limits']['Obiad'],45)
    def test_fresh_strawberries_excluded_in_autumn_but_preserved_allowed(self):
        r=self.recipe(1,'Śniadanie',500);r['ingredients']=[{'name':'truskawki','form':'fresh'}]
        conf={**f.DEFAULTS,'season_mode':'strict'}
        self.assertFalse(f.eligible(r,'Śniadanie',dt.date(2026,10,24),conf,[],[])[0])
        r['ingredients'][0]['form']='frozen'
        self.assertTrue(f.eligible(r,'Śniadanie',dt.date(2026,10,24),conf,[],[])[0])
        r['ingredients'][0]['form']='canned'
        self.assertTrue(f.eligible(r,'Śniadanie',dt.date(2026,10,24),conf,[],[])[0])
    def test_repeat_two_days_uses_same_batch(self):
        rs=[self.recipe(i,m,k) for i,(m,k) in enumerate(zip((*f.MAIN_MEALS,'Przekąska'),(500,1000,500,500)),1)]
        result=f.generate(self.profiles(),rs,[],{**f.DEFAULTS,'repeat_days':{m:2 for m in f.MAIN_MEALS}},{'start_date':'2026-10-26','end_date':'2026-10-27','seed':1})
        lunches=[x for x in result['entries'] if x['meal']=='Obiad']
        self.assertEqual(len(lunches),2);self.assertEqual(lunches[0]['batch_key'],lunches[1]['batch_key'])

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_data,cls.old_db=core.DATA,core.DB
        cls.temp=tempfile.TemporaryDirectory();core.DATA=Path(cls.temp.name);core.DB=core.DATA/'test.sqlite'
        server.init();cls.http=core.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start();cls.base='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();core.DATA,core.DB=cls.old_data,cls.old_db;cls.temp.cleanup()
    def setUp(self):
        with server.connection(write=True) as c:
            for t in reversed(server.BACKUP_TABLES):c.execute('DELETE FROM '+t)
            c.execute('DELETE FROM generator_drafts');c.execute("INSERT INTO profiles(id,name,calories,tolerance_type,tolerance_value) VALUES(1,'Ja',2500,'percent',5)")
        self.cookie=core.COOKIE+'='+core.token()
    def call(self,path,data=None,method=None,auth=True):
        headers={'Content-Type':'application/json'}
        if auth:headers['Cookie']=self.cookie
        req=Request(self.base+'/api/v1/'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method or ('POST' if data is not None else 'GET'))
        try:
            with urlopen(req,timeout=10) as r:return r.status,json.loads(r.read())
        except HTTPError as r:return r.code,json.loads(r.read())
    def make_recipe(self):
        code,res=self.call('recipes',{'name':'Ryż','servings':2,'calories':1000,'meal_types':['Obiad'],'active_minutes':20,'ingredients':[{'name':'ryż','qty':200,'unit':'g'}]})
        self.assertEqual(code,200);return res['id']
    def test_http_assets_login_and_service_worker_scope(self):
        for path,ctype in [('/', 'text/html'),('/static/app.js?v=041','text/javascript'),('/static/style.css?v=041','text/css'),('/static/sw.js','text/javascript')]:
            with urlopen(self.base+path) as r:
                self.assertEqual(r.status,200)
                self.assertTrue(r.headers['Content-Type'].startswith(ctype))
                self.assertGreater(len(r.read()),100)
                if path=='/static/sw.js':self.assertEqual(r.headers['Service-Worker-Allowed'],'/')
        previous=core.PASSWORD
        core.PASSWORD='test-password-long-enough'
        try:
            req=Request(self.base+'/api/login',data=json.dumps({'password':core.PASSWORD}).encode(),headers={'Content-Type':'application/json'})
            with urlopen(req) as r:
                self.assertIn('HttpOnly',r.headers['Set-Cookie']);self.assertIn('Secure',r.headers['Set-Cookie'])
        finally:core.PASSWORD=previous
    def test_authentication_required_and_invalid_payload_is_json(self):
        self.assertEqual(self.call('state',auth=False)[0],401)
        self.assertEqual(self.call('recipes',{'name':'','servings':0})[0],400)
    def test_weight_does_not_change_manual_limit(self):
        self.call('weight',{'profile_id':1,'weight':80,'date':'2026-10-23'})
        p=self.call('state')[1]['profiles'][0]
        self.assertEqual(p['weight'],80);self.assertEqual(p['calories'],2500)
    def test_batch_shopping_and_preserved_flags_with_diff(self):
        rid=self.make_recipe()
        self.assertEqual(self.call('meal',{'recipe_id':rid,'date':'2026-10-26','days':2,'meal':'Obiad','portions':{'1':1}})[0],200)
        lid=self.call('shopping_lists',{'name':'Tydzień','start_date':'2026-10-26','end_date':'2026-10-27'})[1]['id']
        first=self.call('shopping?id='+lid)[1];self.assertEqual(len(first['items']),1);self.assertEqual(first['items'][0]['quantity'],200)
        item=first['items'][0];self.call('shopping-flag',{'list_id':lid,'key':item['key'],'status':'home'});self.call('shopping-sync',{'id':lid})
        self.call('meal',{'recipe_id':rid,'date':'2026-10-28','meal':'Obiad','portions':{'1':1}})
        self.call('shopping_lists',{'id':lid,'name':'Tydzień','start_date':'2026-10-26','end_date':'2026-10-28'})
        result=self.call('shopping?id='+lid)[1];self.assertEqual(result['items'][0]['status'],'home');self.assertTrue(result['items'][0]['needs_review']);self.assertEqual(len(result['diff']['changed']),1)
    def test_log_plan_is_idempotent(self):
        rid=self.make_recipe();mid=self.call('meal',{'recipe_id':rid,'date':'2026-10-23','meal':'Obiad','portions':{'1':1}})[1]['id']
        for status in ('planned','eaten','eaten'):
            code,result=self.call('log-meal',{'id':mid,'profile_id':1,'status':status});self.assertEqual(code,200,result)
        self.assertEqual(len(self.call('state?date=2026-10-23')[1]['diary']),1)
    def test_stale_and_double_draft_approval_are_blocked(self):
        for m in f.MAIN_MEALS:self.call('recipes',{'name':m,'calories':800,'servings':1,'meal_types':[m],'active_minutes':10,'ingredients':[]})
        draft=self.call('generate',{'start_date':'2026-10-26','end_date':'2026-10-26'})[1]
        self.call('profiles',{'id':1,'calories':2400})
        self.assertEqual(self.call('approve',{'id':draft['id']})[0],400)
        draft=self.call('generate',{'start_date':'2026-10-26','end_date':'2026-10-26'})[1]
        self.assertEqual(self.call('approve',{'id':draft['id']})[0],200)
        self.assertEqual(self.call('approve',{'id':draft['id']})[0],400)
        self.assertEqual(len(self.call('state')[1]['meals']),3)
    def test_backup_restore_and_failed_restore_roll_back(self):
        self.make_recipe();backup=self.call('export')[1]
        self.call('recipes',{'name':'Nowy','ingredients':[]})
        self.assertEqual(self.call('restore',backup)[0],200)
        self.assertEqual(len(self.call('state')[1]['recipes']),1)
        backup['tables']['diary']=[{'profile_id':999,'date':'2026-10-23','name':'Zły'}]
        self.assertEqual(self.call('restore',backup)[0],400)
        self.assertEqual(len(self.call('state')[1]['recipes']),1)
        self.assertTrue((core.DATA/'przed-przywroceniem.sqlite').exists())
    def test_mealie_import_preserves_structured_ingredients_and_skips_duplicates(self):
        archive=io.BytesIO()
        tables={'recipes':[{'id':'abc','name':'Owsianka','recipe_servings':2,'prep_time':'10 min'}],
                'recipes_ingredients':[{'recipe_id':'abc','food_id':'food','unit_id':'unit','quantity':100}],
                'ingredient_foods':[{'id':'food','name':'płatki'}], 'ingredient_units':[{'id':'unit','name':'g'}],
                'recipe_instructions':[{'recipe_id':'abc','text':'Ugotuj','position':0}],
                'recipe_nutrition':[{'recipe_id':'abc','calories':'350 kcal'}]}
        with zipfile.ZipFile(archive,'w') as z: z.writestr('database.json',json.dumps(tables))
        with server.connection(write=True) as c: result=imports.mealie(archive.getvalue(),c)
        self.assertEqual(result['imported'],1)
        r=self.call('state')[1]['recipes'][0]
        self.assertEqual(r['ingredients'][0]['name'],'płatki');self.assertEqual(r['ingredients'][0]['qty'],100)
        self.assertEqual(r['active_minutes'],10);self.assertEqual(r['calories'],350)
        with server.connection(write=True) as c: result=imports.mealie(archive.getvalue(),c)
        self.assertEqual((result['imported'],result['skipped']),(0,1))
    def test_invalid_numeric_value_does_not_persist_or_send_success(self):
        self.assertEqual(self.call('profiles',{'id':1,'calories':'NaN'})[0],400)
        self.assertEqual(self.call('state')[1]['profiles'][0]['calories'],2500)
    def test_legacy_migration_keeps_plans(self):
        rid=self.make_recipe()
        with server.connection(write=True) as c:
            c.execute("INSERT INTO plans(profile_id,date,meal,recipe_id,name,servings) VALUES(1,'2026-10-23','Obiad',?,'Ryż',1)",(rid,))
            c.execute("DELETE FROM settings WHERE key='family_schema'")
        with server.connection() as c:f.init(c)
        self.assertEqual(len(self.call('state')[1]['meals']),1)

if __name__=='__main__':unittest.main()
