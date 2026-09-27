# Модели и runtime — контекст предыдущего исследования

Дата обсуждения: 27.09.2026. Это фиксация предыдущей рекомендации ассистента, **не новый аудит источников и не результаты тестирования на железе пользователя**. Перед установкой нужно повторно открыть официальные карточки, проверить существование конкретных revision, лицензию, архитектуру, образ runtime и совместимость.

## Рекомендация в переписке

- Основной кандидат: `nvidia/Qwen3.8-27B-NVFP4`, Qwen3.8-27B, смешанное NVFP4/FP8.
- Конкурент для измерения качества: `Qwen/Qwen3.8-Flash-Next`; сложнее размещение, отдельная Community License.
- Стартовая схема: одинаковые экземпляры основной модели на трёх независимых Spark, роли не закреплять за физическим узлом.
- Runtime: SGLang предложен вместо первоначального vLLM; окончательно подтвердить запуском. LiteLLM и LangGraph от этого выбора не меняют назначение.
- Указанные в исследовании результаты SWE/agentic benchmarks не доказывают качество unit-тестов. Скорости community-конфигураций не являются гарантией для выбранного NVIDIA checkpoint.
- Облачная модель для локального стенда ещё не выбрана. Не подставлять молча облачного провайдера по названию будущей локальной модели.

## Первичные источники для перепроверки

| Тема | URL |
|---|---|
| Базовая модель | https://huggingface.co/Qwen/Qwen3.8-27B |
| NVIDIA checkpoint | https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4 |
| SGLang, модель и Spark | https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B |
| vLLM recipe | https://recipes.vllm.ai/Qwen/Qwen3.8-27B |
| Flash-Next | https://huggingface.co/Qwen/Qwen3.8-Flash-Next |
| Её лицензия | https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE |
| Независимое сравнение | https://artificialanalysis.ai/models/comparisons/qwen3-8-flash-next-vs-qwen3-8-27b |
| Измерения автора на Spark | https://github.com/kosztyua/Qwen3.8-27B-DGX-Spark |
| Альтернативный baseline NVIDIA | https://build.nvidia.com/spark/vllm/agent-ready-models |
| Оценка генерации тестов | https://github.com/facebookresearch/testgeneval |

## Что проверить до миграции

Реальные tool calls и JSON contracts, форматы reasoning/tool parser, длинные контексты, уровень параллелизма, память ОС/KV/состояний модели, поведение после отказа endpoint, реальные отчёты unit-тестов. Не копировать multi-GPU launch flags из документации GB300 на один Spark. Не обещать суммарные 384 ГБ как память одной модели при независимых серверах.

Исходный ориентир 32–64K рабочего контекста / до 128K после проверки и 1–2 запроса на сервер — предложение для старта испытаний. Фактические лимиты установить измерениями.

## Как выбирать по результатам

Позднее использовать 50–100 исторических MR разных продуктов с одинаковыми инструментами, бюджетом и закрытой проверочной выборкой. Измерять принятие разработчиком без правок, обнаружение дефектов/mutation score, meaningful assertions, coverage без обходов, длительность полного цикла, число возвратов и ошибочные одобрения Reviewer. Зелёный CI и tokens/s по отдельности недостаточны.

## Документация компонентов

- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- PostgreSQL checkpoints: https://reference.langchain.com/python/langgraph/checkpoint/postgres
- LiteLLM routing: https://docs.litellm.ai/docs/routing
- GitLab Runner registration: https://docs.gitlab.com/runner/register/
- GitLab API: https://docs.gitlab.com/api/

Все версии и API проверить в момент реализации и закрепить проверенные зависимости, а не использовать плавающий latest в итоговой воспроизводимой конфигурации.
