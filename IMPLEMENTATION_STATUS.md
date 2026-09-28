# Статус реализации

Обновлено 28.09.2026. Работа начата по START_HERE.md; исходные требования сохранены, пользователь выбрал локальный внешний model endpoint для следующей проверки.

## План

1. Настоящий граф Analyst → Coder → GitLab Runner → Reviewer, PostgreSQL queue/checkpoints, проверенные контракты и политика.
2. Docker Compose, bootstrap GitLab/Runner, три облачных gateway и LiteLLM, Java и Python demo.
3. Проверка восстановлений, свежести, событий, политик, ограничений; команды полной приёмки.
4. Документация запуска, свидетельства и Kubernetes/external PostgreSQL/Spark migration.

## Окружение

Windows 11 Pro x64, Intel i5-10400 (12 logical CPU), RAM 23.9 GiB. Python 3.13.7, Java 18.0.2.1, Git доступны. Изначально Docker отсутствовал; затем пользователь установил Docker Desktop в профиль пользователя. Реально доступны Linux Engine 29.8.0 и Compose 5.5.1, 12 CPU/11.6 GiB Docker RAM. Стенд использует loopback-порты 8000/4000/8929/2224. Docker хранит данные на C:; после сборок оставалось около 6 GiB, на D: около 745 GiB. Репозиторий передачи не имел .git.

## Текущая проверка

- Блокер подключения снят: MTPLX `http://192.168.0.105:8000/v1`, `mtplx-qwen36-27b-optimized-speed`, MODEL_MODE=local и MODEL_JSON_MODE=prompt. Все 12 реальных запросов JSON/tool/tool-result на spark-1/2/3 и LiteLLM прошли; `artifacts/model-probe-current.json`. Ключ не выводился.
- Пользовательская конфигурация сейчас включает только Java; Python отключён. Лимиты пользователя сохранены: 1 500 000 tokens, 64 model calls, 7200 seconds. Это локальная модель, не облачная приёмка.
- Предыдущий run `76a79a987b4a41c974e8e2b700f23075` остановлен на 9 вызовах Analyst: model tool-step limit. История и бюджеты сохранены. Исправлен последний запрос: tool_choice=none требует итоговый JSON, дополнительных tool rounds нет.
- Java run `a92a9e87c36e564eeaa89f08e88884f2` получил 6 реальных ответов Analyst и остановился на нарушении JSON schema: `artifacts/model-schema-stop.json`. Coder/MR/restart/feedback не выполнены. Добавлен один запрос исправления формата внутри прежнего бюджета; 19 затронутых model/workflow tests passed. Повтор на новой README-only ревизии `2d93f5a390b0f546e35b69b7bebf2c125903abf5` проверяется отдельно; старые runs не сброшены.
- Исправлена передача MAX_EXTERNAL_ATTEMPTS из .env в Compose API/worker. Пользовательское значение 1 применяется; типы validation errors и имена разрешённых tools теперь доступны в диагностике без prompts/секретов.

## Выполнено

- Реальный LangGraph StateGraph с Analyst/Coder/Runner/Reviewer, возвратами, publication guard, time/token/cost/tool/iteration limits и обнаружением product defect.
- PostgreSQL schema: durable events/runs/queue, immutable O1–O9, actions, usage, feedback, links; PostgresSaver; session advisory lock project/MR, lease и recovery. Pending внешнее действие сначала reconciles, потом повторяется только при отсутствии результата.
- GitLab API/Git adapters: точные source/test SHA, сравнение фактического remote tree с разрешённым test manifest, проверочные refs/pipelines/artifacts, exact commit push с force-with-lease, один MR в feature, без auto-merge и без перезаписи внешних правок ветки.
- Проверяемые webhook, canonical polling, eligibility именно coverage fail, author-ID 👎, event dedupe, snapshots поздних tagged-комментариев и последовательные feedback runs. Некорректное событие не блокирует остальную очередь.
- Аутентифицированный FastAPI: status/object/action/call history, cancel/resume; health/readiness. Snapshot read/search/list tools, защита путей, политика immutable production/old tests/CI/config.
- Read-only preflight перед bootstrap: локальный Docker context/Engine/Compose, ресурсы, порты и наличие cloud-настроек; отчёт без значений секретов. Ответ провайдера в model tool-loop получает только роль assistant, произвольная роль из ответа не принимается.
- Compose с закреплёнными registry digests: GitLab CE 19.4.1, Runner 19.4.0, PostgreSQL, отдельный TLS DinD, API/worker/LiteLLM/три gateway. Идемпотентный localhost-only bootstrap; root token отделён от Developer bot, model не получает host/Docker/GitLab credentials.
- Java/JUnit/JaCoCo/Maven и Python/pytest/Cobertura demo, reviewed argv/config hashes, real CI report normalizer. Скрипты cloud probes, feedback/restart acceptance и проверки generated tests известной mutation на отдельной synthetic ветке.
- `uv.lock` (142 packages), dependency pins, JSON schemas, Dockerfiles, `.env.example`, generated local `.env` (ignored), эксплуатационные инструкции и Kustomize baseline внешней PostgreSQL/трёх Spark. Git инициализирован локально; секреты не staged/committed, коммит не создавался.

## Реально проверено на этом хосте

- **58 автоматизированных тестов passed, без skips**, включая настоящую PostgreSQL 17.6 и PostgresSaver, recovery/locking/deduplication, граф и негативные сценарии. GitLab/model в component tests — явно обозначенные doubles. Новый тест подтверждает отсчёт deadline с первого исполнения без сброса при restart/resume.
- Реальный Docker bootstrap: два demo-проекта, по одному исходному MR, один online Runner; повтор без дубликатов. Java job 2/pipeline 2 и Python job 4/pipeline 4: тесты passed, diff coverage 62.5%, gate failed, свежие JUnit/coverage/logs проверены.
- Настоящие GitLab webhooks доставлены; canonical polling поставил два run в PostgreSQL, повтор сохранил IDs. Перезапуск контейнера PostgreSQL и пересоздание API сохранили очередь; model_calls=0, started_at=NULL. Это не worker restart во время CI.
- Настоящие локальные сборки Java/Python: diff coverage **62.5% → 100%**, после известной mutation **по 2 assertion failures**. Добавленные контрольные tests рукописные, не облачные.
- Настоящий LiteLLM Router: 12 успешных запросов при HTTP 503 одного HTTP double endpoint. Затем отдельно запущены настоящий Proxy + три native gateway процесса с HTTP mock upstream; все три gateway доступны, четыре запроса после остановки первого прошли через второй/третий.
- Compose CLI 2.39.4: `config --quiet`; kubectl 1.34.1: offline Kustomize render; Ruff/compileall; wheel build с SQL schema. Registry manifests действительно получены, linux/amd64 подтверждён. Это не запуск контейнеров и не применение Kubernetes.

## Не проверено / оставшиеся внешние блокеры

- **Docker runtime и model capabilities проверены:** GitLab/Runner/CI/API/PostgreSQL, три gateway и LiteLLM работают. Worker запущен. Старые ошибки адреса `.155` и native JSON сохранены как история в artifacts и не описывают текущий режим prompt.
- Полный Analyst/Coder/Reviewer, MR с сгенерированными тестами, feedback/worker-CI restart и mutation acceptance проверяются на Java. Python/cloud acceptance не выполнены.
- Kubernetes deployment, физические Spark/SGLang/NVFP4 и live YouTrack **not_run**; YouTrack работает только как demo fixture.
- Собственные agent/proxy/CI images собраны; IDs и apt inventory сохранены в `artifacts/runtime-images.json` и `artifacts/runtime-packages.json`. Нагрузочное распределённое ограничение общей provider quota и production retention требуют дальнейшей эксплуатационной проверки.

Подробные команды, статусы A01–A22 и ссылки на артефакты — `VALIDATION.md`. Подробный запуск — `README.md`, перенос — `docs/MIGRATION.md`.
