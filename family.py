"""Local family planner and recurring rules. No network calls or AI."""
import datetime as dt
import itertools
import json
import random
import re
import unicodedata
import uuid

MAIN_MEALS = ('Śniadanie', 'Obiad', 'Kolacja')
SEASONS = ('zima', 'wiosna', 'lato', 'jesień')
DEFAULTS = {
    'repeat_days': {'Śniadanie': 1, 'Obiad': 2, 'Kolacja': 1},
    'active_limits': {'Śniadanie': 15, 'Obiad': 35, 'Kolacja': 15},
    'total_limits': {}, 'season_mode': 'strict', 'min_gap': 3,
    'max_week': 2, 'max_snacks': 3, 'attempts': 1200,
    'exclude_ingredients': [], 'exclude_tags': [], 'preferred_tags': [],
}
TABLES = ('batches', 'family_meals', 'planner_rules', 'shopping_lists',
          'shopping_flags', 'shopping_manual', 'generator_drafts')
FRESH_MONTHS = {
    'szparagi': [4, 5, 6], 'truskawki': [5, 6, 7], 'maliny': [6, 7, 8, 9],
    'borowki': [7, 8, 9], 'czeresnie': [6, 7], 'sliwki': [8, 9, 10],
    'dynia': [8, 9, 10, 11], 'rabarbar': [4, 5, 6], 'bob': [6, 7, 8],
}


def uid():
    return uuid.uuid4().hex


def unpack(value, default=None):
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def key(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower().replace('ł', 'l'))
                   if not unicodedata.combining(c)).strip()


def season(date):
    return SEASONS[(date.month % 12) // 3]


def numeric(value, default=0):
    try:
        result = float(value)
        return result if result == result and abs(result) < 1e9 else default
    except (TypeError, ValueError):
        return default


def minutes(value):
    if isinstance(value, (float, int)):
        return max(0, float(value))
    text = str(value or '').lower()
    if not text.strip():
        return None
    hours = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:h|godz)', text)
    mins = re.search(r'(\d+(?:[.,]\d+)?)\s*min', text)
    if hours or mins:
        return (numeric(hours[1].replace(',', '.')) * 60 if hours else 0) + (numeric(mins[1].replace(',', '.')) if mins else 0)
    match = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*', text)
    return numeric(match[1].replace(',', '.')) if match else None


def recipe_data(row):
    r = dict(row)
    r['ingredients'] = unpack(r.get('ingredients'), [])
    r['seasons'] = unpack(r.get('seasons'), [])
    r['meal_types'] = unpack(r.get('meal_types'), [])
    if not r['meal_types']:
        category = key(r.get('category'))
        r['meal_types'] = [m for m in MAIN_MEALS if key(m) in category]
        if any(s in category for s in ('przekask', 'deser', 'snack')):
            r['meal_types'].append('Przekąska')
    r['active_minutes'] = minutes(r.get('active_minutes')) if r.get('active_minutes') is not None else minutes(r.get('prep_time'))
    r['total_minutes'] = minutes(r.get('total_minutes')) if r.get('total_minutes') is not None else (
        (minutes(r.get('prep_time')) or 0) + (minutes(r.get('cook_time')) or 0)
        if r.get('prep_time') or r.get('cook_time') else None)
    return r


def init(c):
    def column(table, name, definition):
        if name not in [r['name'] for r in c.execute('PRAGMA table_info(' + table + ')')]:
            c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    for name, definition in [('seasons', "TEXT DEFAULT '[]'"), ('meal_types', "TEXT DEFAULT '[]'"),
                             ('active_minutes', 'REAL'), ('total_minutes', 'REAL'),
                             ('favorite', 'INTEGER DEFAULT 0'), ('archived', 'INTEGER DEFAULT 0')]:
        column('recipes', name, definition)
    column('profiles', 'portion_factor', 'REAL DEFAULT 1')
    column('profiles', 'participates', 'INTEGER DEFAULT 1')
    column('diary', 'source_meal_id', 'TEXT')
    for name, definition in [('measure', "TEXT DEFAULT 'g'"), ('grams_each', 'REAL DEFAULT 1'),
                             ('form', "TEXT DEFAULT 'fresh'"), ('months', "TEXT DEFAULT '[]'"),
                             ('group_name', "TEXT DEFAULT ''")]:
        column('foods', name, definition)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS batches(
        id TEXT PRIMARY KEY, recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
        prepare_date TEXT NOT NULL, cook_name TEXT DEFAULT '', note TEXT DEFAULT '',
        prepared INTEGER DEFAULT 0, cook_state TEXT DEFAULT '{}', snapshot TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_meals(
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
        date TEXT NOT NULL, meal TEXT NOT NULL, profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
        portions TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS family_meals_date ON family_meals(date);
    CREATE TABLE IF NOT EXISTS planner_rules(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER DEFAULT 1, priority INTEGER DEFAULT 10,
        start_date TEXT, end_date TEXT, weekdays TEXT DEFAULT '[]', seasons TEXT DEFAULT '[]', settings TEXT DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS shopping_lists(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        snapshot TEXT DEFAULT '[]', updated_at TEXT);
    CREATE TABLE IF NOT EXISTS shopping_flags(
        list_id TEXT REFERENCES shopping_lists(id) ON DELETE CASCADE, item_key TEXT,
        status TEXT NOT NULL, marked_quantity REAL DEFAULT 0, PRIMARY KEY(list_id,item_key));
    CREATE TABLE IF NOT EXISTS shopping_manual(
        id TEXT PRIMARY KEY, list_id TEXT REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name TEXT NOT NULL, quantity REAL DEFAULT 1, unit TEXT DEFAULT 'szt.', category TEXT DEFAULT 'Inne');
    CREATE TABLE IF NOT EXISTS generator_drafts(
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL, applied INTEGER DEFAULT 0,
        created_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS diary_source_unique ON diary(source_meal_id,profile_id)
        WHERE source_meal_id IS NOT NULL;
    ''')
    if not c.execute("SELECT 1 FROM settings WHERE key='family_schema'").fetchone():
        # Preserve earlier per-person plans; same dishes become one shared event.
        old = [dict(r) for r in c.execute('SELECT * FROM plans ORDER BY date,id')]
        grouped = {}
        for item in old:
            grouped.setdefault((item['date'], item['meal'], item['recipe_id'], item['name']), []).append(item)
        for (date, meal, rid, name), entries in grouped.items():
            source = c.execute('SELECT * FROM recipes WHERE id=?', (rid,)).fetchone() if rid else None
            snapshot = recipe_data(source) if source else dict(entries[0], name=name, ingredients=[], servings=1)
            batch_id = uid()
            c.execute('INSERT INTO batches(id,recipe_id,prepare_date,snapshot) VALUES(?,?,?,?)',
                      (batch_id, rid, date, encode(snapshot)))
            c.execute('INSERT INTO family_meals VALUES(?,?,?,?,?,?)',
                      (uid(), batch_id, date, meal, None, encode({str(e['profile_id']): e['servings'] or 1 for e in entries})))
        c.execute("INSERT INTO settings(key,value) VALUES('family_schema','1')")
    c.execute("INSERT OR IGNORE INTO settings VALUES('plan_revision','0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES('generator_defaults',?)", (encode(DEFAULTS),))


def bump(c):
    c.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='plan_revision'")


def revision(c):
    return int(c.execute("SELECT value FROM settings WHERE key='plan_revision'").fetchone()[0])


def meals(c, start=None, end=None):
    query = 'SELECT m.*,b.recipe_id,b.prepare_date,b.cook_name,b.note,b.prepared,b.cook_state,b.snapshot FROM family_meals m JOIN batches b ON b.id=m.batch_id'
    values = []
    if start:
        query += ' WHERE m.date>=? AND m.date<=?'
        values = [start, end or start]
    out = []
    for row in c.execute(query + ' ORDER BY m.date,m.rowid', values):
        row = dict(row)
        row['portions'] = unpack(row['portions'], {})
        row['recipe'] = unpack(row.pop('snapshot'), {})
        out.append(row)
    return out


def balance(profile, value):
    target = numeric(profile.get('calories'))
    tol = numeric(profile.get('tolerance_value'), 5)
    tol = target * tol / 100 if profile.get('tolerance_type') == 'percent' else tol
    return dict(target=target, actual=round(value, 1), deviation=round(value-target, 1), tolerance=round(tol, 1),
                low=round(max(0, target-tol), 1), high=round(target+tol, 1),
                accepted=not target or target-tol <= value <= target+tol)


def merge_settings(base, update):
    out = dict(base)
    for name, value in update.items():
        out[name] = {**out.get(name, {}), **value} if isinstance(value, dict) else value
    return out


def effective_rules(date, defaults, rules, overrides=None):
    config = merge_settings(DEFAULTS, defaults)
    applied = []
    for rule in sorted(rules, key=lambda r: (numeric(r.get('priority')), r.get('name', ''))):
        if not rule.get('enabled', True):
            continue
        if rule.get('start_date') and date.isoformat() < rule['start_date']:
            continue
        if rule.get('end_date') and date.isoformat() > rule['end_date']:
            continue
        if rule.get('weekdays') and date.weekday() not in rule['weekdays']:
            continue
        if rule.get('seasons') and season(date) not in rule['seasons']:
            continue
        config = merge_settings(config, rule.get('settings') or {})
        applied.append(rule['name'])
    return merge_settings(config, overrides or {}), applied


def ingredient_months(ingredient, foods):
    months = ingredient.get('months')
    if months:
        return months
    name, form = key(ingredient.get('name')), ingredient.get('form') or 'fresh'
    for food in foods:
        if key(food.get('name')) == name and (food.get('form') or 'fresh') == form:
            months = unpack(food.get('months'), [])
            if months:
                return months
    if form != 'fresh':
        return list(range(1, 13))
    return next((m for n, m in FRESH_MONTHS.items() if n in name), list(range(1, 13)))


def eligible(recipe, meal, date, config, profiles, foods):
    if recipe.get('archived') or numeric(recipe.get('calories')) <= 0 or meal not in recipe.get('meal_types', []):
        return False, 0
    limit = config.get('active_limits', {}).get(meal)
    total_limit = config.get('total_limits', {}).get(meal)
    if limit and (recipe.get('active_minutes') is None or recipe['active_minutes'] > numeric(limit)):
        return False, 0
    if total_limit and (recipe.get('total_minutes') is None or recipe['total_minutes'] > numeric(total_limit)):
        return False, 0
    tags = key(recipe.get('tags', ''))
    if any(key(t) in tags for t in config.get('exclude_tags', []) if t):
        return False, 0
    restrictions = list(config.get('exclude_ingredients', []))
    for profile in profiles:
        restrictions += re.split(r'[,;\n]+', profile.get('restrictions') or '')
    ingredients = recipe.get('ingredients') or []
    names = ' '.join(key(i if isinstance(i, str) else i.get('name', '')) for i in ingredients)
    if any(key(x) in names for x in restrictions if key(x)):
        return False, 0
    out_of_season = bool(recipe.get('seasons') and season(date) not in recipe['seasons'])
    out_of_season |= any(date.month not in ingredient_months(i, foods) for i in ingredients if isinstance(i, dict))
    mode = config.get('season_mode', 'prefer')
    if mode == 'strict' and out_of_season:
        return False, 0
    score = (100 if mode == 'prefer' and out_of_season else 0)
    score -= sum(10 for tag in config.get('preferred_tags', []) if key(tag) in tags)
    return True, score


def snack_choices(profile, value, candidates, max_count, rng):
    """Stop as soon as tolerance is reached; portions stay practical."""
    initial = balance(profile, value)
    if initial['accepted'] or value > initial['high'] or max_count <= 0:
        return [], initial, 0
    options = [(r, scale) for r in candidates for scale in (.5, .75, 1, 1.25, 1.5, 2)]
    rng.shuffle(options)
    options.sort(key=lambda x: abs(initial['target']-value-numeric(x[0]['calories'])*x[1]))
    options = options[:60]
    beam = [(value, [])]
    best = (abs(value-initial['target']), [], initial)
    attempts = 0
    for _ in range(max_count):
        next_beam = []
        for total, chosen in beam:
            for recipe, scale in options:
                attempts += 1
                amount = total + numeric(recipe['calories'])*scale
                result = balance(profile, amount)
                new = chosen+[(recipe, scale)]
                if result['accepted']:
                    return new, result, attempts
                if abs(amount-result['target']) < best[0]:
                    best = (abs(amount-result['target']), new, result)
                if amount < result['high']:
                    next_beam.append((amount, new))
        next_beam.sort(key=lambda x: abs(x[0]-initial['target']))
        beam = next_beam[:12]
        if not beam:
            break
    return best[1], best[2], attempts


def generate(profiles, recipes, rules, defaults, params, foods=None, existing=None):
    """Generate a reviewable proposal without touching the database."""
    start = dt.date.fromisoformat(params['start_date'])
    end = dt.date.fromisoformat(params['end_date'])
    if end < start or (end-start).days > 30:
        raise ValueError('Wybierz okres od 1 do 31 dni')
    profiles = [p for p in profiles if p.get('participates', 1)]
    if not profiles:
        raise ValueError('Włącz przynajmniej jednego domownika w planowaniu')
    rng = random.Random(params.get('seed') or uid())
    foods, existing = foods or [], existing or []
    fixed = {(m['date'], m['meal']): m for m in existing if m.get('profile_id') is None}
    history = {}
    for event in existing:
        if event['recipe_id'] and event['prepare_date'] < start.isoformat():
            history.setdefault(event['recipe_id'], set()).add(event['prepare_date'])
    held, days, entries, warnings = {}, [], [], []
    max_attempts, snack_attempts = 0, 0
    for day_index in range((end-start).days+1):
        day = start + dt.timedelta(days=day_index)
        config, applied = effective_rules(day, defaults, rules, params.get('overrides'))
        pools, locks = {}, {}
        for meal in MAIN_MEALS:
            exact = fixed.get((day.isoformat(), meal))
            if exact:
                locks[meal] = exact
                pools[meal] = [(exact['recipe'], None, exact)]
                continue
            candidates = []
            for recipe in recipes:
                allowed, score = eligible(recipe, meal, day, config, profiles, foods)
                if not allowed:
                    continue
                uses = history.get(recipe['id'], set())
                min_gap = max(0, int(numeric(config.get('min_gap'))))
                if any(0 <= (day-dt.date.fromisoformat(d)).days < min_gap for d in uses):
                    continue
                monday = day-dt.timedelta(days=day.weekday())
                if sum(monday.isoformat() <= d <= day.isoformat() for d in uses) >= max(1, int(numeric(config.get('max_week'), 2))):
                    continue
                candidates.append((score+rng.random()*8, recipe))
            active = held.get(meal)
            if active and active['until'] >= day and eligible(active['recipe'], meal, day, config, profiles, foods)[0]:
                pools[meal] = [(active['recipe'], active['scale'], active)]
                locks[meal] = active
            else:
                candidates.sort(key=lambda x: x[0])
                pools[meal] = [(r, None, None) for _, r in candidates[:12]]
            if not pools[meal]:
                warnings.append(f'{day.isoformat()} · {meal}: brak przepisu spełniającego czas, sezon, ograniczenia i powtarzalność. Uzupełnij przepisy lub zmień regułę.')
        if any(not pools.get(meal) for meal in MAIN_MEALS):
            days.append({'date': day.isoformat(), 'rules': applied, 'balances': [], 'complete': False})
            continue
        positive = [numeric(p.get('calories'))/max(.25, numeric(p.get('portion_factor'), 1)) for p in profiles if numeric(p.get('calories')) > 0]
        base_target = min(positive) if positive else 2000
        variants = []
        for choice in itertools.product(*(pools[m] for m in MAIN_MEALS)):
            for scale in (.75, 1, 1.25, 1.5):
                total = sum(numeric(x[0]['calories'])*(x[1] if x[1] else scale) for x in choice)
                penalty = sum(eligible(x[0], meal, day, config, profiles, foods)[1] for meal, x in zip(MAIN_MEALS, choice))
                variants.append((abs(total-base_target)+penalty, rng.random(), choice, scale))
        variants.sort(key=lambda x: (x[0], x[1]))
        best, best_score = None, float('inf')
        max_day_attempts = min(5000, max(1, int(numeric(config.get('attempts'), 1200))))
        for attempts, (_, _, choice, scale) in enumerate(variants[:max_day_attempts], 1):
            selections, balances, score = [], [], 0
            for profile in profiles:
                pid = str(profile['id'])
                value = 0
                for recipe, held_scale, locked in choice:
                    portion = numeric(locked['portions'].get(pid), 0) if locked and 'portions' in locked else (held_scale or scale)*numeric(profile.get('portion_factor'), 1)
                    value += numeric(recipe['calories'])*portion
                old_snacks = [m for m in existing if m['date'] == day.isoformat() and m.get('profile_id') == profile['id']]
                value += sum(numeric(m['recipe']['calories'])*numeric(m['portions'].get(pid)) for m in old_snacks)
                snack_pool = [r for r in recipes if eligible(r, 'Przekąska', day, config, [profile], foods)[0]]
                chosen, result, count = snack_choices(profile, value, snack_pool,
                    max(0, min(5, int(numeric(config.get('max_snacks'), 3)))-len(old_snacks)), rng)
                snack_attempts += count
                selections.append((profile, chosen))
                balances.append({'profile_id': profile['id'], 'name': profile['name'], **result})
                if result['target']:
                    score += max(0, abs(result['deviation'])-result['tolerance'])/result['target']
            if score < best_score:
                best_score = score
                best = choice, scale, selections, balances
            if all(b['accepted'] for b in balances):
                break
        max_attempts += attempts
        choice, scale, selections, balances = best
        for meal, (recipe, held_scale, locked) in zip(MAIN_MEALS, choice):
            if locked and 'id' in locked:
                continue  # Existing shared meal already lives in the database.
            use_scale = held_scale or scale
            if not locked:
                batch_key = uid()
                repeat = max(1, min(31, int(numeric(config.get('repeat_days', {}).get(meal), 1))))
                locked = {'batch_key': batch_key, 'recipe': recipe, 'scale': use_scale,
                          'until': day+dt.timedelta(days=repeat-1), 'prepare_date': day.isoformat()}
                held[meal] = locked
                history.setdefault(recipe['id'], set()).add(day.isoformat())
            entries.append({'batch_key': locked['batch_key'], 'date': day.isoformat(), 'meal': meal,
                            'recipe_id': recipe['id'], 'prepare_date': locked['prepare_date'], 'profile_id': None,
                            'portions': {str(p['id']): round(use_scale*numeric(p.get('portion_factor'), 1), 2) for p in profiles}})
        for profile, chosen in selections:
            for recipe, portion in chosen:
                entries.append({'batch_key': uid(), 'date': day.isoformat(), 'meal': 'Przekąska',
                    'recipe_id': recipe['id'], 'prepare_date': day.isoformat(), 'profile_id': profile['id'],
                    'portions': {str(profile['id']): portion}})
        days.append({'date': day.isoformat(), 'rules': applied, 'balances': balances, 'complete': True})
        for b in balances:
            if not b['accepted']:
                warnings.append(f"{day.isoformat()} · {b['name']}: najbliższy wariant {b['actual']} kcal, odchylenie {b['deviation']:+g} kcal; zakres {b['low']}–{b['high']} kcal.")
    return {'entries': entries, 'days': days, 'warnings': warnings,
            'attempts': max_attempts, 'snack_attempts': snack_attempts,
            'start_date': start.isoformat(), 'end_date': end.isoformat()}


def shopping_category(name):
    n = key(name)
    for words, category in [(('mleko', 'jogurt', 'ser', 'jaj', 'maslo', 'twarog'), 'Nabiał i jajka'),
                            (('kurcz', 'indyk', 'losos', 'tunczyk', 'mieso', 'wolow'), 'Mięso i ryby'),
                            (('chleb', 'bulka', 'ryz', 'makaron', 'maka', 'platki', 'kasza', 'cukier'), 'Sypkie i pieczywo'),
                            (('pomidor', 'ogor', 'papry', 'salat', 'trusk', 'banan', 'jabl', 'mango', 'malin', 'szparag', 'dyni'), 'Warzywa i owoce')]:
        if any(w in n for w in words):
            return category
    return 'Inne'


def shopping_items(c, list_id):
    selected = c.execute('SELECT * FROM shopping_lists WHERE id=?', (list_id,)).fetchone()
    if not selected:
        raise ValueError('Nie znaleziono listy zakupów')
    selected = dict(selected)
    ids = [r[0] for r in c.execute('SELECT DISTINCT batch_id FROM family_meals WHERE date>=? AND date<=?',
                                  (selected['start_date'], selected['end_date']))]
    items = {}
    def add(name, qty, unit, category=None, source=None):
        if not name:
            return
        name_key = key(name)
        k = name_key+'|'+key(unit)
        if k not in items:
            items[k] = {'key': k, 'name': name, 'quantity': 0, 'unit': unit,
                        'category': category or shopping_category(name), 'sources': []}
        items[k]['quantity'] += qty
        if source and source not in items[k]['sources']:
            items[k]['sources'].append(source)
    for batch_id in ids:
        batch = c.execute('SELECT snapshot FROM batches WHERE id=?', (batch_id,)).fetchone()
        recipe = unpack(batch[0], {})
        portions = sum(sum(numeric(v) for v in unpack(r[0], {}).values()) for r in c.execute(
            'SELECT portions FROM family_meals WHERE batch_id=?', (batch_id,)))
        factor = portions/max(.01, numeric(recipe.get('servings'), 1))
        for ingredient in recipe.get('ingredients', []):
            if isinstance(ingredient, str):
                add(ingredient, factor, 'porcja', source=recipe.get('name'))
                continue
            qty = numeric(ingredient.get('qty'), 1)*factor
            unit = ingredient.get('unit') or 'szt.'
            each = numeric(ingredient.get('grams_each'))
            if each > 0:
                qty *= each
                unit = 'g'
            if key(unit) in ('kg', 'kilogram'):
                qty *= 1000
                unit = 'g'
            if key(unit) in ('l', 'litr'):
                qty *= 1000
                unit = 'ml'
            form = ingredient.get('form') or 'fresh'
            form_label = {'frozen': 'mrożone', 'canned': 'z puszki', 'jar': 'ze słoika', 'dried': 'suszone'}.get(form)
            name = ingredient.get('name', '')+(f' ({form_label})' if form_label else '')
            add(name, qty, unit, source=recipe.get('name'))
    for manual in c.execute('SELECT * FROM shopping_manual WHERE list_id=?', (list_id,)):
        add(manual['name'], numeric(manual['quantity']), manual['unit'], manual['category'])
    flags = {r['item_key']: dict(r) for r in c.execute('SELECT * FROM shopping_flags WHERE list_id=?', (list_id,))}
    out = []
    for item in items.values():
        item['quantity'] = round(item['quantity'], 2)
        flag = flags.get(item['key'], {})
        item['status'] = flag.get('status', 'needed')
        item['marked_quantity'] = flag.get('marked_quantity', 0)
        item['needs_review'] = item['status'] != 'needed' and item['quantity'] > item['marked_quantity']+.01
        out.append(item)
    out.sort(key=lambda i: (i['category'], key(i['name'])))
    previous = {i['key']: i for i in unpack(selected['snapshot'], [])}
    current = {i['key']: i for i in out}
    diff = {'added': [i for k, i in current.items() if k not in previous],
            'removed': [i for k, i in previous.items() if k not in current],
            'changed': [{'before': previous[k], 'after': i} for k, i in current.items()
                        if k in previous and abs(i['quantity']-previous[k]['quantity']) > .01]}
    return {'list': selected, 'items': out, 'diff': diff,
            'manual': [dict(r) for r in c.execute('SELECT * FROM shopping_manual WHERE list_id=?',(list_id,))]}
