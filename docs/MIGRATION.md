# Перенос после локальной приёмки

Файлы `deploy/kubernetes` — подготовленный baseline, на настоящем Kubernetes не проверен. Deployment agent содержит API и worker в двух контейнерах; второй Deployment — LiteLLM. Бизнес-граф, роли, GitLab API и O1–O9 остаются теми же.

1. Завершить A01–A21 на локальном GitLab и облаке. Сохранить реально исполнявшиеся image IDs, `uv.lock`, версии/артефакты Runner. Собрать свои agent/proxy images, отправить в разрешённый registry и указать digest вместо `registry.example.invalid` через Kustomize images.
2. Подготовить внешнюю PostgreSQL 17+ с отдельной БД/учётной записью, TLS и резервным копированием. Сначала восстановить копию БД стенда и проверить миграции/восстановление checkpoint. В production использовать выдаваемые администратором права; runtime не должен быть superuser. Миграции запускает initContainer `agent_lab.migrate`; после миграций DDL-права можно отозвать и убрать повторную setup из startup в согласованной эксплуатационной конфигурации.
3. Создать `agent-secrets` через secret manager: `DATABASE_URL` (включая `sslmode=verify-full` и доступный CA), `GITLAB_URL`, `GITLAB_PUBLIC_URL`, project-scoped `GITLAB_TOKEN`, `API_TOKEN`, `WEBHOOK_SECRET`, `MODEL_API_KEY`, `MODEL_BASE_URL=http://litellm:4000/v1`, лимиты. Корневого GitLab-токена там нет. При частном CA добавить secret volume с сертификатом в init/API/worker; не отключать проверку TLS.
4. В `projects.yaml` явно добавить одобренные product IDs, config hashes, пути тестов, thresholds, task adapter. В поставке задача — **demo fixture**, live YouTrack пока не реализован. Для другого стека зарегистрировать policy и report adapter, добавить его проверенные argv/build config. Не копировать bootstrap в корпоративный GitLab: он ограничен localhost и создаёт только синтетику.
5. В `model-secrets` задать `SPARK_MODEL=openai/<served-model-name>`, `SPARK_1_BASE`, `SPARK_2_BASE`, `SPARK_3_BASE` с `/v1`, `SPARK_API_KEY`, `MODEL_API_KEY`. В `agent-secrets` задать `MODEL_MODE=local`, `TOKEN_PRICE_UPPER_USD=0` (token/call/time limits сохраняются). Развёртывание cloud/local отличается конфигурацией и deployment. Шлюзы cloud в target не нужны.
6. Проверить `kubectl kustomize deploy/kubernetes`, затем применить в выделенном namespace. Настроить HTTPS ingress для webhook и API, с отдельным доступом к admin API. NetworkPolicy: API/worker → DNS, внешний PostgreSQL, GitLab; worker → LiteLLM; LiteLLM → только три Spark. CIDR/namespace selectors требуют реальных адресов среды и намеренно не выдуманы.
7. Использовать существующие изолированные Runner и согласованные CI images. Учебный DinD переносить нельзя как производственную границу безопасности. Проверить два продукта, source SHA update, закрытый MR, отзыв автора, SIGTERM/SIGKILL worker и outage одного endpoint. Реплики worker допускаются после нагрузочной проверки: PostgreSQL session advisory lock исключает двух writer для одного project/MR.

## Три независимых Spark

Каждый Spark загружает отдельную копию модели. 3 × 128 ГБ не образуют одну память 384 ГБ. Роли не закреплены за устройствами. SGLang остаётся предлагаемым runtime: доступа к Spark в этой сессии нет.

Перед запуском закрепить image digest и HF revision, проверить лицензию, ARM64/GB10, NVFP4/FP8 kernels, parser/tool calls/JSON, память ОС + weights + KV cache. Начать с одного запроса на узел, измерить контекст 32K, затем расширять. Не копировать multi-GPU flags или цифры tokens/s с чужой конфигурации. Должны пройти те же structured-output/tool-call probes, role flows и отключение одного узла. Производительность, throughput и выбор модели подтверждаются отдельным измерением на железе.

## Долговечность и retention

DB содержит очередь, checkpoints, immutable версии, intents, event IDs, usage и ссылки. GitLab artifacts хранят JUnit/coverage/logs 30 дней в demo. Для production согласовать больший срок/архивирование прежде, чем удалять artifacts активных runs. Локальные workspaces восстанавливаются из SHA; `emptyDir` допустим, это не источник истины. Не удалять checkpoints/actions/feedback активных процессов. Встроенного автоматического retention cleanup пока нет.
