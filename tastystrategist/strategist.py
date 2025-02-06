import asyncio
import tkinter as tk
from datetime import date, timedelta
from dataclasses import dataclass
from typing import List
from decimal import Decimal

from tastytrade import Session, Account
from tastytrade.instruments import Option, OptionType
from tastytrade.instruments import get_option_chain
from tastytrade.order import OrderAction, PlacedOrderResponse, PlacedOrder, OrderStatus, Leg
from tastytrade.utils import TastytradeError

from tastystrategist.streamer import LivePrices
from tastystrategist.streamer import AccountUpdates
from tastystrategist.position import IronCondor, PositionState


@dataclass
class PositionManager:
    account_updates: AccountUpdates
    state: PositionState = PositionState.NO_POSITION
    position: IronCondor | None = None
    open_response: PlacedOrderResponse | None = None
    buying_power_effect_open: Decimal | None = None
    buying_power_effect_close: Decimal | None = None
    close_response: PlacedOrderResponse | None = None

    def set_position(self, position: IronCondor):
        self.position = position
        self.state = PositionState.PENDING
    
    @staticmethod
    def print_order_summary(order: PlacedOrder):
        print(f'Order Summary: {order}')

    # Can raise an exception from the account place_order part
    async def open_position(self, session: Session, account: Account, dry_run=True) -> PlacedOrderResponse:
        order = self.position.opening_order()
        response = await account.a_place_order(session, order, dry_run)
        self.open_response = response
        if not dry_run:
            self.state = PositionState.OPENING_REQUESTED
            # Wait until order is filled
            while not self.is_open_order_filled():
                await asyncio.sleep(0.2)
            self.print_order_summary(self.get_open_order())
            self.state = PositionState.OPEN
        return response

    # Can raise an exception from the account place_order part
    async def close_position(self, session: Session, account: Account, dry_run=True) -> PlacedOrderResponse:
        order = self.position.closing_order()
        response = await account.a_place_order(session, order, dry_run)
        self.close_response = response
        if not dry_run:
            self.state = PositionState.CLOSING_REQUESTED
            while not self.is_close_order_filled():
                await asyncio.sleep(0.2)
            self.print_order_summary(self.get_close_order())
            self.state = PositionState.CLOSED
        return response
    
    async def margin_requirement(self, session: Session, account: Account):
        if self.state < PositionState.PENDING:
            return None
        if self.position is None or self.open_response is None:
            await self.open_position(session, account, dry_run=True)
        return self.open_response.buying_power_effect.change_in_buying_power
    
    def margin_requirement_no_wait(self):
        if self.open_response is None:
            return None
        return self.open_response.buying_power_effect.change_in_buying_power
    
    def get_open_order(self) -> PlacedOrder:
        if self.state <= PositionState.PENDING:
            return None
        order_id = self.open_response.order.id
        # Order update not received yet
        if order_id not in self.account_updates.orders:
            return self.open_response.order
        # Getting live status
        return self.account_updates.orders[order_id]
    
    def get_close_order(self) -> PlacedOrder:
        if self.state < PositionState.CLOSING_REQUESTED:
            return None
        order_id = self.close_response.order.id
        # Order update not received yet
        if order_id not in self.account_updates.orders:
            return self.close_response.order
        # Getting live status
        return self.account_updates.orders[order_id]
    
    def is_open_order_filled(self):
        return self.get_open_order() is not None and self.get_open_order().status == OrderStatus.FILLED
    
    def is_close_order_filled(self):
        return self.get_close_order() is not None and self.get_close_order().status == OrderStatus.FILLED


    def _calculate_buying_power_effect(self, legs: list[Leg]) -> Decimal:
        profit = Decimal('0.0')
        for leg in legs:
            # Return None if order is not fully filled. Should not happen as we check at the start
            if leg.remaining_quantity > Decimal('0.0'):
                return None
            for fill in leg.fills:
                fill_cost = fill.quantity * fill.fill_price
                if leg.action in [OrderAction.BUY_TO_OPEN, OrderAction.BUY_TO_CLOSE]:
                    profit -= fill_cost
                elif leg.action in [OrderAction.SELL_TO_OPEN, OrderAction.SELL_TO_CLOSE]:
                    profit += fill_cost
        profit = profit * Decimal('100.0')
        return profit

    def get_buying_power_effect_open(self) -> Decimal:
        # Already calculated before. Think of changing to @property
        if self.buying_power_effect_open is not None:
            return self.buying_power_effect_open
        # Not possible to calculate yet
        if self.state < PositionState.OPEN:
            return None
        legs = self.get_open_order().legs
        profit = self._calculate_buying_power_effect(legs)
        self.buying_power_effect_open = profit
        # print(f'Opened position for ${profit}')
        return profit
    
    def get_buying_power_effect_close(self) -> Decimal:
        if self.buying_power_effect_close is not None:
            return self.buying_power_effect_close
        if self.state != PositionState.CLOSED:
            return None
        legs = self.get_close_order().legs
        profit = self._calculate_buying_power_effect(legs)
        self.buying_power_effect_close = profit
        # print(f'Closed position for ${profit}')
        return profit


@dataclass
class Strategist:
    live_prices: LivePrices
    underlying_symbol: str
    root_symbol: str
    options: List[Option]
    position_manager: PositionManager | None = None
    sandbox_account: Account | None = None
    session_sandbox: Session | None = None

    @classmethod
    async def create(
        cls,
        session: Session,
        session_sandbox: Session,
        account_sandbox: Account,
        underlying_symbol: str,
        root_symbol: str,
    ):
        live_prices = await LivePrices.create(session, [underlying_symbol])
        print('Initialized live prices')

        reference_price = (live_prices.quotes[underlying_symbol].bid_price + live_prices.quotes[underlying_symbol].ask_price) / 2
        print(f'{underlying_symbol} is at {reference_price}')

        # Blocking call
        options = get_option_chain(session_sandbox, root_symbol)[date.today() + timedelta(days=1)]
        # print(f'Options fetched: {options}')

        account_updates = await AccountUpdates.create(session_sandbox, account_sandbox)
        position_manager = PositionManager(account_updates)
        print('Initialized account updates')

        self = cls(live_prices, underlying_symbol, root_symbol, options, position_manager, account_sandbox, session_sandbox)
        
        print('Starting strategy loop...')
        await self._build_strategy()
        print('Strategy loop started!')
        
        # Start the continuous build options loop
        asyncio.create_task(self._run_build_strategy())
        asyncio.create_task(self._run_margin_requirement(session_sandbox, account_sandbox))
        
        return self

    async def _run_margin_requirement(self, session: Session, account: Account):
        while True:
            await self.compute_margin_requirement(session, account)
            await asyncio.sleep(0.3)

    async def _run_build_strategy(self, update_interval: int = 3):
        while True:
            await self._build_strategy()
            await asyncio.sleep(update_interval)

    def get_reference_price(self):
        return (self.live_prices.quotes[self.underlying_symbol].bid_price + self.live_prices.quotes[self.underlying_symbol].ask_price) / 2
    
    # The put we sell
    def get_main_put_price(self, buy=False):
        quote = self.live_prices.quotes[self.position_manager.position.main_put.streamer_symbol]
        return quote.ask_price if buy else quote.bid_price
    
    # The put we buy
    def get_insurance_put_price(self, buy=True):
        quote = self.live_prices.quotes[self.position_manager.position.insurance_put.streamer_symbol]
        return quote.ask_price if buy else quote.bid_price
    
    def get_main_call_price(self, buy=False):
        quote = self.live_prices.quotes[self.position_manager.position.main_call.streamer_symbol]
        return quote.ask_price if buy else quote.bid_price
    
    def get_insurance_call_price(self, buy=True):
        quote = self.live_prices.quotes[self.position_manager.position.insurance_call.streamer_symbol]
        return quote.ask_price if buy else quote.bid_price
    
    async def compute_margin_requirement(self, session: Session, account: Account):
        try:
            await self.position_manager.margin_requirement(session, account)
        except TastytradeError as e:
            print(f'Could not execute dry-run order. Error {e}')

    async def _build_strategy(self, search_interval: int = 500, price_threshold: float = 3.5, insurance_offset: int = 30):
        reference_price_locked = self.get_reference_price()
        # print(f'Reference price: {reference_price_locked}')
        
        lower_options = [option for option in self.options if reference_price_locked - search_interval <= option.strike_price and option.strike_price <= reference_price_locked and option.option_type == OptionType.PUT]
        lower_options.sort(key=lambda o: o.strike_price, reverse=True)
        higher_options = [option for option in self.options if reference_price_locked <= option.strike_price and option.strike_price <= reference_price_locked + search_interval and option.option_type == OptionType.CALL]
        higher_options.sort(key=lambda o: o.strike_price)

        lower_streamer_symbols = [o.streamer_symbol for o in lower_options]
        higher_streamer_symbols = [o.streamer_symbol for o in higher_options]

        # This will be super fast except the first time
        await self.live_prices.add_symbols(lower_streamer_symbols + higher_streamer_symbols)

        put_to_buy: Option | None = None
        put_to_sell: Option | None = None
        call_to_sell: Option | None = None
        call_to_buy: Option | None = None
        
        for option in lower_options:
            price = self.live_prices.quotes[option.streamer_symbol].bid_price
            # print(f'PUT price at strike {option.strike_price}: {price}')
            if price < price_threshold:
                put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                put_to_buy = next((o for o in lower_options if o.strike_price <= insurance_strike_price), None)
                break
        
        for option in higher_options:
            price = self.live_prices.quotes[option.streamer_symbol].bid_price
            # print(f'CALL price at strike {option.strike_price}: {price}')
            if price < price_threshold:
                call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                call_to_buy = next((o for o in higher_options if o.strike_price >= insurance_strike_price), None)
                break

        # print(f'Computed legs: {put_to_buy} {put_to_sell} {call_to_sell} {call_to_buy}')

        # Don't replace strategy after order is sent
        if self.position_manager.state <= PositionState.PENDING:
            try:
                suggested_position = IronCondor(put_to_buy, put_to_sell, call_to_sell, call_to_buy)
                self.position_manager.set_position(suggested_position)
            except Exception as e:
                # No need to set the position_manager to None as it already is per default
                print('Error building and testing order')
                print(e)

    def buying_power_effect(self):
        # Return None if position is not closed
        if self.buying_power_effect_open() is None or self.buying_power_effect_close() is None:
            return None
        return self.buying_power_effect_open() + self.buying_power_effect_close()

    def estimated_buying_power_effect(self):
        # Return None if position is not open
        if self.buying_power_effect_open() is None:
            return None
        return self.buying_power_effect_open() + self.estimated_buying_power_effect_close()

    def buying_power_effect_close(self):
        return self.position_manager.get_buying_power_effect_close()
    
    # This will be normally negative
    def estimated_buying_power_effect_close(self):
        return (
            + self.get_insurance_put_price(buy=False)
            - self.get_main_put_price(buy=True)
            - self.get_main_call_price(buy=True)
            + self.get_insurance_call_price(buy=False)
        ) * Decimal('100.0')

    def buying_power_effect_open(self):
        return self.position_manager.get_buying_power_effect_open()
    
    # This will be normally positive
    def estimated_buying_power_effect_open(self):
        return (
            - self.get_insurance_put_price()
            + self.get_main_put_price()
            + self.get_main_call_price()
            - self.get_insurance_call_price()
        ) * Decimal('100.0')
    
    def is_strategy_available(self):
        return self.position_manager.state >= PositionState.PENDING
