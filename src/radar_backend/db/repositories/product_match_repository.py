from __future__ import annotations

from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row

from radar_backend.domain import (
    ProductCandidate,
    ProductImportedCoo,
    TariffCalculationCoo,
)


class ProductMatchRepository:
    def list_product_candidates(
        self,
        conn: Connection,
    ) -> list[ProductCandidate]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  p.user_id,
                  u.email AS account_owner_email,
                  p.product_uid,
                  COALESCE(NULLIF(p.display_name, ''), NULLIF(p.product_name, ''), p.product_uid) AS product_name,
                  COALESCE(NULLIF(c.hts_code, ''), c.hts_code_normalized) AS hts_code,
                  c.hts_code_normalized,
                  c.candidate_rank
                FROM t_product p
                JOIN t_product_hts_candidate c
                  ON c.product_uid = p.product_uid
                JOIN users u
                  ON u.id = p.user_id
                WHERE p.is_deleted IS FALSE
                  AND p.is_split_parent IS FALSE
                  AND p.classification_type = 'hts'
                  AND c.hts_code_normalized ~ '^[0-9]{10}$'
                ORDER BY
                  p.user_id ASC,
                  p.product_uid ASC,
                  c.candidate_rank ASC NULLS LAST,
                  c.hts_code_normalized ASC
                """
            )
            rows = cur.fetchall()

        return [self._to_product_candidate(row) for row in rows]

    def list_calculation_coos(
        self,
        conn: Connection,
    ) -> list[TariffCalculationCoo]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  product_uid,
                  hts_code_normalized,
                  upper(trim(country_code)) AS country_code
                FROM t_sandbox_calculation_result
                WHERE hts_code_normalized ~ '^[0-9]{10}$'
                  AND NULLIF(trim(country_code), '') IS NOT NULL
                ORDER BY product_uid ASC, hts_code_normalized ASC, country_code ASC
                """
            )
            rows = cur.fetchall()

        return [self._to_calculation_coo(row) for row in rows]

    def list_imported_coos(
        self,
        conn: Connection,
    ) -> list[ProductImportedCoo]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  product_uid,
                  upper(trim(imported_country_code)) AS country_code
                FROM t_sandbox_product_profile
                WHERE NULLIF(trim(imported_country_code), '') IS NOT NULL
                ORDER BY product_uid ASC, country_code ASC
                """
            )
            rows = cur.fetchall()

        return [self._to_imported_coo(row) for row in rows]

    def _to_product_candidate(self, row: dict[str, object]) -> ProductCandidate:
        return {
            "user_id": cast(int, row["user_id"]),
            "account_owner_email": cast(str | None, row["account_owner_email"]),
            "product_uid": cast(str, row["product_uid"]),
            "product_name": cast(str, row["product_name"]),
            "hts_code": cast(str, row["hts_code"]),
            "hts_code_normalized": cast(str, row["hts_code_normalized"]),
            "candidate_rank": cast(int | None, row["candidate_rank"]),
        }

    def _to_calculation_coo(self, row: dict[str, object]) -> TariffCalculationCoo:
        return {
            "product_uid": cast(str, row["product_uid"]),
            "hts_code_normalized": cast(str, row["hts_code_normalized"]),
            "country_code": cast(str, row["country_code"]),
        }

    def _to_imported_coo(self, row: dict[str, object]) -> ProductImportedCoo:
        return {
            "product_uid": cast(str, row["product_uid"]),
            "country_code": cast(str, row["country_code"]),
        }
