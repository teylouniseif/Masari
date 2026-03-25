from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .alpaca_data_fetcher import AlpacaDataFetcher


@dataclass
class AlpacaSandboxOrderResult:
    account_id: str
    payload: Dict[str, Any]
    option_symbol: Optional[str] = None
    impact: Optional[Dict[str, Any]] = None
    submitted: bool = False
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class AlpacaOptionsPlaySandboxExecutor:
    """
    Executes option decisions from OptionsBuyStrategy in Alpaca paper trading.
    """

    def __init__(self, data_fetcher: AlpacaDataFetcher):
        self.data_fetcher = data_fetcher
        account = self.data_fetcher.ensure_paper_account()
        self.account_id = account.get("account_number", "paper")

    def _collect_option_candidates(self, symbol: str) -> List[Dict[str, Any]]:
        chain = self.data_fetcher.get_option_chain(symbol)
        candidates: List[Dict[str, Any]] = []

        for expiry_block in chain.get("optionChain", []):
            expiry_date = expiry_block.get("expiryDate")
            for root in expiry_block.get("chainPerRoot", []):
                for strike_block in root.get("chainPerStrikePrice", []):
                    strike_price = strike_block.get("strikePrice")
                    if strike_price is None:
                        continue

                    for option_type in ("call", "put"):
                        leg = strike_block.get(option_type, {})
                        if not leg:
                            continue

                        bid = leg.get("bid", 0.0) or 0.0
                        ask = leg.get("ask", 0.0) or 0.0
                        candidates.append(
                            {
                                "option_symbol": leg.get("symbol"),
                                "type": option_type,
                                "strike": float(strike_price),
                                "expiry_date": expiry_date,
                                "bid": float(bid),
                                "ask": float(ask),
                                "mid": (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0,
                            }
                        )
        return candidates

    def _resolve_option_contract(
        self,
        symbol: str,
        option_type: str,
        strike: float,
        target_dte: int,
    ) -> Dict[str, Any]:
        candidates = self._collect_option_candidates(symbol)
        typed = [c for c in candidates if c["type"] == option_type.lower() and c.get("option_symbol")]
        if not typed:
            raise ValueError(f"No {option_type} options found for {symbol}.")

        for candidate in typed:
            expiry_raw = candidate.get("expiry_date")
            if not expiry_raw:
                candidate["dte"] = 10_000
                continue
            expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
            candidate["dte"] = (expiry - datetime.now(expiry.tzinfo)).days

        typed.sort(
            key=lambda c: (
                abs(c["strike"] - float(strike)),
                abs(c.get("dte", 10_000) - int(target_dte)),
            )
        )
        return typed[0]

    def _build_order_payload(
        self,
        option_symbol: str,
        quantity: int,
        limit_price: float,
        time_in_force: str = "day",
    ) -> Dict[str, Any]:
        return {
            "symbol": option_symbol,
            "qty": str(int(quantity)),
            "side": "buy",
            "type": "limit",
            "time_in_force": time_in_force.lower(),
            "limit_price": str(round(float(limit_price), 2)),
            "order_class": "simple",
            "position_intent": "buy_to_open",
        }

    def execute_opportunity(
        self,
        opportunity: Dict[str, Any],
        quantity: int = 1,
        submit_order: bool = False,
        preview_impact: bool = True,
        time_in_force: str = "day",
    ) -> AlpacaSandboxOrderResult:
        symbol = opportunity["symbol"]
        option = opportunity["option"]
        metrics = opportunity.get("metrics", {})

        try:
            resolved = self._resolve_option_contract(
                symbol=symbol,
                option_type=option["type"],
                strike=option["strike"],
                target_dte=option["dte"],
            )

            limit = resolved["ask"] if resolved["ask"] > 0 else metrics.get("mid_price", 0)
            if limit <= 0:
                raise ValueError("Could not determine a valid limit price for the option order.")

            payload = self._build_order_payload(
                option_symbol=resolved["option_symbol"],
                quantity=quantity,
                limit_price=limit,
                time_in_force=time_in_force,
            )

            result = AlpacaSandboxOrderResult(
                account_id=self.account_id,
                option_symbol=resolved["option_symbol"],
                payload=payload,
            )

            if preview_impact:
                result.impact = self.data_fetcher.get_order_impact(self.account_id, payload)

            if submit_order:
                result.response = self.data_fetcher.place_order(self.account_id, payload)
                result.submitted = True

            return result

        except Exception as exc:
            return AlpacaSandboxOrderResult(
                account_id=self.account_id,
                payload={},
                error=str(exc),
            )
