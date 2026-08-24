"""NDC-backed drug name mapping and shortage entity resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

try:
    from .normalizers import (
        normalize_drug_name,
        normalize_manufacturer,
        normalize_ndc,
        product_ndc_from_package,
    )
except ImportError:  # Allow direct execution from src/cleaning.
    from normalizers import (
        normalize_drug_name,
        normalize_manufacturer,
        normalize_ndc,
        product_ndc_from_package,
    )


Record = dict[str, Any]


def shortage_context_fields(shortage: Record) -> dict[str, str | None]:
    """Keep FDA reasons nullable and classify related_info without treating it as a reason."""
    status = shortage.get("status")
    related_info = shortage.get("related_info") or None
    return {
        "shortage_reason": shortage.get("shortage_reason") or None,
        "operational_context": related_info if status == "Current" else None,
        "discontinuation_context": related_info if status == "To Be Discontinued" else None,
    }


@dataclass(frozen=True)
class DrugIdentity:
    """Canonical drug names sourced only from an NDC Directory record."""

    product_id: str | None
    product_ndc: str | None
    generic_name: str | None
    brand_name: str | None
    manufacturer_name: str | None
    normalized_manufacturer: str | None
    active_ingredients: tuple[str, ...]


@dataclass(frozen=True)
class NDCResolution:
    method: str
    lookup_ndc: str | None
    candidate_count: int
    identity: DrugIdentity | None


class NDCNameMapper:
    """Resolve package/product NDCs to canonical NDC Directory names."""

    def __init__(self) -> None:
        self.package_index: dict[str, list[DrugIdentity]] = {}
        self.product_index: dict[str, list[DrugIdentity]] = {}

    @classmethod
    def from_records(cls, records: Iterable[Record]) -> "NDCNameMapper":
        mapper = cls()
        for record in records:
            identity = DrugIdentity(
                product_id=record.get("product_id"),
                product_ndc=normalize_ndc(record.get("product_ndc")),
                generic_name=record.get("generic_name"),
                brand_name=record.get("brand_name"),
                manufacturer_name=record.get("labeler_name"),
                normalized_manufacturer=normalize_manufacturer(record.get("labeler_name")),
                active_ingredients=tuple(
                    ingredient["name"]
                    for ingredient in record.get("active_ingredients") or []
                    if isinstance(ingredient, dict) and ingredient.get("name")
                ),
            )
            if identity.product_ndc:
                mapper.product_index.setdefault(identity.product_ndc, []).append(identity)
            for package in record.get("packaging") or []:
                if not isinstance(package, dict):
                    continue
                package_ndc = normalize_ndc(package.get("package_ndc"))
                if package_ndc:
                    mapper.package_index.setdefault(package_ndc, []).append(identity)
        return mapper

    @staticmethod
    def _choose(candidates: list[DrugIdentity]) -> DrugIdentity | None:
        values = list(dict.fromkeys(candidates))
        if not values:
            return None
        if len(values) == 1:
            return values[0]

        fingerprints = {
            (
                normalize_drug_name(candidate.generic_name),
                candidate.normalized_manufacturer,
            )
            for candidate in values
        }
        if len(fingerprints) != 1:
            return None
        return max(
            values,
            key=lambda candidate: (
                bool(candidate.brand_name),
                len(candidate.active_ingredients),
                candidate.product_id or "",
            ),
        )

    def _resolution(self, method: str, key: str, candidates: list[DrugIdentity]) -> NDCResolution:
        identity = self._choose(candidates)
        resolved_method = method if identity else f"ambiguous_{method}"
        return NDCResolution(resolved_method, key, len(candidates), identity)

    def resolve_shortage(self, shortage: Record) -> NDCResolution:
        package_ndc = normalize_ndc(shortage.get("package_ndc"))
        if package_ndc and package_ndc in self.package_index:
            return self._resolution("package_ndc", package_ndc, self.package_index[package_ndc])

        product_ndc = product_ndc_from_package(shortage.get("package_ndc"))
        if product_ndc and product_ndc in self.product_index:
            return self._resolution("product_ndc", product_ndc, self.product_index[product_ndc])

        openfda = shortage.get("openfda") or {}
        for field, index, method in (
            ("package_ndc", self.package_index, "openfda_package_ndc"),
            ("product_ndc", self.product_index, "openfda_product_ndc"),
        ):
            values = openfda.get(field) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                key = normalize_ndc(value)
                if key and key in index:
                    return self._resolution(method, key, index[key])

        return NDCResolution("unmatched", package_ndc, 0, None)
