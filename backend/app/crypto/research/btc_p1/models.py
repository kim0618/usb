"""M0, M1 and M2, written in numpy because this repository has no ML dependency.

`requirements.txt` stops at numpy, pandas and pyarrow: scikit-learn, LightGBM, XGBoost and scipy
are neither installed nor declared. The contract forbids adding a large dependency, so the two
learned models are implemented here.

That trades one risk for another. A hand-written booster that is subtly wrong would report
NO_SIGNAL for a target that actually carries information, which is the expensive direction of
error in a study whose whole job is to decide whether to keep going. The mitigation is in the
tests: M2 has to recover a planted, learnable structure from synthetic data and beat both M0 and
M1 on it before any real result is reported.

Nothing here draws a random number. No subsampling, no shuffling, no random initialisation, so
the same inputs give the same model byte for byte.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

EPS = 1e-12
PROB_CLIP = 1e-6


def sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z, dtype=np.float64)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, PROB_CLIP, 1.0 - PROB_CLIP)
    return np.log(q / (1.0 - q))


# --- M0 ---------------------------------------------------------------------------------

@dataclass
class Baseline:
    """The unconditional frequency in the training window, as a constant probability."""

    rate: float = 0.5

    def fit(self, y: np.ndarray) -> "Baseline":
        self.rate = float(np.mean(y)) if len(y) else 0.5
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.full(len(x), self.rate)


# --- M1 ---------------------------------------------------------------------------------

@dataclass
class RobustScaler:
    """Median and MAD from the training slice only. Never refit on validation."""

    center: np.ndarray | None = None
    scale: np.ndarray | None = None

    def fit(self, x: np.ndarray) -> "RobustScaler":
        self.center = np.median(x, axis=0)
        mad = np.median(np.abs(x - self.center), axis=0) * 1.4826
        # A column that is constant in training carries no information; dividing by 1 leaves it
        # centred at zero rather than exploding.
        self.scale = np.where(mad > 1e-9, mad, 1.0)
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.center) / self.scale
        return np.clip(z, -10.0, 10.0)


@dataclass
class RidgeLogistic:
    """L2-penalised logistic regression solved by IRLS. The intercept is not penalised."""

    l2: float = 1.0
    iters: int = 30
    tol: float = 1e-8
    coef: np.ndarray | None = None
    scaler: RobustScaler = field(default_factory=RobustScaler)
    #: Off when the inputs are already on a meaningful scale. The scaler clips at 10 MADs, which
    #: is right for raw features and wrong for a calibration fit on logits, where the clip would
    #: flatten the tails and bias the slope toward 1.
    scale_inputs: bool = True

    def _prepare(self, x: np.ndarray) -> np.ndarray:
        return self.scaler.transform(x) if self.scale_inputs else x

    def fit(self, x: np.ndarray, y: np.ndarray) -> "RidgeLogistic":
        if self.scale_inputs:
            self.scaler.fit(x)
        z = self._prepare(x)
        n, d = z.shape
        design = np.column_stack([np.ones(n), z])
        penalty = np.eye(d + 1) * self.l2
        penalty[0, 0] = 0.0

        w = np.zeros(d + 1)
        w[0] = logit(np.array([np.clip(np.mean(y), PROB_CLIP, 1 - PROB_CLIP)]))[0]
        for _ in range(self.iters):
            p = sigmoid(design @ w)
            # Weights collapse toward zero for confident predictions; the floor keeps the normal
            # equations solvable for rare targets.
            weights = np.maximum(p * (1.0 - p), 1e-6)
            working = design @ w + (y - p) / weights
            lhs = design.T @ (design * weights[:, None]) + penalty
            rhs = design.T @ (weights * working)
            try:
                new = np.linalg.solve(lhs, rhs)
            except np.linalg.LinAlgError:
                new = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
            step = float(np.max(np.abs(new - w)))
            w = new
            if step < self.tol:
                break
        self.coef = w
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        design = np.column_stack([np.ones(len(x)), self._prepare(x)])
        return sigmoid(design @ self.coef)


# --- M2 ---------------------------------------------------------------------------------

@dataclass
class Tree:
    feature: np.ndarray
    threshold: np.ndarray       # split on binned value <= threshold
    left: np.ndarray
    right: np.ndarray
    value: np.ndarray
    is_leaf: np.ndarray

    def predict(self, binned: np.ndarray) -> np.ndarray:
        node = np.zeros(len(binned), dtype=np.int64)
        for _ in range(64):
            active = ~self.is_leaf[node]
            if not active.any():
                break
            idx = np.flatnonzero(active)
            here = node[idx]
            values = binned[idx, self.feature[here]]
            go_left = values <= self.threshold[here]
            node[idx] = np.where(go_left, self.left[here], self.right[here])
        return self.value[node]


class _Builder:
    """One depth-limited regression tree over pre-binned features."""

    def __init__(self, n_bins: int, max_depth: int, min_samples_leaf: int, leaf_l2: float):
        self.n_bins = n_bins
        self.max_depth = max_depth
        self.min_leaf = min_samples_leaf
        self.l2 = leaf_l2
        self.feature: list[int] = []
        self.threshold: list[int] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.value: list[float] = []
        self.is_leaf: list[bool] = []

    def _new_node(self) -> int:
        self.feature.append(0)
        self.threshold.append(0)
        self.left.append(0)
        self.right.append(0)
        self.value.append(0.0)
        self.is_leaf.append(True)
        return len(self.value) - 1

    def _leaf_value(self, g: np.ndarray, h: np.ndarray) -> float:
        return float(np.clip(-g.sum() / (h.sum() + self.l2), -10.0, 10.0))

    def _histograms(self, binned: np.ndarray, rows: np.ndarray, g: np.ndarray,
                    h: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Sum gradient, hessian and count per (feature, bin) in three bincount calls."""
        n_features = binned.shape[1]
        codes = binned[rows] + (np.arange(n_features, dtype=np.int32) * self.n_bins)
        flat = codes.ravel()
        size = n_features * self.n_bins
        weights_g = np.repeat(g, n_features)
        weights_h = np.repeat(h, n_features)
        hist_g = np.bincount(flat, weights=weights_g, minlength=size).reshape(n_features, self.n_bins)
        hist_h = np.bincount(flat, weights=weights_h, minlength=size).reshape(n_features, self.n_bins)
        hist_c = np.bincount(flat, minlength=size).reshape(n_features, self.n_bins)
        return hist_g, hist_h, hist_c

    def _best_split(self, hist_g, hist_h, hist_c) -> tuple[float, int, int]:
        cum_g = np.cumsum(hist_g, axis=1)
        cum_h = np.cumsum(hist_h, axis=1)
        cum_c = np.cumsum(hist_c, axis=1)
        total_g, total_h = cum_g[:, -1:], cum_h[:, -1:]
        total_c = cum_c[:, -1:]

        left_g, left_h, left_c = cum_g[:, :-1], cum_h[:, :-1], cum_c[:, :-1]
        right_g, right_h = total_g - left_g, total_h - left_h
        right_c = total_c - left_c

        parent = (total_g ** 2) / (total_h + self.l2)
        gain = (left_g ** 2) / (left_h + self.l2) + (right_g ** 2) / (right_h + self.l2) - parent
        gain = np.where((left_c >= self.min_leaf) & (right_c >= self.min_leaf), gain, -np.inf)
        if not np.isfinite(gain).any():
            return -np.inf, -1, -1
        flat = int(np.argmax(gain))
        feature, threshold = divmod(flat, gain.shape[1])
        return float(gain[feature, threshold]), feature, threshold

    def build(self, binned: np.ndarray, g: np.ndarray, h: np.ndarray) -> Tree:
        root = self._new_node()
        stack: list[tuple[int, np.ndarray, int]] = [(root, np.arange(len(g), dtype=np.int64), 0)]
        while stack:
            node, rows, depth = stack.pop()
            self.value[node] = self._leaf_value(g[rows], h[rows])
            if depth >= self.max_depth or len(rows) < 2 * self.min_leaf:
                continue
            hist_g, hist_h, hist_c = self._histograms(binned, rows, g[rows], h[rows])
            gain, feature, threshold = self._best_split(hist_g, hist_h, hist_c)
            if gain <= 0.0 or feature < 0:
                continue
            go_left = binned[rows, feature] <= threshold
            left_rows, right_rows = rows[go_left], rows[~go_left]
            if len(left_rows) < self.min_leaf or len(right_rows) < self.min_leaf:
                continue
            left, right = self._new_node(), self._new_node()
            self.feature[node], self.threshold[node] = feature, threshold
            self.left[node], self.right[node] = left, right
            self.is_leaf[node] = False
            stack.append((left, left_rows, depth + 1))
            stack.append((right, right_rows, depth + 1))
        return Tree(np.array(self.feature, dtype=np.int64),
                    np.array(self.threshold, dtype=np.int64),
                    np.array(self.left, dtype=np.int64),
                    np.array(self.right, dtype=np.int64),
                    np.array(self.value, dtype=np.float64),
                    np.array(self.is_leaf, dtype=bool))


@dataclass
class GradientBoosting:
    """Histogram gradient boosting for binary log loss. Single fixed config, no search."""

    n_trees: int = 200
    max_depth: int = 3
    learning_rate: float = 0.05
    min_samples_leaf: int = 200
    n_bins: int = 32
    leaf_l2: float = 1.0
    edges: np.ndarray | None = None
    base_score: float = 0.0
    trees: list[Tree] = field(default_factory=list)

    def _fit_bins(self, x: np.ndarray) -> None:
        # Quantile edges from the training slice only. Duplicate edges for a near-constant column
        # simply leave some bins empty, which the split search ignores.
        qs = np.linspace(0.0, 1.0, self.n_bins + 1)[1:-1]
        self.edges = np.quantile(x, qs, axis=0).T.copy()

    def _bin(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape, dtype=np.int32)
        for j in range(x.shape[1]):
            out[:, j] = np.searchsorted(self.edges[j], x[:, j], side="left")
        return np.clip(out, 0, self.n_bins - 1)

    def fit(self, x: np.ndarray, y: np.ndarray) -> "GradientBoosting":
        self._fit_bins(x)
        binned = self._bin(x)
        rate = float(np.clip(np.mean(y), PROB_CLIP, 1 - PROB_CLIP))
        self.base_score = float(np.log(rate / (1 - rate)))
        score = np.full(len(y), self.base_score)
        self.trees = []
        for _ in range(self.n_trees):
            p = sigmoid(score)
            g = p - y
            h = np.maximum(p * (1.0 - p), 1e-6)
            builder = _Builder(self.n_bins, self.max_depth, self.min_samples_leaf, self.leaf_l2)
            tree = builder.build(binned, g, h)
            score += self.learning_rate * tree.predict(binned)
            self.trees.append(tree)
        return self

    def raw_score(self, x: np.ndarray) -> np.ndarray:
        binned = self._bin(x)
        score = np.full(len(x), self.base_score)
        for tree in self.trees:
            score += self.learning_rate * tree.predict(binned)
        return score

    def predict(self, x: np.ndarray) -> np.ndarray:
        return sigmoid(self.raw_score(x))


# --- calibration ------------------------------------------------------------------------

@dataclass
class Isotonic:
    """Pool-adjacent-violators, fit on the held-out calibration slice of the training window."""

    x: np.ndarray | None = None
    y: np.ndarray | None = None

    def fit(self, scores: np.ndarray, targets: np.ndarray) -> "Isotonic":
        if len(scores) == 0:
            self.x, self.y = np.empty(0), np.empty(0)
            return self
        order = np.argsort(scores, kind="stable")
        s, t = scores[order].astype(np.float64), targets[order].astype(np.float64)

        # Pool adjacent violators, held on a stack so each block is merged once: appending and
        # popping the tail is amortised linear, where deleting from the middle of a list is not.
        level: list[float] = []
        weight: list[float] = []
        right: list[int] = []           # last input index of each block
        for i in range(len(t)):
            level.append(t[i])
            weight.append(1.0)
            right.append(i)
            while len(level) > 1 and level[-2] > level[-1] + EPS:
                w = weight[-2] + weight[-1]
                level[-2] = (level[-2] * weight[-2] + level[-1] * weight[-1]) / w
                weight[-2] = w
                right[-2] = right[-1]
                level.pop()
                weight.pop()
                right.pop()

        # One knot per block, placed at the block's largest score. Tied scores would break
        # np.interp, so equal knots collapse to the last (highest) fitted value, keeping the
        # mapping monotone.
        knots_x = np.array([s[r] for r in right])
        knots_y = np.clip(np.array(level), 0.0, 1.0)
        keep = np.concatenate((knots_x[1:] > knots_x[:-1], [True]))
        self.x, self.y = knots_x[keep], knots_y[keep]
        return self

    def transform(self, scores: np.ndarray) -> np.ndarray:
        if self.x is None or len(self.x) == 0:
            return scores
        if len(self.x) == 1:
            return np.full(len(scores), float(self.y[0]))
        return np.interp(scores, self.x, self.y, left=float(self.y[0]), right=float(self.y[-1]))
