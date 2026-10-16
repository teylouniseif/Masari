import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .alpaca_data_fetcher import AlpacaDataFetcher
from .options_buy_strategy import OPTION_CONTRACT_MULTIPLIER, OptionsBuyStrategy

# OCC-style option symbol: root ticker + YYMMDD + C/P + 8-digit strike, e.g. AAPL260819C00205000
_OPTION_SYMBOL_RE = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")


def _underlying_symbol(option_symbol: str) -> Optional[str]:
    match = _OPTION_SYMBOL_RE.match(option_symbol)
    return match.group(1) if match else None


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

    def _has_open_exposure(self, underlying_symbol: str) -> bool:
        """
        True if we already hold a position, or have a working (unfilled) order,
        on any option contract for this underlying. Prevents piling into the
        same name repeatedly across scans/scheduled runs before a prior order
        has even filled or been reconciled.
        """
        for position in self.data_fetcher.get_positions():
            if _underlying_symbol(position.get("symbol", "")) == underlying_symbol:
                return True

        open_orders = self.data_fetcher._request(
            "GET", "v2/orders", params={"status": "open", "limit": 500}
        )
        for order in open_orders:
            if _underlying_symbol(order.get("symbol", "")) == underlying_symbol:
                return True

        return False

    def _calculate_quantity(self, limit_price: float) -> int:
        """
        Size the order so max risk (premium paid is the full risk on a long
        option) stays within criteria.max_risk_per_trade percent of account
        equity, and never exceeds available buying power.
        """
        if self.strategy is None or limit_price <= 0:
            return 1

        per_contract_cost = limit_price * OPTION_CONTRACT_MULTIPLIER
        if per_contract_cost <= 0:
            return 0

        account = self.data_fetcher.get_account()
        equity = float(account.get("equity", 0.0))
        buying_power = float(account.get("buying_power", 0.0))

        risk_budget = equity * (self.strategy.criteria.max_risk_per_trade / 100.0)
        by_risk = int(risk_budget // per_contract_cost)
        by_buying_power = int(buying_power // per_contract_cost)

        return max(min(by_risk, by_buying_power), 0)

    def max_affordable_premium(self) -> float:
        """
        Highest per-share premium at which _calculate_quantity still buys one
        contract. Lets the caller pass over contracts it could never size
        instead of spending its order slot on one.
        """
        account = self.data_fetcher.get_account()
        equity = float(account.get("equity", 0.0))
        buying_power = float(account.get("buying_power", 0.0))
        risk_budget = equity * (self.strategy.criteria.max_risk_per_trade / 100.0)
        return min(risk_budget, buying_power) / OPTION_CONTRACT_MULTIPLIER

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
                                "delta": leg.get("delta"),
                                "theta": leg.get("theta"),
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
        quantity: Optional[int] = None,
        submit_order: bool = False,
        preview_impact: bool = True,
        time_in_force: str = "day",
    ) -> AlpacaSandboxOrderResult:
        symbol = opportunity["symbol"]
        option = opportunity["option"]
        metrics = opportunity.get("metrics", {})

        if submit_order and self._has_open_exposure(symbol):
            return AlpacaSandboxOrderResult(
                account_id=self.account_id,
                payload={},
                error=(
                    f"Skipping {symbol}: already have an open position or working order "
                    "on this underlying. Close/cancel it first, or let it resolve, before "
                    "opening another."
                ),
            )

        try:
            resolved = self._resolve_option_contract(
                symbol=symbol,
                option_type=option["type"],
                strike=option["strike"],
                target_dte=option["dte"],
            )

            # Re-checked here because the contract is re-resolved from a fresh
            # chain and may not be the one the scan qualified.
            if resolved.get("delta") is None or resolved.get("theta") is None:
                raise ValueError(
                    f"{resolved['option_symbol']} has no Greeks in the feed; not buying a "
                    "contract the Greek exits can't monitor."
                )

            limit = resolved["ask"] if resolved["ask"] > 0 else metrics.get("mid_price", 0)
            if limit <= 0:
                raise ValueError("Could not determine a valid limit price for the option order.")

            if quantity is None:
                quantity = self._calculate_quantity(limit)
                if quantity < 1:
                    raise ValueError(
                        f"Sizing to {self.strategy.criteria.max_risk_per_trade}% max risk per "
                        f"trade yields 0 contracts at ${limit:.2f}/contract given current "
                        "account equity/buying power."
                    )

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
                    # Needed by monitor_and_close to tell "order hasn't filled yet" apart
                    # from "position filled and is now gone" when get_position() is empty.
                    position["order_id"] = (result.response or {}).get("id")
                    position["max_risk"] = limit * quantity * 100
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

    # Order still working its way to a fill; not a position yet, not gone either.
    _OPEN_ORDER_STATUSES = {
        "new", "accepted", "pending_new", "accepted_for_bidding",
        "partially_filled", "pending_cancel", "pending_replace",
        "calculated", "held", "pending_review",
    }
    # Order died before ever filling: there was never a real position to close.
    _TERMINAL_UNFILLED_STATUSES = {
        "canceled", "expired", "rejected", "done_for_day", "replaced", "suspended", "stopped",
    }

    def monitor_and_close(self, submit_order: bool = True) -> List[AlpacaSandboxOrderResult]:
        """
        Check tracked open positions against live Alpaca data. Closes any that hit an
        exit condition (stop loss, time decay, technical or Greek signal), and
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
                self._reconcile_missing_position(position)
                continue

            current_price = float(broker_position.get("current_price", 0.0))
            exit_reason = self.strategy.evaluate_exit(
                position, current_price, snapshot=self._option_snapshot(option_symbol)
            )
            if exit_reason:
                results.append(
                    self.close_opportunity(
                        position,
                        current_price=current_price,
                        exit_reason=exit_reason,
                        submit_order=submit_order,
                    )
                )

        return results

    def _option_snapshot(self, option_symbol: str) -> Optional[Dict[str, Any]]:
        """Live Greeks and IV for one contract, or None so the Greek exits are skipped."""
        try:
            return self.data_fetcher.get_option_snapshots([option_symbol]).get(option_symbol)
        except Exception as exc:
            print(f"Could not fetch snapshot for {option_symbol}: {exc}")
            return None

    def _reconcile_missing_position(self, position: Dict[str, Any]) -> None:
        """
        get_position() found nothing, which is ambiguous on its own: it means either
        the entry order hasn't filled yet (still working, or died without filling -
        completely normal right after entry, since these are day-limit orders) or a
        real position existed and is now gone. Only the entry order's own status
        tells those apart, so check it before concluding anything.
        """
        order_id = position.get("order_id")
        order = self.data_fetcher.get_order(order_id) if order_id else None

        if order is None:
            # No order to check against (e.g. an older record from before this field
            # existed). Fall back to the previous best-effort behavior rather than
            # silently losing the position.
            self._close_with_unknown_pnl(
                position, "No longer held at Alpaca (expired, exercised, or closed elsewhere)"
            )
            return

        status = order.get("status")
        filled_qty = float(order.get("filled_qty") or 0)

        if filled_qty == 0 and status in self._OPEN_ORDER_STATUSES:
            # Never filled yet. Leave it tracked and check again next tick.
            return

        if filled_qty == 0 and status in self._TERMINAL_UNFILLED_STATUSES:
            # Entry order died before ever filling - this was never a real position,
            # so it doesn't belong in closed_positions distorting P&L stats either.
            self.strategy.open_positions.remove(position)
            return

        # The fill can land between the position lookup and the order lookup -
        # queued orders fill in the first seconds after the open - so the order
        # reads as filled while the position read was empty. Look again before
        # calling it closed.
        if self.data_fetcher.get_position(position["option_symbol"]) is not None:
            return

        # The entry order did fill at some point and the position is gone now: a
        # genuine close. Look for the actual closing fill before giving up on price.
        exit_price = self._find_closing_fill_price(
            position["option_symbol"], after=order.get("filled_at")
        )
        if exit_price is not None:
            self.strategy.close_position(
                {
                    "position": position,
                    "current_price": exit_price,
                    "exit_reason": "Closed at broker (reconciled from fill history)",
                }
            )
        else:
            self._close_with_unknown_pnl(
                position,
                "Closed at broker with no recoverable exit price (likely expired ITM/OTM)",
            )

    def _close_with_unknown_pnl(self, position: Dict[str, Any], reason: str) -> None:
        position["status"] = "closed"
        position["exit_date"] = datetime.now()
        position["exit_reason"] = reason
        position["final_pnl"] = None
        self.strategy.open_positions.remove(position)
        self.strategy.closed_positions.append(position)

    def _find_closing_fill_price(self, option_symbol: str, after: Optional[str]) -> Optional[float]:
        try:
            orders = self.data_fetcher.list_orders(
                symbol=option_symbol, status="closed", side="sell", after=after, limit=10
            )
        except Exception:
            return None
        for order in orders:
            if order.get("status") == "filled" and order.get("filled_avg_price"):
                return float(order["filled_avg_price"])
        return None
