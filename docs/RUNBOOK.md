# Эксплуатация

## Процесс и данные

Webhook проверяет secret/project и сохраняет событие в PostgreSQL. Worker перечитывает канонический GitLab API. Polling восстанавливает потерянные события и читает award emoji; это не scheduled генерация. Обычный запуск — только согласованные свежие CI-отчёты с failed unit coverage gate, пройденными тестами и разрешённым source MR.

Проектные команды — argv в `.test-agent.json`; SHA256 конфигурации, CI entrypoint и build files закреплены в `config/projects.yaml`. Их изменение требует отдельной проверки. Модель не задаёт shell commands. Snapshot имеет отдельную директорию project/run/SHA; инструменты — list/read/literal search, без векторной БД.

Coder может добавлять только `test_agent_*.py` или `Agent*Test.java` в разрешённых test roots. Исходные файлы, включая старые tests, неизменны. Повторная генерация задаёт полный manifest новых файлов относительно исходного SHA. Это намеренно узкая MVP-политика; добавление зависимостей и изменение исходных тестов не разрешены.

Candidate `agent-verify/<mr>/<run>/<iteration>-<attempt>` создаёт один автоматический push pipeline. Worker не POST-ит второй pipeline при потере ответа. Известная infrastructure failure вызывает ограниченный повтор той же версии tests на новом immutable ref; build error возвращает Coder; assertion failure отправляется Reviewer. Нужны source SHA/test SHA/job/pipeline и реальные свежие artifacts. Missing coverage — unknown/error.

PostgresSaver хранит checkpoints. PostgreSQL session advisory lock сериализует все runs одного project/MR; другие продукты независимы. Lease используется для обнаружения умершего worker, а не как единственная защита. Перед побочными эффектами worker проверяет живую lock session и cancel/deadline. Pending intent сверяется с GitLab по стабильной идентичности. Commit/ref, pipeline, push, MR и описание MR имеют отдельные actions. Повтор успешного model node использует его сохранённый JSON; неоднозначный незавершённый облачный вызов может быть повторён в пределах лимита.

O1–O9 сохраняются неизменными версиями; `lab_objects.id` определяет место хранения, API выдаёт hashes, inputs, source/test SHA и всю историю. O1 — product diff, O7 — tests. O2/O3 исходного и проверочного CI различаются по origin и SHA. GitLab хранит полные artifacts; prompt получает ограниченный лог и ссылки. Использованные пути и model usage записываются отдельно. Автоматического retention cleanup нет; 30-дневный demo artifact retention нужно изменить до production.

Reviewer оценивает каждый доступный объект. Решения: publish/revise_scenarios/revise_tests. Product defect/failing CI, missing data, policy violation или лимиты приводят к needs_human. Перед публикацией повторно читаются source MR/SHA и проверочный CI. Exact tested commit пушится с force-with-lease в `ai-tests/<source-mr>`; один MR в feature; merge только человеком.

## Обратная связь

Реакция 👎 проверяется по user ID автора исходного MR. Award ID — стабильный ключ; удаление/повторное добавление создаёт новую идентичность. Tagged-комментарий `@unit-test-agent` всегда сохраняется. До первой авторской реакции он только накапливается. После неё поздний/изменённый комментарий ставит последовательную доработку в очередь. Активный model call не прерывается; следующий run берёт последний опубликованный контекст. Закрытый или слитый test MR не открывается заново.

При смене source SHA старый run останавливается и создаёт событие refresh. Обычный refresh заново проверяет gate. Feedback refresh имеет собственный триггер, но всё равно требует актуальных source MR/context/reports.

## Лимиты и модель

Облачный режим требует `TOKEN_PRICE_UPPER_USD > 0`; локальный `MODEL_MODE=local` допускает 0. Цена вводится локально после выбора провайдера; в коде нет выдуманного тарифа. Перед запросом резервируется консервативное число токенов по UTF-8 bytes + max output, умноженное на 3 с учётом двух retry LiteLLM. После ответа расход также учитывается с запасом ×3; `lab_calls.usage` хранит фактический usage отдельного ответа. При неоднозначном сетевом сбое резерв остаётся занятым. Это верхняя оценка бюджета, а не сверка банковского счёта провайдера. Для модели с дополнительным неизвестным тарифицируемым ресурсом нужен отдельный подтверждённый cost adapter.

Bootstrap сначала останавливает worker на время перенастройки, проверяет JSON mode, принудительный tool call и применение результата инструмента через каждый gateway и LiteLLM. Только успешный probe разрешает запуск worker. Совместимость проверяется настоящими запросами, одного `/models` или `/health` недостаточно. Текущий MTPLX endpoint пользователя требует llguidance для JSON mode; fallback без этого параметра не включён, поскольку и обычная генерация не прошла проверку.

Шлюзы имеют общий upstream limit. Три адреса не утраивают квоту/скорость. Локальный worker работает последовательно; LiteLLM ограничивает RPM/TPM на deployments. Общий провайдер может ответить 429 всем трём. При масштабировании worker необходимо добавить общий provider quota limiter/Redis в LiteLLM и проверить нагрузку; текущие локальные routing counters не являются распределённой квотой.

## API

Header `Authorization: Bearer <API_TOKEN>` обязателен для:

- `GET /runs`, `/runs/{id}`, `/runs/{id}/objects`, `/runs/{id}/actions`, `/runs/{id}/calls`.
- `GET /events` — disposition, dedupe, сохранённые/проигнорированные события.
- `POST /runs/{id}/cancel` — кооперативная отмена.
- `POST /runs/{id}/resume` — с сохранённого checkpoint, без сброса расходов и времени.

Уже исполняющийся LLM request/Runner job при cancel мгновенно не прерывается; после ответа publication guard запрещает запись результата. Лимит времени начинается с первого исполнения (`started_at` в PostgreSQL), а не с ожидания в очереди. Retry/restart/resume не сбрасывают это время. Resume не продлевает исчерпанный бюджет. Если причина — лимит времени/токенов, нужно осознанно менять операторскую конфигурацию/создавать новую ревизию; повторный HTTP resume не должен обходить лимит.

## Restart acceptance

Перед новым реальным run добавить в **локальный demo-проект** CI variable `LAB_TEST_DELAY_SECONDS=45`. Проверенный `.agent-ci.py` поддерживает задержку не более 60 секунд только на `agent-verify/*`. Запустить:

```powershell
.venv/Scripts/python scripts/acceptance.py --restart
```

Скрипт проверяет, что настоящий pipeline ещё running/pending, посылает SIGKILL только Compose worker этого стенда, поднимает его и сверяет сохранённый pipeline ID. Если работающий job не наблюдался, скрипт явно завершается `NOT RUN`. Lease после смерти освобождается не позднее 180 секунд. Тест на границе внешней записи без GitLab уже выполнен с PostgreSQL и fault injection; настоящий Docker/GitLab crash boundary остаётся отдельной приёмкой A17.

## Сервисы

```powershell
docker compose --env-file .env --env-file config/images.env ps
docker compose --env-file .env --env-file config/images.env logs --tail 100 worker
docker compose --env-file .env --env-file config/images.env stop spark-1
# Проверить новый реальный model call через LiteLLM, затем восстановить:
docker compose --env-file .env --env-file config/images.env start spark-1
docker compose --env-file .env --env-file config/images.env down
```

`down` сохраняет volumes. Глобальный prune запрещён. `compose config` использовать с `--quiet`, чтобы не вывести интерполированные секреты. Проверенные registry digests не заменяют подтверждения build/run: после настоящей сборки сохранить `compose images --format json` и package inventory.
