# API, зависимости и образы: проверка 27.09.2026

## Официальные контракты

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [PostgresSaver](https://reference.langchain.com/python/langgraph/checkpoint.postgres): thread_id, setup/migrations, durable checkpoints. Дополнительно реально выполнен restart-тест с PostgreSQL 17.6 и закреплёнными Python packages.
- [GitLab commits](https://docs.gitlab.com/api/commits/): commit с actions/start_sha в новой ветке. [Pipelines](https://docs.gitlab.com/api/pipelines/): поиск pipeline по ref/SHA; работающий pipeline не создаётся заново.
- [Emoji reactions](https://docs.gitlab.com/api/emoji_reactions/): award_emoji API для MR, стабильный ID и user.id. Старый URL документации `/api/award_emoji/` при открытии давал redirect/error; актуальная страница подтверждает используемые API paths.
- [Runner workflow](https://docs.gitlab.com/ci/runners/new_creation_workflow/), [registration](https://docs.gitlab.com/runner/register/): authentication token нового workflow. Bootstrap создаёт project runner через API, записывает его token в закрытый volume конфигурации manager; legacy registration token не используется.
- [GitLab Docker install](https://docs.gitlab.com/install/docker/installation/) и [patch 19.4.1](https://docs.gitlab.com/releases/patches/patch-release-gitlab-19-4-1-released/): выбран CE 19.4.1, Runner 19.4.0. Docker установлен пользователем; GitLab healthy, bootstrap и реальные Runner jobs прошли.
- [Monitoring IP allowlist](https://docs.gitlab.com/administration/monitoring/ip_allowlist/) и [health checks](https://docs.gitlab.com/administration/monitoring/health_check/): default разрешает localhost контейнера, запрос с Windows через Docker NAT может получить 404. Реально подтверждено: внутренний readiness 200, внешний 404, sign-in 200. Bootstrap ждёт Compose healthcheck и проверяет внешнюю страницу входа, не расширяя allowlist.
- [LiteLLM config](https://docs.litellm.ai/docs/proxy/configs), [routing](https://docs.litellm.ai/docs/routing): три deployment одного model alias, API bases и ограниченные retries. Реальная библиотека 1.102.1 проверена через три HTTP test doubles, один отвечает 503. Это не облачные вызовы.
- [NVIDIA model card](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4) и [SGLang recipe](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B) доступны при повторной проверке. SGLang описывает отдельный DGX Spark и NVFP4, но локальный Spark/runtime/weights не развёрнуты и не измерялись.

## Воспроизводимость

`uv.lock` фиксирует 142 Python packages с hashes, включая optional proxy group. Проверенные верхнеуровневые версии в `pyproject.toml`; данные PyPI получены по официальному JSON API при реализации. Proxy собран из этого lock, отдельный плавающий LiteLLM image не используется.

`config/images.lock.json` содержит digest официальных manifests, теги и platform descriptors, полученные напрямую Docker Registry API; `config/images.env` использует `tag@sha256`. Теперь подтверждён и запуск Docker GitLab/Runner/PostgreSQL. Agent/proxy/CI images реально собраны, image IDs и apt inventory сохранены в `artifacts/runtime-images.json` и `artifacts/runtime-packages.json`. Перед Kubernetes deployment отправить проверенные images в разрешённый registry и закрепить digest.

Локальная PostgreSQL 17.6 взята из portable EDB binaries, Maven 3.9.11 — Maven Central. Они расположены только в `.tools`, системные переменные и службы не изменялись. SHA256 загрузок в `artifacts/tool-downloads.json`. Локальная Java 18.0.2.1 уже была на хосте; Docker CI image использует Temurin 17, реальные Java/Python jobs выполнены. Различие JDK отмечено в VALIDATION.md.

28.09 пользователь выбрал **локальный MTPLX**: OpenAPI сервера сообщает `MTPLX OpenAI-compatible server 0.1.0`, `/models` возвращает `mtplx-qwen36-27b-optimized-quality`. Реальные проверки JSON/tool/generation зафиксированы в `artifacts/model-capabilities.json`: JSON mode требует отсутствующий llguidance, generation probes завершаются timeout. Это конкретный внешний блокер; совместимость и облачная приёмка не объявляются пройденными.
