# Проверка реализации — обновлено 28.09.2026

**Последняя повторная проверка (01:46 UTC+5):** настроенный адрес `192.168.0.155:8000` не устанавливает TCP-соединение из Windows и Docker. Windows `Test-NetConnection`: TCP=false, ping=`DestinationHostUnreachable`; маршрут идёт напрямую по Wi-Fi `192.168.0.0/24` с адреса `192.168.0.194`. Повторные JSON/tool probes не достигли сервера. Предыдущие ошибки MTPLX ниже — история, их текущее состояние не подтверждено. [Свидетельство](artifacts/model-recheck.json).

**Docker/GitLab/Runner проверены; полный модельный end-to-end не выполнен.** Пользователь подключил локальный MTPLX вместо облака; URL/model/key применены к gateway, MODEL_MODE=local. Сервер отклоняет JSON mode и не завершает generation probes в установленный timeout. Worker выключен, два run ожидают в очереди. Проверки ниже не подменяют невыполненные A01–A22. Статусы: `passed`, `failed`, `blocked`, `not_run`.

## Выполненные запуски

| Команда / действие | Статус и результат | Свидетельство |
|---|---|---|
| Первичная проверка PowerShell CIM, Get-Command, WSL list, порты, наличие ключей без значений | passed; Windows 11 x64, RAM 23.9 GiB, i5-10400/12 logical CPU; Docker тогда отсутствовал, позднее установлен пользователем; облачных ключей не было | IMPLEMENTATION_STATUS.md |
| `.venv/Scripts/python scripts/preflight.py` после подключения модели | passed: локальный Linux Engine 29.8.0/Compose, model/key/base заданы, MODEL_MODE=local допускает price=0. Это проверка наличия настроек, не совместимости модели | [preflight](artifacts/preflight.json) |
| Реальный MTPLX `/models`, `/chat/completions` и OpenAPI | models=200; JSON mode=400 (missing llguidance), forced tool call timeout 120 s; plain 16-token request и AR без thinking timeout 60 s. JSON fallback не включён без успешного доказательства совместимости | [model capabilities](artifacts/model-capabilities.json), [minimal](artifacts/local-model-minimal.json) |
| Реальные контейнеры gateway/LiteLLM с пользовательским `.env` | Три health в local mode; authenticated proxy models=200; JSON request через gateway=502 (нормализованный upstream 400). Worker не стартовал | [model connection](artifacts/model-connection.json) |
| Повтор bootstrap 28.09 с настроенным MTPLX | Ожидаемый blocked/exit 1 на реальном capability probe; worker не запущен, обе записи по-прежнему queued, model_calls=0. Отрицательный сценарий проверки перед стартом подтверждён | [probe](artifacts/model-probe.json) |
| Portable `initdb`, `pg_ctl`, `createdb` только внутри `.state`, loopback 55432 | passed; настоящая PostgreSQL 17.6, schema/indices и PostgresSaver setup | artifacts/versions.json, artifacts/tool-downloads.json |
| `$env:TEST_DATABASE_URL='postgresql://lab@127.0.0.1:55432/lab_test'; .venv/Scripts/python -m pytest -q --junitxml=artifacts/pytest.xml` | **passed: 52 tests, 0 failures, 0 skips, 76.58 s** | [JUnit](artifacts/pytest.xml) |
| Повтор `tests/test_model_contract.py` после защиты роли ответа провайдера | passed: 1 test, 1.40 s; ответ с role=system преобразуется в assistant в следующем запросе. Также входит в полный набор выше | [JUnit boundary](artifacts/pytest-model-boundary.xml) |
| `.venv/Scripts/python scripts/bootstrap.py` и повтор bootstrap | passed; GitLab 19.4.1 healthy, Runner 19.4.0 online; ровно два проекта/исходных MR/hooks, один Runner; API/PG/LiteLLM/три gateway запущены. Worker выключен до cloud settings | [bootstrap](artifacts/bootstrap.json), [idempotency](artifacts/bootstrap-idempotency.json), [images](artifacts/runtime-images.json), [packages](artifacts/runtime-packages.json) |
| `.venv/Scripts/python scripts/validate_infrastructure.py` | passed; настоящие Java job/pipeline 2 и Python job/pipeline 4, tests=1/errors=0, diff coverage=62.5%, gate failed; actual SHA/JUnit/coverage/logs проверены. API 401 без токена и 200 с токеном | [infrastructure](artifacts/infrastructure.json) |
| `Events.poll(); Events.drain()` дважды внутри контейнера API | passed; доставлены реальные GitLab webhooks, создано ровно два queued run, IDs сохраняются, model_calls=0 | [trigger](artifacts/docker-trigger.json) |
| Пересоздание API с миграцией и `docker compose ... restart postgres` | passed; оба run/IDs сохранены, started_at=NULL до первого исполнения. Это не A16 worker restart во время CI | [queue restart](artifacts/docker-queue-restart.json) |
| `.venv/Scripts/python scripts/validate_demos.py` | passed; Python: 1 исходный → 11 tests; Java: 1 исходный → 10 tests. Diff coverage 62.5% → 100% в обоих. Mutation `>= 10000` → `> 10000`: по 2 assertion failures | [summary](artifacts/local-demos/summary.json), `artifacts/local-demos/{python,java}/{baseline,tests,mutation}` |
| `.venv/Scripts/python scripts/validate_router.py` | passed; настоящий LiteLLM 1.102.1, 12 успешных routed requests; HTTP double первого endpoint отвечает 503 | [router report](artifacts/router-local.json) |
| `.venv/Scripts/python scripts/validate_proxy.py` | passed; настоящий LiteLLM Proxy и три native gateway процесса, HTTP mock upstream. Три direct calls прошли; после остановки spark-1 четыре proxy calls прошли через spark-2/3 | [proxy report](artifacts/proxy-local.json) |
| `.tools/docker-compose.exe --env-file .env --env-file config/images.env config --quiet` | passed; Compose 2.39.4 разбирает/interpolates конфигурацию. Секреты не выведены | compose.yaml, artifacts/versions.json |
| `.tools/kubectl.exe kustomize deploy/kubernetes` | passed; offline render, без подключения к кластеру | [rendered manifests](artifacts/kubernetes-rendered.yaml), artifacts/kubectl-version.json |
| `.venv/Scripts/ruff check agent_lab scripts tests ci` | passed | конфигурация Ruff в pyproject.toml |
| `.venv/Scripts/uv lock --check`, `python -m compileall -q agent_lab scripts ci`, `uv build --wheel --out-dir .state/dist` | passed; 142 locked packages, wheel включает `agent_lab/schema.sql`, secret files отсутствуют | uv.lock; `.state/dist/unit_test_agent_lab-0.1.0-py3-none-any.whl` (локальный ignored artifact) |
| `scripts/resolve_images.py` | passed; реальные manifests/digests официального registry, все имеют linux/amd64 descriptor. Образы не запускались | config/images.lock.json, config/images.env |
| `git check-ignore` для .env/.state/.tools/.venv | passed; secrets/runtime/cache исключены | .gitignore |

В component tests используются **реальные PostgreSQL и LangGraph**, но GitLab/LLM — `tests/fakes.py` или HTTP doubles. Demo product builds используют **рукописные** `tests/fixtures`, не выдаются за результат Coder в облаке. Native Proxy/gateway проверяет настоящий код транспорта, но upstream возвращает явно синтетический ответ.

## Матрица ACCEPTANCE.md

| ID | Независимая локальная проверка | Полная требуемая приёмка | Свидетельство / что осталось |
|---|---|---|---|
| A01 | passed | passed | Реальный bootstrap/повтор, healthy инфраструктура и Runner online; worker ожидает успешного model probe, см. A02 |
| A02 | passed | blocked | Реальный локальный MTPLX задан пользователем вместо облака, но JSON support/generation probes не прошли; mock-only component tests не заменяют это |
| A03 | passed | blocked | Настоящий native Proxy + три gateway, один остановлен; контейнеры/реальный cloud failover не проверены |
| A04 | passed | passed | Оба настоящих source MR: Runner unit tests passed, coverage 62.5% gate fail; реальные webhooks/polling создали durable runs. Исполнение ролей отдельно A06 |
| A05 | passed | blocked | pass/unknown/infra/build/foreign target/self MR/stale SHA/fork отвергаются; live events не проверены |
| A06 | passed | blocked | Настоящий LangGraph, O1–O8 в PostgreSQL; GitLab/model doubles; требуется реальный cloud цикл |
| A07 | passed | blocked | Рукописные fixtures дают 100%, ловят известную регрессию; cloud-generated tests и Runner mutation пока не выполнены |
| A08 | passed | blocked | Exact remote tree gate, target feature, stable marker, no auto-merge проверены doubles; исходные MR созданы, MR с generated tests ещё нет |
| A09 | passed | blocked | revise_scenarios → Analyst → Coder → Runner → Reviewer через настоящий граф; модель stub |
| A10 | passed | blocked | revise_tests и build-error → Coder с logs, новая версия; live repair не выполнен |
| A11 | passed | blocked | Реальная локальная mutation даёт failures; defect path сохраняет evidence, не публикует MR; cloud defect case не выполнен |
| A12 | passed | blocked | Author user ID, feedback при source gate passed, обновление того же MR в component test; реального 👎 нет |
| A13 | passed | blocked | Foreign/duplicate award игнорируется; PostgreSQL event/run dedupe конкурентно проверен |
| A14 | passed | blocked | Tagged note сохраняется до обработки webhook; late feedback поставлен следующим run; live GitLab note не проверен |
| A15 | passed | blocked | Смена source SHA запрещает publication и ставит canonical refresh; live source update не проверен |
| A16 | passed | blocked | Component PostgresSaver recovery и реальный PostgreSQL-container restart с сохранением очереди прошли; actual worker+GitLab CI SIGKILL ещё нет |
| A17 | passed | blocked | Fault injection после внешнего side effect до ack; новый Actions instance reconciles без дубликата; внешняя система в тесте double |
| A18 | passed | blocked | Реальная PostgreSQL: immutable versions, inputs/hashes/source/test SHA; история полного облачного run отсутствует |
| A19 | passed | blocked | Token/cost reservation, policy/tool boundary и infrastructure retry ceiling проверены; limits реального provider не проверены |
| A20 | passed | blocked | Два live GitLab проекта/MR и реальные Java/Python jobs проверены; оба полных облачных цикла ещё не выполнены |
| A21 | passed | blocked | Traversal/symlink/secrets/production/old-tests/CI edits, skip/constant assertions и tampered remote commit отвергнуты; cloud adversarial case не выполнен |
| A22 | passed | not_run | Configurable cloud/local API, Kustomize render, migration plan; существующий кластер/external PostgreSQL/Spark не использовались |

`passed` в локальном столбце означает только указанную проверку. Ни один `blocked/not_run` полный сценарий не объявлен завершённым на основании unit tests.

## Версии

Python 3.13.7, FastAPI 0.141.1, Uvicorn 0.54.0, LangGraph 1.2.12, langgraph-checkpoint-postgres 3.1.2, psycopg 3.3.6, LiteLLM 1.102.1, pytest 9.1.1. Локальная PostgreSQL 17.6, Maven 3.9.11, установленная Java 18.0.2.1; release target demo — Java 17. Реальные Docker CI jobs используют Temurin 17/Python 3.12, Compose PostgreSQL — 17.11. Инвентаризация: [versions.json](artifacts/versions.json), [Docker packages](artifacts/runtime-packages.json), [runtime images](artifacts/runtime-images.json); lock и manifests содержат hashes/digests.

## Найденные и исправленные проблемы

- Windows выбирал системный Python для subprocess вместо venv: CI entrypoint теперь разрешает executable через `shutil.which` из проверенного PATH. Повторные реальные build/mutation проверки прошли.
- Native LiteLLM CLI унаследовал несовместимый `DEBUG=release`; его баннер не помещался в cp1251, а autoload dotenv подтягивал ненужный DATABASE_URL. В validation subprocess env изолирован от dotenv/DB, используется UTF-8. Реальный native Proxy после исправления успешно запущен. Docker image не копирует .env и не передаёт Proxy DB credentials.
- Восстановление model result до graph checkpoint учитывает изменившийся контекст без повторного облачного вызова.
- Проверяется фактическое remote tree/parent/content candidate, а не только намерение Coder. Source/CI/old tests edits и внешняя правка publication branch блокируются.
- Некорректный event/report больше не останавливает обработку последующих событий; оригинальный tagged-text webhook сохраняется до worker polling.
- Роль сообщения от model provider не переносится в следующий запрос: tool-loop фиксирует assistant, даже если провайдер вернул system. Затронутый contract test повторно прошёл после исправления.
- На Docker Desktop readiness через NAT получал 404 из-за monitoring IP allowlist GitLab. Bootstrap ждёт внутренний healthcheck Compose и проверяет `/users/sign_in`, без расширения allowlist.
- TLS DinD сертификат не содержал `dind`: задан стабильный hostname, контейнер пересоздан с сохранением volumes; реальная CI сборка и Runner прошли.
- CI image теперь строится до записи конфигурации Runner, чтобы pending jobs не стартовали раньше сборки.
- Runtime deadline перенесён с времени постановки в очередь на сохранённое первое исполнение `started_at`; restart/resume его не сбрасывают. Миграция и regression test прошли.

## Следующий внешний шаг

На сервере пользователя 192.168.0.155 установить недостающий `llguidance` в окружение MTPLX/перезапустить runtime и дождаться либо исправить прогрев/генерацию. У агента нет доступа к окружению этого сервера для установки. После успешных JSON/tool probes повторить bootstrap (включит worker), затем `scripts/acceptance.py --feedback --mutation`; отдельно `--restart` с документированной задержкой CI. Исходные MR/job/pipeline/image IDs сохранены; model-generated test MR пока отсутствует. Пользователь выбрал local model, реальный cloud acceptance этим не подменяется.

Для продолжения с этой папкой ничего восстанавливать из чата не требуется. Docker-стенд оставлен работающим, worker выключен, два run queued без расходов. Временные native Proxy/gateway процессы и portable PostgreSQL остановлены; её данные остаются в `.state/pgdata`.
