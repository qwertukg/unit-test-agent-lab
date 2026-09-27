# Unit Test Agent Lab

Последняя повторная проверка: `192.168.0.155:8000` сейчас недоступен из Windows и Docker на уровне TCP. Нужен актуальный доступный адрес API; предыдущие результаты MTPLX ниже не подтверждают его текущее состояние. См. [model-recheck.json](artifacts/model-recheck.json).

Реализован стенд **Analyst → Coder → GitLab Runner → Reviewer**: Python/FastAPI/LangGraph, PostgreSQL, LiteLLM, три облачных API-шлюза, два синтетических продукта Java/Python, очередь/checkpoints и проверки политики.

**Docker-стенд запущен:** GitLab, online Runner, PostgreSQL, API, LiteLLM и три шлюза. В обоих demo настоящие CI jobs подтвердили coverage fail 62.5%; два run стоят в долговечной очереди, повторные события не создали дубликатов. Пользователь подключил локальную модель MTPLX через `.env`. **Полный цикл пока blocked:** сервер модели отклоняет JSON mode (отсутствует `llguidance`), запросы генерации завершились timeout; worker выключен. [VALIDATION.md](VALIDATION.md) содержит свидетельства.

## Запуск

PowerShell из корня проекта; нужен работающий Docker с Linux containers и Compose v2:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install uv==0.12.19
.venv/Scripts/uv sync --frozen
.venv/Scripts/python scripts/init_env.py
```

Локально заполнить `.env`: `CLOUD_MODEL` (`provider/model` в формате LiteLLM), `CLOUD_API_KEY`, при необходимости `CLOUD_BASE_URL`; `TOKEN_PRICE_UPPER_USD` — консервативная верхняя цена за миллион токенов. Ключ не отправлять в чат. Лимит `MAX_COST_USD` по умолчанию 5. Секреты локальных сервисов генерируются автоматически.

Для выбранного пользователем локального OpenAI-compatible сервера уже выставлено:

```dotenv
CLOUD_BASE_URL=http://192.168.0.155:8000/v1
CLOUD_MODEL=openai/mtplx-qwen36-27b-optimized-quality
MODEL_MODE=local
TOKEN_PRICE_UPPER_USD=0
```

`CLOUD_API_KEY` уже введён пользователем и не публикуется. `MODEL_BASE_URL` остаётся адресом LiteLLM. Названия `CLOUD_*` сохранены для совместимости адаптера, они применимы и к локальному upstream. Нулевой monetary budget допустим в local mode; token/call/time limits сохраняются. Три gateway обращаются к одному серверу пользователя, это не три независимые GPU.

```powershell
.venv/Scripts/python scripts/preflight.py
docker version
docker compose --env-file .env --env-file config/images.env config --quiet
.venv/Scripts/python scripts/bootstrap.py
.venv/Scripts/python scripts/validate_infrastructure.py
```

Preflight сохраняет `artifacts/preflight.json`, проверяет локальный Docker, ресурсы, порты и наличие настроек без вывода секретов. Код выхода `2` означает, что prerequisites ещё не готовы; текущие блокеры перечислены в отчёте.

Bootstrap ограничен `localhost:8929`: создаёт только изолированные GitLab/PostgreSQL/DinD/Runner, отдельного runtime bot, два demo-проекта, feature → dev MR и webhooks. Повтор использует существующие ресурсы. В Linux заменить `.venv/Scripts` на `.venv/bin`.

| Адрес после успешного bootstrap | Назначение |
|---|---|
| `http://localhost:8929` | GitLab; root password локально в `.env` |
| `http://localhost:8000/health` и `/ready` | API liveness и PostgreSQL readiness |
| `http://localhost:8000/runs` | Runs; `Authorization: Bearer <API_TOKEN>` |
| `http://localhost:4000/v1` | LiteLLM; ключ `MODEL_API_KEY` |

Эти адреса проверены на работающем стенде. Исходные MR: [Java](http://localhost:8929/root/unit-test-agent-demo-java/-/merge_requests/1), [Python](http://localhost:8929/root/unit-test-agent-demo-python/-/merge_requests/1). Внутренний GitLab URL и clone_url Runner — `http://gitlab:8929`. PostgreSQL, DinD и gateways на хост не публикуются. Bootstrap сохранит проекты, остановит worker на время перенастройки и включит его только после реальных JSON/tool probes. На текущем сервере сначала требуется исправить JSON support и доступность генерации.

## Облачная приёмка

```powershell
docker compose --env-file .env --env-file config/images.env exec -T litellm python -m agent_lab.probe > artifacts/cloud-probe.json
.venv/Scripts/python scripts/acceptance.py --feedback --mutation
```

Probe проверяет реальные tool calls/JSON на всех трёх шлюзах и LiteLLM. Acceptance ожидает два опубликованных run, проверяет O1–O8/usage, на отдельной synthetic mutation-ветке проверяет обнаружение регрессии настоящим Runner, оставляет демонстрационные 👎 и tagged-комментарий от автора и проверяет тот же MR. Product mutation не попадает в MR тестов. Автоматического merge нет. Ссылки MR сохраняются в `artifacts/bootstrap.json`.

## Локальные проверки

```powershell
$env:TEST_DATABASE_URL='postgresql://lab@127.0.0.1:55432/lab_test'
.venv/Scripts/python -m pytest -q --junitxml=artifacts/pytest.xml
.venv/Scripts/ruff check agent_lab scripts tests ci
.venv/Scripts/python scripts/validate_demos.py
.venv/Scripts/uv sync --frozen --group proxy
.venv/Scripts/python scripts/validate_router.py
```

Нужна выделенная тестовая PostgreSQL, JDK и Maven. Без `TEST_DATABASE_URL` PG tests явно skipped. Demo script создаёт disposable Git-копии в `.state`, запускает настоящие pytest/Maven с **рукописными** fixtures, временно вносит известную регрессию и проверяет падения. Это не облачная генерация.

Подготовленная portable PostgreSQL (только локальные тесты):

```powershell
.tools/postgres/pgsql/bin/pg_ctl.exe -D .state/pgdata -l .state/postgres.log -o '-p 55432 -h 127.0.0.1' start
# По окончании:
.tools/postgres/pgsql/bin/pg_ctl.exe -D .state/pgdata stop
```

## Документы

- [RUNBOOK](docs/RUNBOOK.md): архитектура реализации, API, recovery, restart acceptance, ограничения.
- [SECURITY](docs/SECURITY.md): разделение credentials и привилегированный учебный DinD; это не production sandbox.
- [MIGRATION](docs/MIGRATION.md): Kubernetes, внешняя PostgreSQL, три независимых Spark.
- [VERIFIED_SOURCES](docs/VERIFIED_SOURCES.md): API, проверенные версии и digests.
- [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md) и [VALIDATION](VALIDATION.md): выполнено, свидетельства и блокеры.

Исходные требования и PDF сохранены. `MANIFEST.sha256` относится к исходному архиву передачи, не к текущей реализации. Kubernetes не применялся, live YouTrack не реализован: используется явный demo fixture adapter.
