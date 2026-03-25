"""
Questrade practice-account option execution for OptionsBuyStrategy decisions.

This module places option orders through Questrade order endpoints. It is built
for sandbox/practice usage by using a practice refresh token and (optionally)
the practice login token URL in credentials.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import os
import uuid

from .questrade_data_fetcher import (
    PRACTICE_QUESTRADE_TOKEN_URL,
    QuestradeDataFetcher,
)


@dataclass
class SandboxOrderResult:
    account_id: str
    payload: Dict
    impact: Optional[Dict] = None
    submitted: bool = False
    response: Optional[Dict] = None
    error: Optional[str] = None


class QuestradeOptionsPlaySandboxExecutor:
    """
    Executes option decisions from OptionsBuyStrategy in Questrade practice.

    Notes:
    - Questrade's order endpoints are used directly.
    - The caller should pass practice credentials/token URL.
    """

    def __init__(
        self,
        data_fetcher: QuestradeDataFetcher,
        account_id: Optional[str] = None,
    ):
        self.data_fetcher = data_fetcher
        self.account_id = account_id or self._resolve_account_id()

    def _resolve_account_id(self) -> str:
        accounts = self.data_fetcher.get_accounts()
        if not accounts:
            raise ValueError("No Questrade accounts available for this token.")

        # Prefer a practice account if one is marked as practice.
        for account in accounts:
            if account.get("isPrimary", False):
                return str(account["number"])
        return str(accounts[0]["number"])

    def _collect_option_candidates(self, symbol: str) -> List[Dict]:
        chain = self.data_fetcher.get_option_chain(symbol)
        candidates: List[Dict] = []
        symbol_ids: List[int] = []

        for expiry_block in chain.get("optionChain", []):
            expiry_date = expiry_block.get("expiryDate")
            for root in expiry_block.get("chainPerRoot", []):
                for strike_block in root.get("chainPerStrikePrice", []):
                    strike_price = strike_block.get("strikePrice")
                    if strike_price is None:
                        continue
                    for option_type in ("call", "put"):
                        leg = strike_block.get(option_type, {}) or {}
                        symbol_id = (
                            leg.get("symbolId")
                            or strike_block.get(f"{option_type}SymbolId")
                            or strike_block.get(f"{option_type}symbolId")
                        )
                        if symbol_id is None:
                            continue

                        symbol_id = int(symbol_id)
                        symbol_ids.append(symbol_id)

                        bid = leg.get("bid", 0) or 0
                        ask = leg.get("ask", 0) or 0
                        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                        candidates.append(
                            {
                                "symbol_id": symbol_id,
                                "type": option_type,
                                "strike": float(strike_price),
                                "expiry_date": expiry_date,
                                "bid": float(bid),
                                "ask": float(ask),
                                "mid": float(mid),
                            }
                        )

        # Enrich with quotes if we only have symbol IDs.
        if candidates and symbol_ids and hasattr(self.data_fetcher, "get_quotes_by_ids"):
            quotes: Dict[int, Dict] = {}
            unique_ids = sorted(set(symbol_ids))
            batch_size = 10
            for start in range(0, len(unique_ids), batch_size):
                batch = unique_ids[start : start + batch_size]
                try:
                    quotes.update(self.data_fetcher.get_quotes_by_ids(batch))
                except Exception as err:
                    if os.getenv("DEBUG_OPTIONS") == "1":
                        print(f"[DEBUG] Option quote fetch failed for batch {start}-{start + batch_size}: {err}")
                    continue

            if os.getenv("DEBUG_OPTIONS") == "1":
                print(f"[DEBUG] Option quotes returned: {len(quotes)}")
                if quotes:
                    sample_quote = next(iter(quotes.values()))
                    print(f"[DEBUG] Sample option quote keys: {list(sample_quote.keys())}")

            for candidate in candidates:
                if candidate["bid"] > 0 and candidate["ask"] > 0:
                    continue
                quote = quotes.get(candidate["symbol_id"], {})
                bid = quote.get("bid", quote.get("bidPrice", 0)) or 0
                ask = quote.get("ask", quote.get("askPrice", 0)) or 0
                candidate["bid"] = float(bid)
                candidate["ask"] = float(ask)
                candidate["mid"] = (
                    (candidate["bid"] + candidate["ask"]) / 2
                    if candidate["bid"] > 0 and candidate["ask"] > 0
                    else 0
                )

        if os.getenv("DEBUG_OPTIONS") == "1":
            with_mid = sum(1 for c in candidates if c["mid"] > 0)
            print(f"[DEBUG] Option candidates for {symbol}: {len(candidates)} (mid>0: {with_mid})")
        return candidates

    def _resolve_option_symbol(
        self,
        symbol: str,
        option_type: str,
        strike: float,
        target_dte: int,
    ) -> Dict:
        candidates = self._collect_option_candidates(symbol)
        typed = [c for c in candidates if c["type"] == option_type.lower()]
        if not typed:
            raise ValueError(f"No {option_type} options found for {symbol}.")

        for candidate in typed:
            expiry_raw = candidate.get("expiry_date")
            if not expiry_raw:
                candidate["dte"] = 10_000
                continue
            expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
            candidate["dte"] = (expiry - datetime.now(expiry.tzinfo)).days

        # Choose closest strike and DTE to the strategy decision.
        typed.sort(
            key=lambda c: (
                abs(c["strike"] - float(strike)),
                abs(c.get("dte", 10_000) - int(target_dte)),
            )
        )
        return typed[0]

    def _build_order_payload(
        self,
        symbol_id: int,
        quantity: int,
        action: str,
        limit_price: float,
        time_in_force: str = "Day",
    ) -> Dict:
        return {
            "clientOrderId": f"optsandbox-{uuid.uuid4().hex[:16]}",
            "symbolId": int(symbol_id),
            "quantity": int(quantity),
            "isAllOrNone": False,
            "isAnonymous": False,
            "orderType": "Limit",
            "action": action,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
            "timeInForce": time_in_force,
            "limitPrice": round(float(limit_price), 2),
        }

    def execute_opportunity(
        self,
        opportunity: Dict,
        quantity: int = 1,
        submit_order: bool = False,
        preview_impact: bool = True,
        time_in_force: str = "Day",
    ) -> SandboxOrderResult:
        """
        Build and optionally submit a buy-to-open paper order from strategy output.

        Expected opportunity shape:
        {
            "symbol": "AAPL",
            "option": {"type": "call"|"put", "strike": 220.0, "dte": 30, ...},
            "metrics": {"mid_price": 2.15, ...}
        }
        """
        symbol = opportunity["symbol"]
        option = opportunity["option"]
        metrics = opportunity.get("metrics", {})

        try:
            resolved = self._resolve_option_symbol(
                symbol=symbol,
                option_type=option["type"],
                strike=option["strike"],
                target_dte=option["dte"],
            )

            # For entries, limit at ask if available; fallback to strategy mid.
            limit = resolved["ask"] if resolved["ask"] > 0 else metrics.get("mid_price", 0)
            if limit <= 0:
                raise ValueError("Could not determine a valid limit price for the option order.")

            payload = self._build_order_payload(
                symbol_id=resolved["symbol_id"],
                quantity=quantity,
                action="BTO",
                limit_price=limit,
                time_in_force=time_in_force,
            )

            result = SandboxOrderResult(account_id=self.account_id, payload=payload)

            if preview_impact:
                result.impact = self.data_fetcher.get_order_impact(self.account_id, payload)

            if submit_order:
                result.response = self.data_fetcher.place_order(self.account_id, payload)
                result.submitted = True

            return result

        except Exception as exc:
            return SandboxOrderResult(
                account_id=self.account_id,
                payload={},
                error=str(exc),
            )


def ensure_practice_token_url(fetcher: QuestradeDataFetcher) -> bool:
    """
    Helper to check whether credentials are set to practice login endpoint.
    """
    token_url = fetcher.credentials.token_url or ""
    return token_url.rstrip("/") == PRACTICE_QUESTRADE_TOKEN_URL.rstrip("/")
