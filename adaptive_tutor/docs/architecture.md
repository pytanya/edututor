# Adaptive Tutor — Архитектура

## Диаграмма агентного цикла (Mermaid)

```mermaid
flowchart TD
    A[User Input] --> B{Input Guard}
    B -->|Injection/Content| C[Reject with Error]
    B -->|OK| D[Region Router]

    D -->|REGION=RU| E[RouterAI Client]
    D -->|REGION=GLOBAL| F[OpenRouter Client]

    E --> G[Agent Loop - ReAct]
    F --> G

    G --> H[LLM Call - Planner]
    H --> I{Tool Selection}

    I -->|Search| J[Search Router]
    I -->|RAG| K[Vector Store Query]
    I -->|Generate Lesson| L[Lesson Generator]
    I -->|Evaluate| M[Knowledge Assessor]
    I -->|Final Answer| N[Response Formatter]

    J -->|RU| O[Yandex Search API]
    J -->|GLOBAL| P[Tavily Search API]
    J -->|Fallback| Q[DuckDuckGo]

    O --> R[Search Results]
    P --> R
    Q --> R

    R --> S[Context Enrichment]
    S --> G

    K --> T[Qdrant Vector Store]
    T --> U[RAG Context]
    U --> G

    L --> V[Lesson with LaTeX]
    V --> N

    M --> W[Student Knowledge Graph]
    W --> X[Adaptive Difficulty - LinUCB]
    X --> G

    N --> Y[Output Guard]
    Y -->|Valid| Z[User Response + Metadata]
    Y -->|Invalid| AA[Retry or Fallback]

    G --> AB[Circuit Breaker Check]
    AB -->|Open| AC[Fallback Model]
    AB -->|Closed| H

    G --> AD[Budget Guard]
    AD -->|Exceeded| AE[Stop with Error]
    AD -->|OK| H
```

## Фабрики

### LLMClientFactory (src/llm/base.py)
- `get_client(region)` — возвращает `RouterAIClient` (RU) или `OpenRouterClient` (Global).
- Каждому клиенту подключается провайдер-специфичный `TokenCounter`.

### TokenCounter (Strategy Pattern)
- `TokenUsage` — нормализованная модель токенов/стоимости.
- `RouterAITokenCounter` — извлекает `usage.cost` в **рублях**, курс RUB→USD.
- `OpenRouterTokenCounter` — извлекает `usage.cost` в **credits** (≈USD).
- Поля токенов извлекаются через безопасные get-цепочки, т.к. их расположение
  в JSON-ответе у провайдеров различается.

### SearchRouter (src/search/base.py)
- `get_engines(region)` — упорядоченный список поисковиков.
- RU: Yandex → DuckDuckGo. Global: Tavily → DuckDuckGo.
- `search()` — перебор движков с fallback при ошибке.