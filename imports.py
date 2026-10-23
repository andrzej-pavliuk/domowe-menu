"""Read Mealie database backups without running or extracting their contents."""
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
import family as f
import app as core


def image_extension(raw):
    if raw.startswith(b'\xff\xd8\xff'): return 'jpg'
    if raw.startswith(b'\x89PNG\r\n\x1a\n'): return 'png'
    if len(raw)>12 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP': return 'webp'
    return None


def number(value):
    match=re.search(r'-?\d+(?:[.,]\d+)?',str(value or ''))
    return max(0,float(match[0].replace(',','.'))) if match else 0


def mealie(raw,c):
    imported=skipped=images=0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries=archive.infolist()
        if sum(x.file_size for x in entries)>500_000_000: raise ValueError('Rozpakowana kopia przekracza 500 MB')
        entry=next((x for x in entries if PurePosixPath(x.filename).name=='database.json'),None)
        if not entry: raise ValueError('W ZIP nie znaleziono database.json z kopii Mealie')
        tables=json.loads(archive.read(entry))
        if not isinstance(tables,dict): raise ValueError('Nieprawidłowa kopia Mealie')
        foods={str(x.get('id')):x.get('name') or '' for x in tables.get('ingredient_foods',[])}
        units={str(x.get('id')):x.get('name') or '' for x in tables.get('ingredient_units',[])}
        nutrition={str(x.get('recipe_id')):x for x in tables.get('recipe_nutrition',[])}
        ingredients,steps={},{}
        for x in tables.get('recipes_ingredients',[]): ingredients.setdefault(str(x.get('recipe_id')),[]).append(x)
        for x in tables.get('recipe_instructions',[]): steps.setdefault(str(x.get('recipe_id')),[]).append(x)
        for r in tables.get('recipes',[]):
            name=str(r.get('name') or '').strip()
            if not name: continue
            old_id=str(r.get('id') or '')
            marker='mealie-import:'+old_id
            previous=c.execute('SELECT value FROM settings WHERE key=?',(marker,)).fetchone()
            if previous and c.execute('SELECT 1 FROM recipes WHERE id=?',(previous[0],)).fetchone():
                skipped+=1;continue
            ing=[]
            for it in sorted(ingredients.get(old_id,[]),key=lambda x:number(x.get('position'))):
                food=foods.get(str(it.get('food_id')),'');unit=units.get(str(it.get('unit_id')),'')
                if food:
                    ing.append({'name':food,'qty':number(it.get('quantity')) or 1,'unit':unit or 'szt.','grams_each':None,'form':'fresh','months':[]})
                else:
                    text=str(it.get('original_text') or it.get('note') or '').strip()
                    if text: ing.append({'name':text,'qty':1,'unit':'porcja','grams_each':None})
            n=nutrition.get(old_id,{})
            instructions='\n'.join(str(x.get('text') or x.get('instruction') or '').strip() for x in sorted(steps.get(old_id,[]),key=lambda x:number(x.get('position'))))
            values=(name,max(.25,number(r.get('recipe_servings')) or 1),f.encode(ing),number(n.get('calories')),number(n.get('protein_content')),number(n.get('carbohydrate_content')),number(n.get('fat_content')),str(r.get('description') or ''),'Inne',str(r.get('prep_time') or ''),str(r.get('perform_time') or ''),instructions)
            cur=c.execute('INSERT INTO recipes(name,servings,ingredients,calories,protein,carbs,fat,notes,category,prep_time,cook_time,instructions) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',values)
            new_id=cur.lastrowid
            # Full-size images live at data/recipes/<UUID>/images/original.webp.
            picture=next((x for x in entries if old_id and '/'+old_id+'/images/' in '/'+x.filename and PurePosixPath(x.filename).name.startswith('original.') and x.file_size<=8_000_000),None)
            if picture:
                image=archive.read(picture);ext=image_extension(image)
                if ext:
                    filename=f.uid()+'.'+ext;folder=core.DATA/'images';folder.mkdir(exist_ok=True);(folder/filename).write_bytes(image)
                    c.execute('UPDATE recipes SET image_path=? WHERE id=?',('/media/'+filename,new_id));images+=1
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(marker,str(new_id)))
            imported+=1
    f.bump(c)
    return {'imported':imported,'skipped':skipped,'images':images,'message':'Sprawdź porcje, makroskładniki, typ posiłku i aktywny czas pracy w zaimportowanych przepisach.'}
