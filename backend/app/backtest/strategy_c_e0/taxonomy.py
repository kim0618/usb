"""Form type and 8-K item number -> event class. No text, no sentiment, no LLM.

Every list comes from `c_e0_event_taxonomy_v1.json`; this module holds no form code of its own.
A form that matches nothing is ROUTINE_IGNORED: it never creates an event and never removes a
candidate from M_ONLY. Amendments (`/A`) create no event at all, except on the MA pin list.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

#: Dedup classes. E3a and E3b merge into E3 (taxonomy `deduplication.e3_merge`); the E4
#: subtypes stay separate keys, as the multi-item example in the taxonomy shows.
MATERIAL_CLASSES = ("E1", "E2", "E3", "E5")
NEGATIVE_RISK_CLASSES = ("E4a", "E4b", "E4c", "E4d")
ITEM_PATTERN = re.compile(r"\d+\.\d+")


@dataclass(frozen=True)
class Classification:
    """What one filing is, before any window or dedup rule is applied."""

    classes: tuple[str, ...]  # dedup classes, e.g. ("E1", "E4a")
    subtypes: tuple[str, ...]  # reporting granularity, e.g. ("E3a",)
    ma_pin: bool = False
    unclassifiable: bool = False
    unknown_items: bool = False
    amendment: bool = False

    @property
    def material(self) -> tuple[str, ...]:
        return tuple(c for c in self.classes if c in MATERIAL_CLASSES)

    @property
    def negative_risk(self) -> tuple[str, ...]:
        return tuple(c for c in self.classes if c in NEGATIVE_RISK_CLASSES)

    @property
    def routine(self) -> bool:
        return not (self.classes or self.ma_pin or self.unclassifiable or self.unknown_items)


def _norm_form(form: str) -> str:
    return " ".join(str(form).upper().split())


def parse_items(items: str | None) -> tuple[str, ...]:
    """8-K item numbers as filed ('1.01,2.03,9.01'); anything else yields no item."""
    return tuple(dict.fromkeys(ITEM_PATTERN.findall(items or "")))


class Taxonomy:
    """The frozen class table, plus the optional form-alias addendum."""

    def __init__(self, taxonomy: Mapping[str, Any], addendum: Mapping[str, Any] | None = None) -> None:
        self._raw = taxonomy
        classes = taxonomy["classes"]
        self._item_rules: list[tuple[str, str, frozenset[str], frozenset[str]]] = []
        self._form_rules: list[tuple[str, str, frozenset[str]]] = []
        for name, body in classes.items():
            key = name.split("_")[0]  # E1_MATERIAL_AGREEMENT -> E1
            for subtype, spec in (body.get("subtypes") or {name: body.get("match")}).items():
                if spec is None:
                    continue
                label = subtype if subtype != name else key
                dedup = key if key != "E4" else label.split("_")[0]
                forms = frozenset(_norm_form(f) for f in spec.get("forms", ()))
                items = frozenset(str(i) for i in spec.get("items_any", ()))
                short = label if label.startswith("E") and "_" not in label else label.split("_")[0]
                if items:
                    self._item_rules.append((dedup, short, forms, items))
                else:
                    self._form_rules.append((dedup, short, forms))
        flags = taxonomy["flags_outside_E_classes"]
        self._ma_forms = frozenset(_norm_form(f) for f in flags["MA_TARGET_PIN"]["forms"])
        self._unclassifiable = frozenset(_norm_form(f) for f in flags["UNCLASSIFIABLE_FORMS"]["forms"])
        self._aliases = {_norm_form(a["new_form"]): _norm_form(a["same_form_as"])
                         for a in ((addendum or {}).get("aliases") or ())}

    @property
    def known_forms(self) -> frozenset[str]:
        """Every form the class table can act on. Used by the alias audit, not by the join."""
        listed = {f for _, _, forms, _ in self._item_rules for f in forms}
        listed |= {f for _, _, forms in self._form_rules for f in forms}
        return frozenset(listed | self._ma_forms | self._unclassifiable)

    def resolve_alias(self, form: str) -> str:
        norm = _norm_form(form)
        return self._aliases.get(norm, norm)

    def classify(self, form: str, items: str | None = None) -> Classification:
        norm = self.resolve_alias(form)
        if norm in self._ma_forms:  # /A counts here, and only here
            return Classification((), (), ma_pin=True, amendment=norm.endswith("/A"))
        if norm.endswith("/A"):
            return Classification((), (), amendment=True)
        if norm in self._unclassifiable:
            return Classification((), (), unclassifiable=True)
        parsed = parse_items(items)
        dedup: list[str] = []
        subtypes: list[str] = []
        for key, label, forms, wanted in self._item_rules:
            if norm in forms and wanted & set(parsed):
                dedup.append(key)
                subtypes.append(label)
        for key, label, forms in self._form_rules:
            if norm in forms:
                dedup.append(key)
                subtypes.append(label)
        unknown_items = bool(not parsed and any(norm in forms for _, _, forms, _ in self._item_rules))
        return Classification(tuple(dict.fromkeys(dedup)), tuple(dict.fromkeys(subtypes)),
                              unknown_items=unknown_items and not dedup)
