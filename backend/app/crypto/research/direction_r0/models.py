"""The three pre-registered classifiers (contract section 3). Imports sklearn (system Python)."""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 20261013
MODELS = ("LR", "RF", "GB")


def make(name: str, n_jobs: int = 4):
    if name == "LR":
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
                             StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    if name == "RF":
        return RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=200, max_features="sqrt",
                                      random_state=SEED, n_jobs=n_jobs)
    if name == "GB":
        return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_depth=3, min_samples_leaf=200,
                                              l2_regularization=1.0, early_stopping=False, random_state=SEED)
    raise ValueError(name)


def fit_predict(name: str, Xtr, ytr, Xte, n_jobs: int = 4) -> tuple[object, np.ndarray]:
    m = make(name, n_jobs)
    m.fit(Xtr, (ytr == 1).astype(int))
    return m, m.predict_proba(Xte)[:, 1]


def lr_coefficients(model, n_features: int) -> list[float]:
    return model[-1].coef_[0][:n_features].tolist()
