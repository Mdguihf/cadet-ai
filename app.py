import csv
import io
import json
import os
import random
import string
from datetime import datetime
from pathlib import Path

import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Критическое мышление кадет", page_icon="🧠", layout="wide")

APP_TITLE = "Интеллектуальная система персонализированной диагностики и развития критического мышления кадет"
BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
COMPONENTS = {
    1: "Обнаружение фактов, аргументов, гипотез и опровержений",
    2: "Анализ и критика аргументов",
    3: "Оценка противоречий и альтернатив",
    4: "Формулировка вывода",
}
SKILLS = {
    "Различение факта, аргумента, гипотезы и опровержения": "отличать установленное утверждение от предположения, довода и опровержения",
    "Распознавание аргумента": "видеть, какое утверждение реально поддерживает вывод",
    "Классификация утверждения": "определять роль отдельной фразы в рассуждении",
    "Критика недостаточного основания": "проверять, достаточно ли данных для вывода",
    "Оценка силы критики": "сравнивать возражения по релевантности и силе",
    "Оценка причинного аргумента": "проверять, следует ли причинный вывод из приведённых данных",
    "Поиск релевантного аргумента": "отбирать довод, непосредственно связанный с тезисом",
    "Поиск альтернативного объяснения": "искать другие правдоподобные причины наблюдаемого результата",
    "Многофакторное объяснение": "учитывать несколько факторов вместо одной причины",
    "Разведение причин и следствий": "не смешивать одновременно наблюдаемые явления с их причинами",
    "Оценка альтернативного объяснения": "сравнивать конкурирующие объяснения",
    "Определение границ вывода": "не делать вывод шире, чем позволяют данные",
    "Осторожный причинный вывод": "отделять возможность причинной связи от доказательства причины",
    "Согласование правил и ценностей": "разрешать конфликт требований без ложной дилеммы",
}


def now():
    return datetime.now().isoformat(timespec="seconds")


@st.cache_resource(show_spinner=False)
def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
    key = (
        os.getenv("SUPABASE_SECRET_KEY")
        or st.secrets.get("SUPABASE_SECRET_KEY", "")
        or os.getenv("SUPABASE_KEY")
        or st.secrets.get("SUPABASE_KEY", "")
    )
    if not url or not key:
        raise RuntimeError(
            "Не настроены SUPABASE_URL и SUPABASE_SECRET_KEY. "
            "Добавьте их в Streamlit Secrets."
        )
    return create_client(url, key)


try:
    sb = get_supabase()
    DB_ERROR = None
except Exception as exc:
    sb = None
    DB_ERROR = str(exc)

DIAG = json.loads((DATA / "diagnostic_tasks.json").read_text(encoding="utf-8"))
TASKS = json.loads((DATA / "training_tasks.json").read_text(encoding="utf-8"))


def rows(result):
    return result.data or []


def generate_code():
    for _ in range(100):
        code = "CD-" + "".join(random.choices(string.digits, k=6))
        found = sb.table("cadets").select("id").eq("code", code).limit(1).execute()
        if not rows(found):
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный код кадета.")


def create_cadet(name, cls):
    code = generate_code()
    result = sb.table("cadets").insert({
        "code": code,
        "name": name.strip(),
        "class_name": cls.strip(),
        "created_at": now(),
        "last_login": now(),
    }).execute()
    data = rows(result)
    if not data:
        raise RuntimeError("Supabase не вернул созданный кабинет.")
    return data[0]["id"]


def get_cadet(cid):
    result = sb.table("cadets").select("*").eq("id", cid).limit(1).execute()
    data = rows(result)
    return data[0] if data else None


def login(code):
    result = sb.table("cadets").select("*").eq("code", code.strip().upper()).limit(1).execute()
    data = rows(result)
    if not data:
        return None
    cadet = data[0]
    sb.table("cadets").update({"last_login": now()}).eq("id", cadet["id"]).execute()
    cadet["last_login"] = now()
    return cadet


def save_diag(cid, answers):
    scores = {i: 0 for i in range(1, 5)}
    mistakes = []
    answer_rows = []
    for t in DIAG:
        selected = answers.get(t["id"])
        correct_answer = t["options"][t["answer"]]
        ok = selected == correct_answer
        scores[t["component"]] += int(ok)
        if not ok:
            mistakes.append(t["id"])
        answer_rows.append({
            "task_id": t["id"],
            "selected": str(selected),
            "correct": bool(ok),
        })

    result = sb.table("diagnostics").insert({
        "cadet_id": cid,
        "created_at": now(),
        "total": sum(scores.values()),
        "scores_json": scores,
        "mistakes_json": mistakes,
    }).execute()
    data = rows(result)
    if not data:
        raise RuntimeError("Не удалось сохранить диагностику.")
    did = data[0]["id"]
    for item in answer_rows:
        item["diagnostic_id"] = did
    sb.table("diagnostic_answers").insert(answer_rows).execute()
    return scores, mistakes


def latest_diag(cid):
    result = (
        sb.table("diagnostics")
        .select("*")
        .eq("cadet_id", cid)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    data = rows(result)
    return data[0] if data else None


def save_session(cid, details):
    score = sum(int(x["correct"]) for x in details)
    result = sb.table("training_sessions").insert({
        "cadet_id": cid,
        "created_at": now(),
        "score": score,
        "total": len(details),
        "details_json": details,
    }).execute()
    data = rows(result)
    if not data:
        raise RuntimeError("Не удалось сохранить тренировочную сессию.")

    attempt_rows = []
    stamp = now()
    for x in details:
        attempt_rows.append({
            "cadet_id": cid,
            "task_id": x["task_id"],
            "component": x["component"],
            "skill": x["skill"],
            "correct": bool(x["correct"]),
            "created_at": stamp,
        })
    if attempt_rows:
        sb.table("task_attempts").insert(attempt_rows).execute()


def attempts(cid):
    result = (
        sb.table("task_attempts")
        .select("*")
        .eq("cadet_id", cid)
        .order("id", desc=True)
        .limit(100)
        .execute()
    )
    return rows(result)


def sessions(cid):
    result = (
        sb.table("training_sessions")
        .select("*")
        .eq("cadet_id", cid)
        .order("id", desc=True)
        .execute()
    )
    return rows(result)


def current_chat(cid):
    result = (
        sb.table("ai_dialogues")
        .select("user_message,ai_message")
        .eq("cadet_id", cid)
        .order("id", desc=True)
        .limit(12)
        .execute()
    )
    return list(reversed(rows(result)))


def save_chat(cid, message, answer):
    sb.table("ai_dialogues").insert({
        "cadet_id": cid,
        "created_at": now(),
        "user_message": message,
        "ai_message": answer,
    }).execute()


def save_generated_task(cid, source_task_id, task):
    sb.table("ai_tasks").insert({
        "cadet_id": cid,
        "created_at": now(),
        "source_task_id": source_task_id,
        "task_json": task,
    }).execute()


def csv_export():
    result = (
        sb.table("task_attempts")
        .select("created_at,cadet_id,task_id,component,skill,correct")
        .order("created_at")
        .execute()
    )
    attempts_data = rows(result)
    cadet_ids = sorted({x["cadet_id"] for x in attempts_data})
    code_map = {}
    for cid in cadet_ids:
        c = get_cadet(cid)
        if c:
            code_map[cid] = c["code"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["created_at", "cadet_code", "task_id", "component", "skill", "correct"])
    for x in attempts_data:
        w.writerow([
            x.get("created_at", ""), code_map.get(x.get("cadet_id"), ""),
            x.get("task_id", ""), x.get("component", ""), x.get("skill", ""), x.get("correct", False)
        ])
    return out.getvalue()


def offline_explain(t, selected):
    correct = t["options"][t["answer"]]
    skill = t.get("skill", "")
    explanation = t.get("explanation", "")
    prompts = {
        "Поиск альтернативного объяснения": "Какие ещё причины могли привести к такому результату? Какие данные отличили бы эти версии?",
        "Многофакторное объяснение": "Какие факторы могли действовать одновременно? Что произойдёт, если убрать один из них?",
        "Разведение причин и следствий": "Что здесь наблюдается, а что именно объявляется причиной? Достаточно ли данных для такого вывода?",
        "Оценка альтернативного объяснения": "Какая из версий лучше объясняет все приведённые факты? Какие данные помогли бы сравнить версии?",
        "Критика недостаточного основания": "Каких данных не хватает, чтобы вывод стал надёжнее?",
        "Оценка силы критики": "Отвечает ли возражение непосредственно на исходный тезис и меняет ли оно его обоснованность?",
        "Оценка причинного аргумента": "Есть ли здесь только совпадение во времени или действительно приведены основания для причинного вывода?",
        "Определение границ вывода": "Не шире ли вывод, чем позволяют приведённые факты?",
        "Осторожный причинный вывод": "Можно ли из этих данных говорить о возможности причины, или уже утверждается доказанная причина?",
    }
    q = prompts.get(skill, "Какая часть условия подтверждает выбранный ответ? Какой факт мог бы опровергнуть твой вывод?")
    return (
        f"**Где ошибка:** выбран вариант «{selected}», а правильный вариант — «{correct}».\n\n"
        f"**Почему:** {explanation}\n\n"
        f"**Самопроверка:** {q}\n\n"
        "**Микро-совет:** сначала отдели факты от предположений, затем проверь, действительно ли доводы поддерживают вывод."
    )


def offline_chat(message, cid):
    msg = message.lower().strip()
    plan = plan_for(cid)
    hist = attempts(cid)
    weak = []
    for r in hist:
        if not r["correct"] and r["skill"] not in weak:
            weak.append(r["skill"])
    focus = [p["skill"] for p in plan]
    if any(w in msg for w in ("ошиб", "неправ", "почему", "где")):
        if weak:
            return (
                f"Давай разберём спокойно. В последних попытках встречались ошибки по навыку **{weak[0]}**. "
                "Назови свой вывод в одном предложении. Затем спроси себя: какие факты его подтверждают и какое альтернативное объяснение возможно?"
            )
        return "Давай начнём с основания вывода: какой факт в условии ты считаешь главным и почему он действительно поддерживает твой ответ?"
    if any(w in msg for w in ("альтернатив", "друг", "причин")):
        return "Попробуй построить минимум две версии объяснения. Для каждой укажи один факт, который её поддерживает, и один факт, который мог бы её ослабить."
    if any(w in msg for w in ("аргумент", "довод", "доказ")):
        return "Проверь аргумент по цепочке: тезис → довод → связь между ними. Спроси себя: если довод верен, следует ли из него тезис?"
    if any(w in msg for w in ("факт", "гипотез", "мнение")):
        return "Раздели утверждения на факт, аргумент, гипотезу или мнение. Для гипотезы отдельно сформулируй, какие данные могли бы её подтвердить или опровергнуть."
    if any(w in msg for w in ("манипуля", "фейк", "достовер")):
        return "Не спеши принимать сообщение на веру. Проверь источник, отдели проверяемые факты от оценок и поищи независимое подтверждение."
    if focus:
        return f"Сейчас в твоём индивидуальном плане есть навык **{focus[0]}**. Попробуй ответить: какие данные здесь являются фактами, а какие уже интерпретацией?"
    return "Сформулируй свой вывод. Затем назови два основания в его пользу и одну альтернативную версию объяснения. Я помогу проверить ход рассуждения."


def offline_new_task(t, cid):
    used = {r["task_id"] for r in attempts(cid)}
    same = [x for x in TASKS if x.get("skill") == t.get("skill") and x.get("id") != t.get("id") and x.get("id") not in used]
    if not same:
        same = [x for x in TASKS if x.get("skill") == t.get("skill") and x.get("id") != t.get("id")]
    return random.choice(same) if same else None


def plan_for(cid):
    d = latest_diag(cid)
    if not d:
        return []
    scores = {int(k): v for k, v in (d.get("scores_json") or {}).items()}
    miss = set(d.get("mistakes_json") or [])
    priorities = []
    for t in DIAG:
        if t["id"] in miss:
            priorities.append((t["skill"], t["component"]))
    seen = []
    for skill, comp in priorities:
        if skill not in seen:
            seen.append(skill)
    if not seen:
        seen = [DIAG[0]["skill"], DIAG[4]["skill"], DIAG[8]["skill"], DIAG[12]["skill"]]
    return [
        {"skill": s, "component": next(t["component"] for t in DIAG if t["skill"] == s), "reason": "есть ошибка во входной диагностике"}
        for s in seen[:5]
    ]


def choose_tasks(cid, n=8):
    plan = plan_for(cid)
    hist = attempts(cid)
    byskill = {}
    for r in hist:
        byskill.setdefault(r["skill"], []).append(bool(r["correct"]))
    candidates = []
    for t in TASKS:
        p = next((x for x in plan if x["skill"] == t["skill"]), None)
        score = 0
        if p:
            score += 100
        if byskill.get(t["skill"]):
            acc = sum(byskill[t["skill"]]) / len(byskill[t["skill"]])
            score += (1 - acc) * 30
        score += random.random() * 8
        candidates.append((score, t))
    candidates.sort(key=lambda x: x[0], reverse=True)
    chosen, used = [], set()
    for _, t in candidates:
        if t["id"] not in used:
            chosen.append(t)
            used.add(t["id"])
        if len(chosen) >= n:
            break
    return chosen


def go(page):
    st.session_state.page = page
    st.rerun()


for key, value in {
    "page": "home",
    "cadet_id": None,
    "diag_answers": {},
    "training": [],
    "ti": 0,
    "details": [],
    "finished": False,
}.items():
    st.session_state.setdefault(key, value)

if DB_ERROR:
    st.error("Система ещё не подключена к Supabase.")
    st.code("SUPABASE_URL=...\nSUPABASE_SECRET_KEY=...", language="text")
    st.info("Добавьте эти два секрета в Streamlit Cloud → Settings → Secrets, затем перезапустите приложение.")
    st.stop()

with st.sidebar:
    st.title("🧠 Критическое мышление кадет")
    if st.session_state.cadet_id:
        cadet = get_cadet(st.session_state.cadet_id)
        if cadet:
            st.success(f"{cadet['name']}\n\nКод: **{cadet['code']}**")
            for label, page in [
                ("Диагностика", "diag"),
                ("Моя карта навыков", "map"),
                ("Индивидуальный план", "plan"),
                ("Тренировка", "train"),
                ("AI-наставник", "coach"),
                ("История", "history"),
                ("Педагог / исследователь", "teacher"),
            ]:
                if st.button(label, use_container_width=True):
                    go(page)
            if st.button("Выйти", use_container_width=True):
                st.session_state.cadet_id = None
                go("home")
        else:
            st.session_state.cadet_id = None
            st.warning("Кабинет не найден в облачной базе.")
    else:
        st.info("Войдите по коду или создайте кабинет.")

st.title("🧠 Интеллектуальная система персонализированной диагностики и развития критического мышления кадет")
st.caption("Фиксированная диагностика → индивидуальная карта навыков → персональный план → адаптивная тренировка → автономный наставник")

if st.session_state.page == "home":
    a, b = st.tabs(["Вход по коду", "Новый кабинет"])
    with a:
        code = st.text_input("Код кадета", placeholder="CD-123456").upper()
        if st.button("Войти", type="primary"):
            cadet = login(code)
            if cadet:
                st.session_state.cadet_id = cadet["id"]
                st.session_state.chat = current_chat(cadet["id"])
                go("map")
            else:
                st.error("Код не найден в облачной базе.")
    with b:
        name = st.text_input("Имя / идентификатор")
        cls = st.text_input("Класс / взвод")
        if st.button("Создать кабинет", type="primary"):
            if name.strip():
                try:
                    cid = create_cadet(name, cls)
                    st.session_state.cadet_id = cid
                    st.session_state.chat = []
                    st.success(f"Ваш код: **{get_cadet(cid)['code']}**")
                    go("diag")
                except Exception as exc:
                    st.error(f"Не удалось создать кабинет: {exc}")
            else:
                st.error("Введите имя или учебный идентификатор.")

elif not st.session_state.cadet_id:
    st.warning("Сначала войдите в кабинет.")
    if st.button("На вход"):
        go("home")

elif st.session_state.page == "diag":
    st.header("Входная диагностика")
    st.info("16 заданий из исследовательской работы. Формулировки и варианты ответов перенесены без изменений.")
    for i, t in enumerate(DIAG, 1):
        st.subheader(f"{i}. {t['title']}")
        st.write(t["question"])
        st.session_state.diag_answers[t["id"]] = st.radio(
            "Выберите один ответ",
            t["options"],
            index=None,
            key="diag_" + t["id"],
        )
    if st.button("Завершить диагностику", type="primary"):
        if len(st.session_state.diag_answers) != len(DIAG) or any(v is None for v in st.session_state.diag_answers.values()):
            st.error("Ответьте на все 16 заданий.")
        else:
            try:
                save_diag(st.session_state.cadet_id, st.session_state.diag_answers)
                st.session_state.diag_answers = {}
                go("map")
            except Exception as exc:
                st.error(f"Не удалось сохранить диагностику: {exc}")

elif st.session_state.page == "map":
    st.header("Моя карта навыков")
    d = latest_diag(st.session_state.cadet_id)
    if not d:
        st.info("Сначала пройдите входную диагностику.")
        if st.button("Начать диагностику", type="primary"):
            go("diag")
    else:
        scores = {int(k): v for k, v in (d.get("scores_json") or {}).items()}
        miss = set(d.get("mistakes_json") or [])
        st.write(f"Дата диагностики: **{d['created_at']}** · результат **{d['total']}/16**")
        cols = st.columns(4)
        for i, comp in enumerate(range(1, 5)):
            with cols[i]:
                st.metric(f"Компонент {comp}", f"{scores.get(comp, 0)}/4")
        st.subheader("Навыки, которые стоит потренировать")
        if miss:
            for t in DIAG:
                if t["id"] in miss:
                    st.write("•", t["skill"])
        else:
            st.success("Во входной диагностике ошибок нет. Можно переходить к расширению и применению навыков.")
        st.caption("Здесь нет профилей и ярлыков: карта показывает конкретные умения, а не «тип» кадета.")

elif st.session_state.page == "plan":
    st.header("Индивидуальный план")
    d = latest_diag(st.session_state.cadet_id)
    if not d:
        st.info("Пройдите диагностику.")
    else:
        plan = plan_for(st.session_state.cadet_id)
        if not plan:
            st.success("План поддержки не требуется — можно тренировать более широкий набор навыков.")
        else:
            for i, p in enumerate(plan, 1):
                st.markdown(f"**Шаг {i}. {p['skill']}**")
                st.caption(COMPONENTS[p["component"]])
                st.write(SKILLS.get(p["skill"], "Тренировка навыка по конкретным заданиям."))
            if st.button("Сформировать тренировку по моему плану", type="primary"):
                st.session_state.training = choose_tasks(st.session_state.cadet_id, 8)
                st.session_state.ti = 0
                st.session_state.details = []
                st.session_state.finished = False
                go("train")

elif st.session_state.page == "train":
    st.header("Индивидуальная тренировка")
    if not st.session_state.training:
        if st.button("Сформировать задания", type="primary"):
            st.session_state.training = choose_tasks(st.session_state.cadet_id, 8)
            st.session_state.ti = 0
            st.session_state.details = []
            st.session_state.finished = False
            st.rerun()
        else:
            st.info("Задания будут подобраны по вашей карте навыков и предыдущим попыткам.")
    elif st.session_state.finished:
        score = sum(x["correct"] for x in st.session_state.details)
        st.success(f"Сессия завершена: {score}/{len(st.session_state.details)}")
        for x in st.session_state.details:
            with st.expander(("✅ " if x["correct"] else "❌ ") + x["title"]):
                st.write(f"Навык: **{x['skill']}**")
                st.write(f"Ваш ответ: {x['selected']}")
                st.write(f"Правильный вариант: {x['correct_answer']}")
                st.write(x.get("ai_explanation") or x["explanation"])
                if x.get("ai_task"):
                    st.caption("Дополнительное задание сохранено в индивидуальной истории.")
        if st.button("Новый индивидуальный план", type="primary"):
            st.session_state.training = []
            go("plan")
    else:
        i = st.session_state.ti
        if i >= len(st.session_state.training):
            try:
                save_session(st.session_state.cadet_id, st.session_state.details)
                st.session_state.finished = True
                st.rerun()
            except Exception as exc:
                st.error(f"Не удалось сохранить тренировку: {exc}")
                st.stop()
        t = st.session_state.training[i]
        st.progress(i / len(st.session_state.training))
        st.caption(f"Задание {i + 1} из {len(st.session_state.training)} · {t['skill']}")
        st.subheader(t["title"])
        st.write(t["question"])
        ans = st.radio("Ответ", t["options"], index=None, key=f"tr_{i}_{t['id']}")
        if st.button("Проверить", type="primary"):
            if ans is None:
                st.error("Выберите ответ.")
                st.stop()
            correct = ans == t["options"][t["answer"]]
            x = {
                "task_id": t["id"],
                "title": t["title"],
                "component": t["component"],
                "skill": t["skill"],
                "correct": correct,
                "selected": ans,
                "correct_answer": t["options"][t["answer"]],
                "explanation": t["explanation"],
            }
            if not correct:
                x["ai_explanation"] = offline_explain(t, ans)
                nt = offline_new_task(t, st.session_state.cadet_id)
                if nt:
                    try:
                        save_generated_task(st.session_state.cadet_id, t["id"], nt)
                    except Exception:
                        pass
                    x["ai_task"] = nt
            st.session_state.details.append(x)
            st.session_state.ti += 1
            st.rerun()

elif st.session_state.page == "coach":
    st.header("Автономный наставник")
    st.info("Работает без OpenAI API, ключей и оплаты. Использует правила критического мышления, ваш план и историю попыток.")
    if "chat" not in st.session_state:
        st.session_state.chat = current_chat(st.session_state.cadet_id)
    for m in st.session_state.chat:
        with st.chat_message("user"):
            st.write(m["user_message"] if isinstance(m, dict) else m.get("user_message"))
        with st.chat_message("assistant"):
            st.write(m["ai_message"] if isinstance(m, dict) else m.get("ai_message"))
    msg = st.chat_input("Например: помоги понять, где я поспешил с выводом")
    if msg:
        answer = offline_chat(msg, st.session_state.cadet_id)
        save_chat(st.session_state.cadet_id, msg, answer)
        st.session_state.chat = current_chat(st.session_state.cadet_id)
        st.rerun()

elif st.session_state.page == "history":
    st.header("История прогресса")
    rows_data = sessions(st.session_state.cadet_id)
    if not rows_data:
        st.info("Тренировок пока нет.")
    for r in rows_data:
        with st.expander(f"{r['created_at']} · {r['score']}/{r['total']}"):
            for x in (r.get("details_json") or []):
                st.write(("✅" if x["correct"] else "❌"), x["skill"])

elif st.session_state.page == "teacher":
    st.header("Педагог / исследователь")
    cadets = rows(sb.table("cadets").select("id").execute())
    diagnostics = rows(sb.table("diagnostics").select("id").execute())
    task_attempts = rows(sb.table("task_attempts").select("skill,correct").execute())
    a, b, d = st.columns(3)
    a.metric("Кадетов", len(cadets))
    b.metric("Диагностик", len(diagnostics))
    d.metric("Попыток в тренировках", len(task_attempts))
    st.subheader("Сводка по навыкам")
    grouped = {}
    for r in task_attempts:
        skill = r["skill"]
        grouped.setdefault(skill, []).append(bool(r["correct"]))
    for skill, values in sorted(grouped.items(), key=lambda item: sum(item[1]) / len(item[1])):
        st.write(f"**{skill}** — {len(values)} попыток, точность {sum(values) / len(values) * 100:.1f}%")
    st.download_button(
        "Скачать CSV обезличенной статистики",
        csv_export(),
        "cadet_critical_thinking_progress.csv",
        "text/csv",
    )
    st.caption("В экспорт не включаются имена кадетов; используется код кадета. Доступ к панели должен контролироваться организацией.")

st.divider()
st.caption("Интеллектуальная система персонализированной диагностики и развития критического мышления кадет · автономный режим без OpenAI API. Диагностика фиксирована; индивидуальный маршрут строится по навыкам и истории попыток.")
