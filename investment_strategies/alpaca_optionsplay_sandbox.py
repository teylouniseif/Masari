from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .alpaca_data_fetcher import AlpacaDataFetcher
from .options_buy_strategy import OptionsBuyStrategy


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

    def __init__(self, data_fetcher: AlpacaDataFetcher, strategy: Optional[OptionsBuyStrategy] = None):
        self.data_fetcher = data_fetcher
        self.strategy = strategy
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
        side: str = "buy",
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
        position_intent: str = "buy_to_open",
    ) -> Dict[str, Any]:
        payload = {
            "symbol": option_symbol,
            "qty": str(int(quantity)),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force.lower(),
            "order_class": "simple",
            "position_intent": position_intent,
        }
        if limit_price is not None:
            payload["limit_price"] = str(round(float(limit_price), 2))
        return payload

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

                if self.strategy is not None:
                    position = self.strategy.create_position(opportunity, position_size=quantity)
                    # Track against the actual resolved contract and submitted limit price,
                    # not the (possibly stale) values captured during the earlier scan.
                    position["option_symbol"] = resolved["option_symbol"]
                    position["entry_price"] = limit
                    position["max_risk"] = limit * quantity * 100
                    position["profit_target"] = limit * self.strategy.criteria.profit_target_multiplier
                    position["stop_loss"] = limit * self.strategy.criteria.stop_loss_multiplier
                    result.impact = result.impact or {}
                    result.impact["tracked_position_id"] = position["id"]

            return result

        except Exception as exc:
            return AlpacaSandboxOrderResult(
                account_id=self.account_id,
                payload={},
                error=str(exc),
            )

    def close_opportunity(
        self,
        position: Dict[str, Any],
        current_price: Optional[float] = None,
        exit_reason: str = "Manual sell-to-close",
        submit_order: bool = True,
    ) -> AlpacaSandboxOrderResult:
        """Sell-to-close a tracked position at Alpaca and record realized P&L on the strategy."""
        option_symbol = position.get("option_symbol")
        if not option_symbol:
            return AlpacaSandboxOrderResult(
                account_id=self.account_id,
                payload={},
                error="Position has no option_symbol; cannot close it via Alpaca.",
            )

        broker_position = self.data_fetcher.get_position(option_symbol)
        if broker_position is None:
            return AlpacaSandboxOrderResult(
                account_id=self.account_id,
                payload={},
                error=f"No open Alpaca position found for {option_symbol}.",
            )

        qty = abs(int(float(broker_position.get("qty", position.get("position_size", 1)))))
        if current_price is None:
            current_price = float(broker_position.get("current_price", 0.0))

        payload = self._build_order_payload(
            option_symbol=option_symbol,
            quantity=qty,
            side="sell",
            order_type="market",
            position_intent="sell_to_close",
        )

        result = AlpacaSandboxOrderResult(
            account_id=self.account_id,
            option_symbol=option_symbol,
            payload=payload,
        )

        try:
            if submit_order:
                result.response = self.data_fetcher.place_order(self.account_id, payload)
                result.submitted = True

                if self.strategy is not None:
                    self.strategy.close_position(
                        {
                            "position": position,
                            "current_price": current_price,
                            "exit_reason": exit_reason,
                        }
                    )
        except Exception as exc:
            result.error = str(exc)

        return result

    def monitor_and_close(self, submit_order: bool = True) -> List[AlpacaSandboxOrderResult]:
        """
        Check tracked open positions against live Alpaca data. Closes any that hit an
        exit condition (profit target, stop loss, time decay, technical signal), and
        reconciles positions no longer held at the broker (expired or exercised).
        """
        if self.strategy is None:
            return []

        results: List[AlpacaSandboxOrderResult] = []

        for position in list(self.strategy.open_positions):
            option_symbol = position.get("option_symbol")
            if not option_symbol:
                continue

            broker_position = self.data_fetcher.get_position(option_symbol)

            if broker_position is None:
                # Contract is no longer held: expired worthless, was exercised/assigned,
                # or was closed outside this tool. We have no reliable exit price.
                position["status"] = "closed"
                position["exit_date"] = datetime.now()
                position["exit_reason"] = "No longer held at Alpaca (expired, exercised, or closed elsewhere)"
                position["final_pnl"] = None
                self.strategy.open_positions.remove(position)
                self.strategy.closed_positions.append(position)
                continue

            current_price = float(broker_position.get("current_price", 0.0))
            exit_reason = self.strategy.evaluate_exit(position, current_price)
            if not exit_reason:
                continue

            results.append(
                self.close_opportunity(
                    position,
                    current_price=current_price,
                    exit_reason=exit_reason,
                    submit_order=submit_order,
                )
            )

        return results
