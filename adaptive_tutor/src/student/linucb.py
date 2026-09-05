"""LinUCB contextual bandit — советник сложности заданий (Этап 5).

Чистые функции без I/O; состояние — JSON-безопасный dict
`{"d", "alpha", "arms": [{"A": [[float]], "b": [float], "n": int}]}`.
Руки — уровни сложности easy/medium/hard; выбор — disjoint LinUCB
`p = x^T theta + alpha * sqrt(x^T A^-1 x)`; обновление `A += x x^T`,
`b += reward * x`. Советник НЕ принуждает модель (envelope.difficulty
остаётся за моделью).
"""

import numpy as np

DIFFICULTY_ORDER = ["easy", "medium", "hard"]
N_ARMS = 3
BANDIT_DIM = 4
DEFAULT_ALPHA = 0.6


def make_bandit(d: int = BANDIT_DIM, alpha: float = DEFAULT_ALPHA, n_arms: int = N_ARMS) -> dict:
    """Создаёт состояние LinUCB: A = I_d, b = 0, n = 0 для каждой руки."""
    return {
        "d": d,
        "alpha": float(alpha),
        "arms": [
            {
                "A": [[1.0 if i == j else 0.0 for j in range(d)] for i in range(d)],
                "b": [0.0] * d,
                "n": 0,
            }
            for _ in range(n_arms)
        ],
    }


def difficulty_arm(difficulty: str) -> int:
    """Индекс руки по сложности: easy=0, medium=1, hard=2; иначе 1."""
    try:
        return DIFFICULTY_ORDER.index(difficulty)
    except (ValueError, AttributeError):
        return 1


def arm_difficulty(arm: int) -> str:
    """Сложность по индексу руки с клампингом в валидный диапазон."""
    arm = int(arm)
    if arm < 0:
        arm = 0
    if arm >= N_ARMS:
        arm = N_ARMS - 1
    return DIFFICULTY_ORDER[arm]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def build_features(
    *,
    accuracy: float,
    attempts: int,
    fatigue: float,
    overall: float,
) -> list[float]:
    """Контекст решения d=4: [точность темы, attempts_norm, fatigue, общий уровень].

    Точность без попыток принимается 0.5; attempts нормируется на 10 попыток.
    """
    acc = 0.5 if int(attempts) <= 0 else _clamp01(accuracy)
    attempts_norm = min(1.0, int(attempts) / 10.0)
    return [acc, attempts_norm, _clamp01(fatigue), _clamp01(overall)]


def bandit_select(bandit: dict, features: list[float], current_arm: int = 1) -> int:
    """Выбирает руку по UCB; при равенстве (холодный старт) — текущую."""
    x = np.asarray(features, dtype=float)
    alpha = float(bandit.get("alpha", DEFAULT_ALPHA))
    best = int(current_arm)
    best_p = -1e18
    for idx, arm in enumerate(bandit["arms"]):
        a = np.asarray(arm["A"], dtype=float)
        b = np.asarray(arm["b"], dtype=float)
        a_inv = np.linalg.inv(a)
        theta = a_inv @ b
        p = float(x @ theta + alpha * float(np.sqrt(float(x @ a_inv @ x))))
        if p > best_p + 1e-9:
            best, best_p = idx, p
        elif abs(p - best_p) <= 1e-9 and idx == current_arm:
            best = idx
    return best


def bandit_update(bandit: dict, features: list[float], arm: int, reward: float) -> dict:
    """Обновляет сыгранную руку: A += x x^T, b += reward x, n += 1."""
    arm = int(arm)
    if arm < 0:
        arm = 0
    if arm >= len(bandit["arms"]):
        arm = len(bandit["arms"]) - 1
    x = np.asarray(features, dtype=float)
    armd = bandit["arms"][arm]
    a = np.asarray(armd["A"], dtype=float)
    b = np.asarray(armd["b"], dtype=float)
    a = a + np.outer(x, x)
    b = b + float(reward) * x
    armd["A"] = a.tolist()
    armd["b"] = b.tolist()
    armd["n"] += 1
    return bandit
