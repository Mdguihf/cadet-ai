import csv, io, json, os, random, sqlite3, string
from datetime import datetime
from pathlib import Path
import streamlit as st

# Load local .env automatically when running Cadet AI on a personal computer.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

st.set_page_config(page_title="Cadet AI 4.0", page_icon="🧠", layout="wide")
BASE=Path(__file__).resolve().parent; DATA=BASE/"data"; DB=DATA/"cadets.db"
COMPONENTS={1:"Обнаружение фактов, аргументов, гипотез и опровержений",2:"Анализ и критика аргументов",3:"Оценка противоречий и альтернатив",4:"Формулировка вывода"}
SKILLS={
"Различение факта, аргумента, гипотезы и опровержения":"отличать установленное утверждение от предположения, довода и опровержения",
"Распознавание аргумента":"видеть, какое утверждение реально поддерживает вывод",
"Классификация утверждения":"определять роль отдельной фразы в рассуждении",
"Критика недостаточного основания":"проверять, достаточно ли данных для вывода",
"Оценка силы критики":"сравнивать возражения по релевантности и силе",
"Оценка причинного аргумента":"проверять, следует ли причинный вывод из приведённых данных",
"Поиск релевантного аргумента":"отбирать довод, непосредственно связанный с тезисом",
"Поиск альтернативного объяснения":"искать другие правдоподобные причины наблюдаемого результата",
"Многофакторное объяснение":"учитывать несколько факторов вместо одной причины",
"Разведение причин и следствий":"не смешивать одновременно наблюдаемые явления с их причинами",
"Оценка альтернативного объяснения":"сравнивать конкурирующие объяснения",
"Определение границ вывода":"не делать вывод шире, чем позволяют данные",
"Осторожный причинный вывод":"отделять возможность причинной связи от доказательства причины",
"Согласование правил и ценностей":"разрешать конфликт требований без ложной дилеммы",
}

def now(): return datetime.now().isoformat(timespec="seconds")
def db():
    DATA.mkdir(exist_ok=True); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS cadets(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, class_name TEXT DEFAULT '', created_at TEXT NOT NULL, last_login TEXT);
    CREATE TABLE IF NOT EXISTS diagnostics(id INTEGER PRIMARY KEY AUTOINCREMENT,cadet_id INTEGER NOT NULL,created_at TEXT NOT NULL,total INTEGER NOT NULL,scores_json TEXT NOT NULL,mistakes_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS diagnostic_answers(id INTEGER PRIMARY KEY AUTOINCREMENT,diagnostic_id INTEGER NOT NULL,task_id TEXT NOT NULL,selected TEXT NOT NULL,correct INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS training_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,cadet_id INTEGER NOT NULL,created_at TEXT NOT NULL,score INTEGER NOT NULL,total INTEGER NOT NULL,details_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS task_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,cadet_id INTEGER NOT NULL,task_id TEXT NOT NULL,component INTEGER NOT NULL,skill TEXT NOT NULL,correct INTEGER NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ai_dialogues(id INTEGER PRIMARY KEY AUTOINCREMENT,cadet_id INTEGER NOT NULL,created_at TEXT NOT NULL,user_message TEXT NOT NULL,ai_message TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ai_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,cadet_id INTEGER NOT NULL,created_at TEXT NOT NULL,source_task_id TEXT,task_json TEXT NOT NULL);
    '''); c.commit(); c.close()
init_db()

DIAG=json.loads((DATA/"diagnostic_tasks.json").read_text(encoding="utf-8")); TASKS=json.loads((DATA/"training_tasks.json").read_text(encoding="utf-8"))

def generate_code():
    c=db()
    while True:
        x="CD-"+"".join(random.choices(string.digits,k=6))
        if not c.execute("SELECT 1 FROM cadets WHERE code=?",(x,)).fetchone(): c.close(); return x

def create_cadet(name,cls):
    code=generate_code(); c=db(); cur=c.execute("INSERT INTO cadets(code,name,class_name,created_at,last_login) VALUES(?,?,?,?,?)",(code,name.strip(),cls.strip(),now(),now())); c.commit(); cid=cur.lastrowid; c.close(); return cid

def get_cadet(cid):
    c=db(); r=c.execute("SELECT * FROM cadets WHERE id=?",(cid,)).fetchone(); c.close(); return r

def login(code):
    c=db(); r=c.execute("SELECT * FROM cadets WHERE code=?",(code.strip().upper(),)).fetchone()
    if r: c.execute("UPDATE cadets SET last_login=? WHERE id=?",(now(),r['id'])); c.commit()
    c.close(); return r

def save_diag(cid,answers):
    scores={i:0 for i in range(1,5)}; mistakes=[]
    for t in DIAG:
        ok=answers.get(t['id'])==t['answer']; scores[t['component']]+=int(ok)
        if not ok: mistakes.append(t['id'])
    c=db(); cur=c.execute("INSERT INTO diagnostics(cadet_id,created_at,total,scores_json,mistakes_json) VALUES(?,?,?,?,?)",(cid,now(),sum(scores.values()),json.dumps(scores),json.dumps(mistakes))); did=cur.lastrowid
    for t in DIAG: c.execute("INSERT INTO diagnostic_answers(diagnostic_id,task_id,selected,correct) VALUES(?,?,?,?)",(did,t['id'],str(answers.get(t['id'])),int(answers.get(t['id'])==t['answer'])))
    c.commit(); c.close(); return scores,mistakes

def latest_diag(cid):
    c=db(); r=c.execute("SELECT * FROM diagnostics WHERE cadet_id=? ORDER BY id DESC LIMIT 1",(cid,)).fetchone(); c.close(); return r

def save_session(cid,details):
    c=db(); score=sum(int(x['correct']) for x in details); c.execute("INSERT INTO training_sessions(cadet_id,created_at,score,total,details_json) VALUES(?,?,?,?,?)",(cid,now(),score,len(details),json.dumps(details,ensure_ascii=False)))
    for x in details: c.execute("INSERT INTO task_attempts(cadet_id,task_id,component,skill,correct,created_at) VALUES(?,?,?,?,?,?)",(cid,x['task_id'],x['component'],x['skill'],int(x['correct']),now()))
    c.commit(); c.close()

def attempts(cid):
    c=db(); r=c.execute("SELECT * FROM task_attempts WHERE cadet_id=? ORDER BY id DESC LIMIT 100",(cid,)).fetchall(); c.close(); return r

def sessions(cid):
    c=db(); r=c.execute("SELECT * FROM training_sessions WHERE cadet_id=? ORDER BY id DESC",(cid,)).fetchall(); c.close(); return r

def ai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        try:
            api_key = str(st.secrets.get("OPENAI_API_KEY", "")).strip()
        except Exception:
            api_key = ""

    if not api_key:
        return None

    return OpenAI(api_key=api_key)

def ai_call(system, user):
    cl = ai_client()
    if not cl:
        return None

    model = os.getenv("OPENAI_MODEL", "").strip()

    if not model:
        try:
            model = str(st.secrets.get("OPENAI_MODEL", "gpt-4o-mini")).strip()
        except Exception:
            model = "gpt-4o-mini"

    try:
        response = cl.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ]
        )
        return response.output_text.strip()
    except Exception as e:
        return f"ОШИБКА AI: {e}"
def plan_for(cid):
    d=latest_diag(cid)
    if not d: return []
    scores={int(k):v for k,v in json.loads(d['scores_json']).items()}; miss=set(json.loads(d['mistakes_json']))
    # No profiles and no visible levels: prioritize individual skills by current evidence.
    priorities=[]
    for t in DIAG:
        if t['id'] in miss: priorities.append((t['skill'],t['component'],2))
    seen=[]
    for skill,comp,_ in priorities:
        if skill not in seen: seen.append(skill)
    if not seen:
        seen=[DIAG[0]['skill'],DIAG[4]['skill'],DIAG[8]['skill'],DIAG[12]['skill']]
    return [{'skill':s,'component':next(t['component'] for t in DIAG if t['skill']==s),'reason':'есть ошибка во входной диагностике'} for s in seen[:5]]

def choose_tasks(cid,n=8):
    plan=plan_for(cid); hist=attempts(cid)
    byskill={}
    for r in hist:
        byskill.setdefault(r['skill'],[]).append(r['correct'])
    candidates=[]
    for t in TASKS:
        p=next((x for x in plan if x['skill']==t['skill']),None)
        score=0
        if p: score+=100
        if byskill.get(t['skill']):
            acc=sum(byskill[t['skill']])/len(byskill[t['skill']]); score += (1-acc)*30
        score += random.random()*8
        candidates.append((score,t))
    candidates.sort(key=lambda x:x[0],reverse=True)
    chosen=[]; used=set()
    for _,t in candidates:
        if t['id'] not in used: chosen.append(t); used.add(t['id'])
        if len(chosen)>=n: break
    return chosen

def ai_explain(t,selected):
    system='Ты образовательный наставник по критическому мышлению. Не оценивай личность и не ставь диагнозов. Объясни логику конкретного задания кратко.'
    user=f"Задание: {t['question']}\nВарианты: {t['options']}\nОтвет кадета: {selected}\nПравильный вариант: {t['options'][t['answer']]}\nДай 4 пункта: где ошибка; почему верный ответ сильнее; вопрос для самопроверки; микро-совет."
    return ai_call(system,user)

def ai_new_task(t,selected):
    system='Создай новое учебное задание по критическому мышлению на тот же навык, но в другой ситуации. Верни только JSON с полями title,question,options,answer,explanation,component,skill. 4 варианта, answer 0-3.'
    user=f"Навык: {t['skill']}\nКомпонент: {COMPONENTS[t['component']]}\nИсходное задание: {t['question']}\nОтвет кадета: {selected}"
    raw=ai_call(system,user)
    if not raw: return None
    try:
        raw=raw.replace('```json','').replace('```','').strip(); x=json.loads(raw)
        if len(x.get('options',[]))!=4 or not 0<=int(x.get('answer',-1))<4: return None
        x['component']=int(x.get('component',t['component'])); x['skill']=x.get('skill',t['skill']); return x
    except Exception: return None

def current_chat(cid):
    c=db(); r=c.execute("SELECT user_message,ai_message FROM ai_dialogues WHERE cadet_id=? ORDER BY id DESC LIMIT 12",(cid,)).fetchall(); c.close(); return list(reversed(r))

def csv_export():
    c=db(); rows=c.execute('''SELECT ta.created_at,c.code,ta.task_id,ta.component,ta.skill,ta.correct FROM task_attempts ta JOIN cadets c ON c.id=ta.cadet_id ORDER BY ta.created_at''').fetchall(); c.close()
    out=io.StringIO(); w=csv.writer(out); w.writerow(['created_at','cadet_code','task_id','component','skill','correct']); w.writerows(rows); return out.getvalue()

def go(p): st.session_state.page=p; st.rerun()
for k,v in {'page':'home','cadet_id':None,'diag_answers':{},'training':[],'ti':0,'details':[],'finished':False,'chat':[]}.items(): st.session_state.setdefault(k,v)

with st.sidebar:
    st.title('🧠 Cadet AI 4.0')
    if st.session_state.cadet_id:
        c=get_cadet(st.session_state.cadet_id); st.success(f"{c['name']}\n\nКод: **{c['code']}**")
        for label,p in [('Диагностика','diag'),('Моя карта навыков','map'),('Индивидуальный план','plan'),('Тренировка','train'),('AI-наставник','coach'),('История','history'),('Педагог / исследователь','teacher')]:
            if st.button(label,use_container_width=True): go(p)
        if st.button('Выйти',use_container_width=True): st.session_state.cadet_id=None; go('home')
    else: st.info('Войдите по коду или создайте кабинет.')

st.title('🧠 Cadet AI 4.0')
st.caption('Фиксированная диагностика → индивидуальная карта навыков → персональный план → адаптивная тренировка → AI-наставник')

if st.session_state.page=='home':
    a,b=st.tabs(['Вход по коду','Новый кабинет'])
    with a:
        code=st.text_input('Код кадета',placeholder='CD-123456').upper()
        if st.button('Войти',type='primary'):
            c=login(code)
            if c: st.session_state.cadet_id=c['id']; go('map')
            else: st.error('Код не найден.')
    with b:
        name=st.text_input('Имя / идентификатор'); cls=st.text_input('Класс / взвод')
        if st.button('Создать кабинет',type='primary'):
            if name.strip():
                cid=create_cadet(name,cls); st.session_state.cadet_id=cid; st.success(f"Ваш код: **{get_cadet(cid)['code']}**"); go('diag')
            else: st.error('Введите имя или учебный идентификатор.')

elif not st.session_state.cadet_id:
    st.warning('Сначала войдите в кабинет.')
    if st.button('На вход'): go('home')

elif st.session_state.page=='diag':
    st.header('Входная диагностика')
    st.info('16 заданий из исследовательской работы. Формулировки и варианты ответов перенесены без изменений.')
    for i,t in enumerate(DIAG,1):
        st.subheader(f"{i}. {t['title']}"); st.write(t['question'])
        st.session_state.diag_answers[t['id']]=st.radio('Выберите один ответ',t['options'],index=None,key='diag_'+t['id'])
    if st.button('Завершить диагностику',type='primary'):
        if any(v is None for v in st.session_state.diag_answers.values()): st.error('Ответьте на все 16 заданий.')
        else:
            save_diag(st.session_state.cadet_id,st.session_state.diag_answers); st.session_state.diag_answers={}; go('map')

elif st.session_state.page=='map':
    st.header('Моя карта навыков')
    d=latest_diag(st.session_state.cadet_id)
    if not d:
        st.info('Сначала пройдите входную диагностику.');
        if st.button('Начать диагностику',type='primary'): go('diag')
    else:
        scores={int(k):v for k,v in json.loads(d['scores_json']).items()}; miss=set(json.loads(d['mistakes_json']))
        st.write(f"Дата диагностики: **{d['created_at']}** · результат **{d['total']}/16**")
        cols=st.columns(4)
        for i,c in enumerate(range(1,5)):
            with cols[i]: st.metric(f'Компонент {c}',f"{scores[c]}/4")
        st.subheader('Навыки, которые стоит потренировать')
        if miss:
            for t in DIAG:
                if t['id'] in miss: st.write('•',t['skill'])
        else: st.success('Во входной диагностике ошибок нет. Можно переходить к расширению и применению навыков.')
        st.caption('Здесь нет профилей и ярлыков: карта показывает конкретные умения, а не «тип» кадета.')

elif st.session_state.page=='plan':
    st.header('Индивидуальный план')
    d=latest_diag(st.session_state.cadet_id)
    if not d: st.info('Пройдите диагностику.');
    else:
        plan=plan_for(st.session_state.cadet_id)
        if not plan: st.success('План поддержки не требуется — можно тренировать более широкий набор навыков.')
        else:
            for i,p in enumerate(plan,1):
                st.markdown(f"**Шаг {i}. {p['skill']}**")
                st.caption(COMPONENTS[p['component']]); st.write(SKILLS.get(p['skill'],'Тренировка навыка по конкретным заданиям.'))
            if st.button('Сформировать тренировку по моему плану',type='primary'):
                st.session_state.training=choose_tasks(st.session_state.cadet_id,8); st.session_state.ti=0; st.session_state.details=[]; st.session_state.finished=False; go('train')

elif st.session_state.page=='train':
    st.header('Индивидуальная тренировка')
    if not st.session_state.training:
        if st.button('Сформировать задания',type='primary'):
            st.session_state.training=choose_tasks(st.session_state.cadet_id,8); st.session_state.ti=0; st.session_state.details=[]; st.session_state.finished=False; st.rerun()
        else: st.info('Задания будут подобраны по вашей карте навыков и предыдущим попыткам.')
    elif st.session_state.finished:
        score=sum(x['correct'] for x in st.session_state.details); st.success(f"Сессия завершена: {score}/{len(st.session_state.details)}")
        for x in st.session_state.details:
            with st.expander(('✅ ' if x['correct'] else '❌ ')+x['title']):
                st.write(f"Навык: **{x['skill']}**"); st.write(f"Ваш ответ: {x['selected']}"); st.write(f"Правильный вариант: {x['correct_answer']}")
                if x.get('ai_explanation'): st.write(x['ai_explanation'])
                else: st.write(x['explanation'])
        if st.button('Новый индивидуальный план',type='primary'): st.session_state.training=[]; go('plan')
    else:
        i=st.session_state.ti
        if i>=len(st.session_state.training):
            save_session(st.session_state.cadet_id,st.session_state.details); st.session_state.finished=True; st.rerun()
        t=st.session_state.training[i]; st.progress(i/len(st.session_state.training)); st.caption(f"Задание {i+1} из {len(st.session_state.training)} · {t['skill']}")
        st.subheader(t['title']); st.write(t['question']); ans=st.radio('Ответ',t['options'],index=None,key=f"tr_{i}_{t['id']}")
        if st.button('Проверить',type='primary'):
            if ans is None: st.error('Выберите ответ.'); st.stop()
            correct=ans==t['options'][t['answer']]
            x={'task_id':t['id'],'title':t['title'],'component':t['component'],'skill':t['skill'],'correct':correct,'selected':ans,'correct_answer':t['options'][t['answer']],'explanation':t['explanation']}
            if not correct:
                x['ai_explanation']=ai_explain(t,ans)
                nt=ai_new_task(t,ans)
                if nt:
                    c=db(); c.execute('INSERT INTO ai_tasks(cadet_id,created_at,source_task_id,task_json) VALUES(?,?,?,?)',(st.session_state.cadet_id,now(),t['id'],json.dumps(nt,ensure_ascii=False))); c.commit(); c.close(); x['ai_task']=nt
            st.session_state.details.append(x); st.session_state.ti+=1; st.rerun()

elif st.session_state.page=='coach':
    st.header('AI-наставник')
    if not ai_client(): st.warning('AI не подключён. Добавьте OPENAI_API_KEY и при необходимости OPENAI_MODEL в окружение.')
    for m in st.session_state.chat:
        with st.chat_message(m['role']): st.write(m['content'])
    msg=st.chat_input('Например: помоги понять, где я поспешил с выводом')
    if msg:
        st.session_state.chat.append({'role':'user','content':msg})
        plan=plan_for(st.session_state.cadet_id); hist=attempts(st.session_state.cadet_id)
        context=f"Индивидуальный план: {plan}\nПоследние попытки: {[dict(x) for x in hist[:12]]}"
        answer=ai_call('Ты персональный образовательный наставник по критическому мышлению кадет. Используй сократические вопросы. Не ставь диагнозов и не оценивай личность. Помогай проверять основания, альтернативы и границы выводов.',context+'\nСообщение кадета: '+msg) or 'AI пока недоступен. Проверьте подключение API.'
        st.session_state.chat.append({'role':'assistant','content':answer}); c=db(); c.execute('INSERT INTO ai_dialogues(cadet_id,created_at,user_message,ai_message) VALUES(?,?,?,?)',(st.session_state.cadet_id,now(),msg,answer)); c.commit(); c.close(); st.rerun()

elif st.session_state.page=='history':
    st.header('История прогресса')
    rows=sessions(st.session_state.cadet_id)
    if not rows: st.info('Тренировок пока нет.')
    for r in rows:
        with st.expander(f"{r['created_at']} · {r['score']}/{r['total']}"):
            for x in json.loads(r['details_json']): st.write(('✅' if x['correct'] else '❌'),x['skill'])

elif st.session_state.page=='teacher':
    st.header('Педагог / исследователь')
    c=db(); cad=c.execute('SELECT COUNT(*) n FROM cadets').fetchone()['n']; att=c.execute('SELECT COUNT(*) n FROM task_attempts').fetchone()['n']; di=c.execute('SELECT COUNT(*) n FROM diagnostics').fetchone()['n']; c.close()
    a,b,d=st.columns(3); a.metric('Кадетов',cad); b.metric('Диагностик',di); d.metric('Попыток в тренировках',att)
    st.subheader('Сводка по навыкам')
    c=db(); rows=c.execute('SELECT skill,COUNT(*) n,AVG(correct)*100 acc FROM task_attempts GROUP BY skill ORDER BY acc').fetchall(); c.close()
    for r in rows: st.write(f"**{r['skill']}** — {r['n']} попыток, точность {r['acc']:.1f}%")
    st.download_button('Скачать CSV обезличенной статистики',csv_export(),'cadet_ai_progress.csv','text/csv')
    st.caption('В экспорт не включаются имена кадетов; используется код кадета. Перед реальным внедрением настройте хранение и доступ с учётом требований вашей организации.')

st.divider(); st.caption('Cadet AI 4.0 · диагностика фиксирована; индивидуальный маршрут строится по навыкам и истории попыток. AI используется для обучения, объяснений и генерации дополнительных задач, а не для изменения диагностического результата.')
