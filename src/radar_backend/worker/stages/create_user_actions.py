from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from time import perf_counter
from typing import TypeVar, TypedDict

from radar_backend.db.connection import (
    acquire_connection,
    acquire_connection_with_transaction,
)
from radar_backend.db.repositories import (
    email_deliveries_repository,
    notification_recipients_repository,
    policy_impacts_repository,
    policy_updates_repository,
    product_match_repository,
    user_actions_repository,
    webhook_events_repository,
)
from radar_backend.domain import (
    ActionItem,
    ActionItemStatus,
    ActionType,
    AffectedProduct,
    AttemptExhaustedPayload,
    EmailActionSummary,
    EmailAffectedProduct,
    EmailDeliveryPayload,
    PolicyImpactModel,
    PolicyImpactType,
    PolicyUpdateModel,
    ProductCandidate,
    ProductImportedCoo,
    TariffCalculationCoo,
    WebhookEntityType,
    WebhookEventType,
)
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult

logger = logging.getLogger(__name__)

_ACTION_TYPE_ORDER = (
    ActionType.RECLASSIFY_PRODUCT,
    ActionType.RECALCULATE_TARIFF,
)
_ACTION_CALCULATE_MAX_ATTEMPT_COUNT = 3
_VALID_HTS_PREFIX_LENGTHS = {2, 4, 6, 8, 10}
_CooRow = TypeVar("_CooRow", TariffCalculationCoo, ProductImportedCoo)


class ActionCalculationError(RuntimeError):
    pass


class ProductMatchData(TypedDict):
    product_candidates: list[ProductCandidate]
    calculation_coos_by_product_uid: dict[str, list[TariffCalculationCoo]]
    imported_coos_by_product_uid: dict[str, list[ProductImportedCoo]]


@dataclass(frozen=True)
class NormalizedImpact:
    id: int
    hts_prefix: str
    impacted_type: PolicyImpactType
    action_types: tuple[ActionType, ...]
    effective_date: date | None
    coos: frozenset[str]


@dataclass(frozen=True)
class ProductImpactMatch:
    action_types: tuple[ActionType, ...]
    effective_date: date | None


@dataclass(frozen=True)
class UserActionCandidate:
    user_id: int
    affected_products: list[AffectedProduct]
    action_items: list[ActionItem]
    email_payload: EmailDeliveryPayload


@dataclass(frozen=True)
class _MatchedCandidate:
    hts_code: str
    hts_code_normalized: str
    candidate_rank: int | None


@dataclass
class _ProductActionAccumulator:
    product_uid: str
    product_name: str
    matched_candidates: list[_MatchedCandidate] = field(default_factory=list)
    matched_candidate_keys: set[tuple[str, str, int | None]] = field(default_factory=set)
    suggested_actions: set[ActionType] = field(default_factory=set)

    def add_candidate(self, candidate: ProductCandidate) -> None:
        key = (
            candidate["hts_code_normalized"],
            candidate["hts_code"],
            candidate["candidate_rank"],
        )
        if key in self.matched_candidate_keys:
            return

        self.matched_candidate_keys.add(key)
        self.matched_candidates.append(
            _MatchedCandidate(
                hts_code=candidate["hts_code"],
                hts_code_normalized=candidate["hts_code_normalized"],
                candidate_rank=candidate["candidate_rank"],
            )
        )

    def display_hts_code(self) -> str:
        if not self.matched_candidates:
            raise ActionCalculationError(
                f"matched product has no candidate: product_uid={self.product_uid}"
            )

        candidate = sorted(
            self.matched_candidates,
            key=lambda item: (
                item.candidate_rank is None,
                item.candidate_rank if item.candidate_rank is not None else 0,
                item.hts_code_normalized,
            ),
        )[0]
        return candidate.hts_code


class UserActionBuilder:
    def __init__(self, *, user_id: int, account_owner_email: str | None) -> None:
        self._user_id = user_id
        self._account_owner_email = account_owner_email
        self._products: dict[str, _ProductActionAccumulator] = {}
        self._product_order: list[str] = []
        self._product_uids_by_action: dict[ActionType, set[str]] = {
            action_type: set() for action_type in _ACTION_TYPE_ORDER
        }
        self._effective_dates_by_action: dict[ActionType, list[date]] = {
            action_type: [] for action_type in _ACTION_TYPE_ORDER
        }

    def add_product_matches(
        self,
        candidate: ProductCandidate,
        matches: list[ProductImpactMatch],
    ) -> None:
        product_uid = candidate["product_uid"]
        product = self._products.get(product_uid)
        if product is None:
            product = _ProductActionAccumulator(
                product_uid=product_uid,
                product_name=candidate["product_name"],
            )
            self._products[product_uid] = product
            self._product_order.append(product_uid)

        product.add_candidate(candidate)

        for match in matches:
            for action_type in match.action_types:
                product.suggested_actions.add(action_type)
                self._product_uids_by_action[action_type].add(product_uid)
                if match.effective_date is not None:
                    self._effective_dates_by_action[action_type].append(
                        match.effective_date
                    )

    def to_candidate(self, policy_update: PolicyUpdateModel) -> UserActionCandidate:
        affected_products = self._build_affected_products()
        action_items = self._build_action_items(policy_update)
        email_payload = self._build_email_payload(
            policy_update=policy_update,
            affected_products=affected_products,
            action_items=action_items,
        )
        return UserActionCandidate(
            user_id=self._user_id,
            affected_products=affected_products,
            action_items=action_items,
            email_payload=email_payload,
        )

    def _build_affected_products(self) -> list[AffectedProduct]:
        products: list[AffectedProduct] = []
        for product_uid in self._product_order:
            product = self._products[product_uid]
            suggested_actions = [
                action_type
                for action_type in _ACTION_TYPE_ORDER
                if action_type in product.suggested_actions
            ]
            if not suggested_actions:
                continue

            products.append(
                {
                    "product_uid": product.product_uid,
                    "product_name": product.product_name,
                    "hts_code": product.display_hts_code(),
                    "suggested_actions": suggested_actions,
                }
            )
        return products

    def _build_action_items(self, policy_update: PolicyUpdateModel) -> list[ActionItem]:
        items: list[ActionItem] = []
        for action_type in _ACTION_TYPE_ORDER:
            if not self._product_uids_by_action[action_type]:
                continue

            items.append(
                {
                    "action_type": action_type,
                    "effective_date": _action_effective_date(
                        self._effective_dates_by_action[action_type],
                        policy_update,
                    ),
                    "status": ActionItemStatus.ACTION_NEEDED,
                }
            )
        return items

    def _build_email_payload(
        self,
        *,
        policy_update: PolicyUpdateModel,
        affected_products: list[AffectedProduct],
        action_items: list[ActionItem],
    ) -> EmailDeliveryPayload:
        email_products: list[EmailAffectedProduct] = [
            {
                "product_name": product["product_name"],
                "hts_code": product["hts_code"],
            }
            for product in affected_products
        ]
        action_summaries: list[EmailActionSummary] = [
            {
                "action_type": action_item["action_type"],
                "product_count": len(
                    self._product_uids_by_action[action_item["action_type"]]
                ),
                "effective_date": action_item["effective_date"],
            }
            for action_item in action_items
        ]
        payload: EmailDeliveryPayload = {
            "source_label": policy_update["source_label"],
            "reference_number": policy_update["reference_number"],
            "headline": policy_update["headline"],
            "summary": policy_update["summary"],
            "source_url": policy_update["source_url"],
            "affected_products": email_products,
            "action_summaries": action_summaries,
        }
        if self._account_owner_email is not None:
            payload["account_owner_email"] = self._account_owner_email
        return payload


class CreateUserActionsStage:
    name = "create_user_actions"

    def run(self, context: WorkerContext) -> StageResult:
        logger.info(
            "stage invoked: name=%s run_id=%s",
            self.name,
            context.run_id,
        )

        policy_updates = _list_policy_updates_to_calculate_user_actions()
        logger.info(
            "policy updates selected for action calculation: count=%s run_id=%s",
            len(policy_updates),
            context.run_id,
        )
        if not policy_updates:
            return StageResult()

        product_match_data = _load_product_match_data()

        for policy_update in policy_updates:
            try:
                _create_user_actions(policy_update, product_match_data)
            except Exception:
                logger.exception(
                    "create user actions failed: policy_update_id=%s",
                    policy_update["id"],
                )
                try:
                    _mark_failed(policy_update)
                except Exception:
                    logger.exception(
                        "create user actions failed and failed to mark failed: "
                        "policy_update_id=%s",
                        policy_update["id"],
                    )

        return StageResult()


def _list_policy_updates_to_calculate_user_actions() -> list[PolicyUpdateModel]:
    with acquire_connection() as conn:
        return policy_updates_repository.list_policy_updates_to_calculate_user_actions(conn)


def _load_product_match_data() -> ProductMatchData:
    started_at = perf_counter()
    with acquire_connection() as conn:
        product_candidates = product_match_repository.list_product_candidates(conn)
        calculation_coos = product_match_repository.list_calculation_coos(conn)
        imported_coos = product_match_repository.list_imported_coos(conn)

    data: ProductMatchData = {
        "product_candidates": product_candidates,
        "calculation_coos_by_product_uid": _group_by_product_uid(calculation_coos),
        "imported_coos_by_product_uid": _group_by_product_uid(imported_coos),
    }
    logger.info(
        "product match data loaded: product_candidates=%s calculation_coos=%s "
        "imported_coos=%s duration_seconds=%.3f",
        len(product_candidates),
        len(calculation_coos),
        len(imported_coos),
        perf_counter() - started_at,
    )
    return data


def _group_by_product_uid(
    rows: list[_CooRow],
) -> dict[str, list[_CooRow]]:
    grouped: defaultdict[str, list[_CooRow]] = defaultdict(list)
    for row in rows:
        grouped[row["product_uid"]].append(row)
    return dict(grouped)


def _create_user_actions(
    policy_update: PolicyUpdateModel,
    product_match_data: ProductMatchData,
) -> None:
    started_at = perf_counter()
    impacts = _load_and_normalize_impacts(policy_update)
    candidates = _calculate_user_action_candidates(
        policy_update=policy_update,
        impacts=impacts,
        product_match_data=product_match_data,
    )
    _commit_user_actions(policy_update, candidates)
    logger.info(
        "user actions calculated: policy_update_id=%s impacts=%s user_actions=%s "
        "affected_products=%s duration_seconds=%.3f",
        policy_update["id"],
        len(impacts),
        len(candidates),
        sum(len(candidate.affected_products) for candidate in candidates),
        perf_counter() - started_at,
    )


def _load_and_normalize_impacts(policy_update: PolicyUpdateModel) -> list[NormalizedImpact]:
    with acquire_connection() as conn:
        rows = policy_impacts_repository.list_by_policy_update_id(
            conn,
            policy_update_id=policy_update["id"],
        )

    if not rows:
        raise ActionCalculationError(
            f"approved policy update has no policy impacts: policy_update_id={policy_update['id']}"
        )

    impacts: list[NormalizedImpact] = []
    for row in rows:
        impact = _normalize_impact(row)
        if impact is not None:
            impacts.append(impact)
    return impacts


def _normalize_impact(row: PolicyImpactModel) -> NormalizedImpact | None:
    coos = _normalize_coos(row)
    if row["impacted_type"] is not PolicyImpactType.MEASURE_CHANGED and coos:
        raise ActionCalculationError(
            "non-measure policy impact must not have coos: "
            f"policy_impact_id={row['id']} impacted_type={row['impacted_type']}"
        )

    action_types = _action_types_for_impact(row["impacted_type"])
    if not action_types:
        return None

    hts_prefix = _normalize_hts_prefix(row)
    return NormalizedImpact(
        id=row["id"],
        hts_prefix=hts_prefix,
        impacted_type=row["impacted_type"],
        action_types=action_types,
        effective_date=row["effective_time"],
        coos=coos,
    )


def _action_types_for_impact(
    impacted_type: PolicyImpactType,
) -> tuple[ActionType, ...]:
    if impacted_type is PolicyImpactType.DELETED:
        return (ActionType.RECLASSIFY_PRODUCT, ActionType.RECALCULATE_TARIFF)
    if impacted_type is PolicyImpactType.INSERTED:
        return ()
    if impacted_type is PolicyImpactType.MEASURE_CHANGED:
        return (ActionType.RECALCULATE_TARIFF,)
    if impacted_type is PolicyImpactType.DESC_CHANGED:
        return (ActionType.RECLASSIFY_PRODUCT, ActionType.RECALCULATE_TARIFF)
    if impacted_type is PolicyImpactType.RATE_CHANGED:
        return (ActionType.RECALCULATE_TARIFF,)
    raise ActionCalculationError(f"unknown impacted_type: {impacted_type}")


def _normalize_hts_prefix(row: PolicyImpactModel) -> str:
    digits = re.sub(r"\D", "", row["hts_number"])
    if len(digits) not in _VALID_HTS_PREFIX_LENGTHS:
        raise ActionCalculationError(
            "policy impact hts_number must normalize to 2/4/6/8/10 digits: "
            f"policy_impact_id={row['id']} hts_number={row['hts_number']!r}"
        )
    return digits


def _normalize_coos(row: PolicyImpactModel) -> frozenset[str]:
    raw_coos = row["coos"]
    if raw_coos is None or not raw_coos:
        return frozenset()

    normalized = frozenset(coo.strip().upper() for coo in raw_coos if coo.strip())
    if not normalized:
        raise ActionCalculationError(
            f"policy impact coos normalized to empty: policy_impact_id={row['id']}"
        )
    return normalized


def _calculate_user_action_candidates(
    *,
    policy_update: PolicyUpdateModel,
    impacts: list[NormalizedImpact],
    product_match_data: ProductMatchData,
) -> list[UserActionCandidate]:
    builders: dict[int, UserActionBuilder] = {}
    impacts_by_prefix = _group_impacts_by_prefix(impacts)

    for candidate in product_match_data["product_candidates"]:
        matching_impacts = [
            impact
            for prefix in _candidate_hts_prefixes(candidate)
            for impact in impacts_by_prefix.get(prefix, [])
        ]
        matches = [
            ProductImpactMatch(
                action_types=impact.action_types,
                effective_date=impact.effective_date,
            )
            for impact in matching_impacts
            if _candidate_matches_impact(candidate, impact, product_match_data)
        ]
        if not matches:
            continue

        builder = builders.get(candidate["user_id"])
        if builder is None:
            builder = UserActionBuilder(
                user_id=candidate["user_id"],
                account_owner_email=candidate["account_owner_email"],
            )
            builders[candidate["user_id"]] = builder

        builder.add_product_matches(candidate, matches)

    return [
        builder.to_candidate(policy_update)
        for _user_id, builder in sorted(builders.items(), key=lambda item: item[0])
    ]


def _group_impacts_by_prefix(
    impacts: list[NormalizedImpact],
) -> dict[str, list[NormalizedImpact]]:
    grouped: defaultdict[str, list[NormalizedImpact]] = defaultdict(list)
    for impact in impacts:
        grouped[impact.hts_prefix].append(impact)
    return dict(grouped)


def _candidate_hts_prefixes(candidate: ProductCandidate) -> list[str]:
    hts_code = candidate["hts_code_normalized"]
    return [
        hts_code[:length]
        for length in sorted(_VALID_HTS_PREFIX_LENGTHS)
        if length <= len(hts_code)
    ]


def _candidate_matches_impact(
    candidate: ProductCandidate,
    impact: NormalizedImpact,
    product_match_data: ProductMatchData,
) -> bool:
    if not candidate["hts_code_normalized"].startswith(impact.hts_prefix):
        return False
    if not impact.coos:
        return True
    return _has_matching_product_coo(candidate, impact, product_match_data)


def _has_matching_product_coo(
    candidate: ProductCandidate,
    impact: NormalizedImpact,
    product_match_data: ProductMatchData,
) -> bool:
    product_uid = candidate["product_uid"]
    calculation_coos = product_match_data["calculation_coos_by_product_uid"].get(
        product_uid,
        [],
    )
    for row in calculation_coos:
        if (
            row["hts_code_normalized"].startswith(impact.hts_prefix)
            and row["country_code"] in impact.coos
        ):
            return True

    imported_coos = product_match_data["imported_coos_by_product_uid"].get(
        product_uid,
        [],
    )
    return any(row["country_code"] in impact.coos for row in imported_coos)


def _commit_user_actions(
    policy_update: PolicyUpdateModel,
    candidates: list[UserActionCandidate],
) -> None:
    with acquire_connection_with_transaction() as conn:
        for candidate in candidates:
            user_action_id = user_actions_repository.create_user_action(
                conn,
                user_id=candidate.user_id,
                policy_update_id=policy_update["id"],
                affected_products=candidate.affected_products,
                action_items=candidate.action_items,
            )
            if user_action_id is None:
                user_action_id = user_actions_repository.get_id_by_user_and_policy_update(
                    conn,
                    user_id=candidate.user_id,
                    policy_update_id=policy_update["id"],
                )
                if user_action_id is None:
                    raise ActionCalculationError(
                        "existing user action not found after conflict: "
                        f"user_id={candidate.user_id} policy_update_id={policy_update['id']}"
                    )

            recipients = notification_recipients_repository.list_active_recipients_by_user_id(
                conn,
                user_id=candidate.user_id,
            )
            for recipient in recipients:
                email_deliveries_repository.create_email_delivery(
                    conn,
                    user_action_id=user_action_id,
                    recipient_id=recipient["id"],
                    payload=candidate.email_payload,
                )

        rowcount = policy_updates_repository.mark_action_calculate_succeeded(
            conn,
            id=policy_update["id"],
        )
        if rowcount != 1:
            raise ActionCalculationError(
                f"policy update not found while marking succeeded: policy_update_id={policy_update['id']}"
            )


def _mark_failed(policy_update: PolicyUpdateModel) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = policy_updates_repository.mark_action_calculate_failed(
            conn,
            id=policy_update["id"],
        )
        if rowcount != 1:
            raise ActionCalculationError(
                f"policy update not found while marking failed: policy_update_id={policy_update['id']}"
            )

        updated_policy_update = policy_updates_repository.get_by_id(
            conn,
            id=policy_update["id"],
        )
        if updated_policy_update is None:
            raise ActionCalculationError(
                f"policy update not found after marking failed: policy_update_id={policy_update['id']}"
            )

        if (
            updated_policy_update["action_calculate_attempt_count"]
            >= _ACTION_CALCULATE_MAX_ATTEMPT_COUNT
        ):
            payload: AttemptExhaustedPayload = {
                "reason": "action_calculate_failed",
                "source_label": policy_update["source_label"],
                "reference_number": policy_update["reference_number"],
                "headline": policy_update["headline"],
                "source_url": policy_update["source_url"],
                "attempt_count": updated_policy_update[
                    "action_calculate_attempt_count"
                ],
                "stage": CreateUserActionsStage.name,
            }
            webhook_events_repository.create_webhook_event(
                conn,
                event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                entity_type=WebhookEntityType.ACTION_CALCULATE,
                entity_id=policy_update["id"],
                payload=payload,
            )


def _action_effective_date(
    effective_dates: list[date],
    policy_update: PolicyUpdateModel,
) -> str | None:
    if effective_dates:
        return min(effective_dates).isoformat()
    if policy_update["effective_date"] is not None:
        return policy_update["effective_date"].isoformat()
    return None
