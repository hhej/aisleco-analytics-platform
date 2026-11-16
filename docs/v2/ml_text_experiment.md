# Text representation experiment (as of 2026-11-16)

Rule intents vs. rule intents + TF-IDF (character 2–4-grams of 39,792 masked inbound messages, SVD to 16 dimensions, averaged per inquiry over the messages before the cutoff). Same rolling-origin CV folds as the model; PR-AUC per fold.

| Fold | Without TF-IDF | With TF-IDF | Gain |
|---|---|---|---|
| 1 | 0.3317 | 0.3728 | +0.0411 |
| 2 | 0.2611 | 0.3351 | +0.0740 |
| 3 | 0.5436 | 0.4480 | -0.0955 |
| 4 | 0.3896 | 0.3785 | -0.0111 |
| 5 | 0.3067 | 0.2944 | -0.0123 |

Mean gain -0.0008. Rule: keep only if the mean gain is at least 0.01 and every fold gains. Decision: **drop TF-IDF; the transparent rule intents stay**.
