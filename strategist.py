import asyncio
import tkinter as tk
from datetime import date, timedelta
from dataclasses import dataclass
from typing import List
from decimal import Decimal
from enum import Enum

from tastytrade import Session, Account
from tastytrade.account import CurrentPosition
from tastytrade.instruments import Option, OptionType
from tastytrade.instruments import get_option_chain
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType, PlacedOrderResponse, PlacedOrder, OrderStatus
from tastytrade.utils import TastytradeError

from lib import TTConfig

from live_prices import LivePrices
from account_updates import AccountUpdates


@dataclass
class IronCondor:
    put_buy: Option
    put_sell: Option
    call_sell: Option
    call_buy: Option

    def __init__(self, put_buy, put_sell, call_sell, call_buy):
        # Validate inputs
        if None in (put_buy, put_sell, call_sell, call_buy):
            raise ValueError("IronCondor cannot be initialized with None option objects.")

        # Initialize the instance variables
        self.put_buy = put_buy
        self.put_sell = put_sell
        self.call_sell = call_sell
        self.call_buy = call_buy

    def _order(self, open: bool, limit: Decimal) -> NewOrder:
        # Negative decimal to close position
        leg_put_buy = self.put_buy.build_leg(Decimal(1), OrderAction.BUY_TO_OPEN if open else OrderAction.SELL_TO_CLOSE)
        leg_put_sell = self.put_sell.build_leg(Decimal(1), OrderAction.SELL_TO_OPEN if open else OrderAction.BUY_TO_CLOSE)
        leg_call_sell = self.call_sell.build_leg(Decimal(1), OrderAction.SELL_TO_OPEN if open else OrderAction.BUY_TO_CLOSE)
        leg_call_buy = self.call_buy.build_leg(Decimal(1), OrderAction.BUY_TO_OPEN if open else OrderAction.SELL_TO_CLOSE)

        return NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[leg_put_buy, leg_put_sell, leg_call_sell, leg_call_buy],  # you can have multiple legs in an order
            price=limit  # limit price, $10/share debit for a total value of $50
        )
    
    # Opening Iron Condor gives money
    def opening_order(self, limit: Decimal = Decimal('0.05')):
        return self._order(True, limit)
    
    # Closing Iron Condor consts money
    def closing_order(self, limit: Decimal = Decimal('-0.05')):
        return self._order(False, limit)
    

class PositionState(Enum):
    PENDING = 'no order submitted'
    OPENING_REQUESTED = 'opening order submitted'
    OPEN = 'position is open'
    CLOSING_REQUESTED = 'closing order submitted'
    CLOSED = 'postition was closed'


@dataclass
class PositionManager:
    account_updates: AccountUpdates
    state: PositionState = PositionState.PENDING
    position: IronCondor | None = None
    open_response: PlacedOrderResponse | None = None
    close_response: PlacedOrderResponse | None = None

    def set_position(self, position: IronCondor):
        self.position = position
    
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
            self.state = PositionState.CLOSED
        return response
    
    async def margin_requirement(self, session: Session, account: Account):
        if self.position is None or self.open_response is None:
            await self.open_position(session, account, dry_run=True)
        return self.open_response.buying_power_effect.change_in_buying_power
    
    def margin_requirement_no_wait(self):
        if self.open_response is None:
            return None
        return self.open_response.buying_power_effect.change_in_buying_power
    
    def get_open_order(self) -> PlacedOrder:
        if self.state == PositionState.PENDING:
            return None
        order_id = self.open_response.order.id
        # Order update not received yet
        if order_id not in self.account_updates.orders:
            return self.open_response.order
        # Getting live status
        return self.account_updates.orders[order_id]
    
    def get_close_order(self) -> PlacedOrder:
        if self.state not in [PositionState.CLOSING_REQUESTED, PositionState.CLOSED]:
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

    def opening_profit(self) -> Decimal:
        if self.state == PositionState.PENDING or not self.is_open_order_filled():
            return None
        legs = self.get_open_order().legs
        profit = Decimal('0.0')
        for leg in legs:
            # Return None if order is not fully filled. Should not happen as we check at the start
            if leg.remaining_quantity > Decimal('0.0'):
                return None
            for fill in leg.fills:
                fill_cost = fill.quantity * fill.fill_price
                if leg.action == OrderAction.BUY_TO_OPEN:
                    profit -= fill_cost
                elif leg.action == OrderAction.SELL_TO_OPEN:
                    profit += fill_cost
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
    positions: List[CurrentPosition] | None = None


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

        reference_price = (live_prices.quotes[underlying_symbol].bidPrice + live_prices.quotes[underlying_symbol].askPrice) / 2
        print(f'{underlying_symbol} is at {reference_price}')

        # Blocking call
        options = get_option_chain(session_sandbox, root_symbol)[date.today() + timedelta(days=3)]
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
        return (self.live_prices.quotes[self.underlying_symbol].bidPrice + self.live_prices.quotes[self.underlying_symbol].askPrice) / 2
    
    def get_put_to_sell_price(self):
        return self.live_prices.quotes[self.position_manager.position.put_sell.streamer_symbol].bidPrice
    
    def get_puy_to_buy_price(self):
        return self.live_prices.quotes[self.position_manager.position.put_buy.streamer_symbol].askPrice
    
    def get_call_to_sell_price(self):
        return self.live_prices.quotes[self.position_manager.position.call_sell.streamer_symbol].bidPrice
    
    def get_call_to_buy_price(self):
        return self.live_prices.quotes[self.position_manager.position.call_buy.streamer_symbol].askPrice
    
    async def compute_margin_requirement(self, session: Session, account: Account):
        try:
            await self.position_manager.margin_requirement(session, account)
        except TastytradeError as e:
            print(f'Could not execute dry-run order. Error {e}')

    async def _build_strategy(self, search_interval: int = 500, price_threshold: float = 3.5, insurance_offset: int = 30):
        reference_price_locked = self.get_reference_price()
        print(f'Reference price: {reference_price_locked}')
        
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
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            # print(f'PUT price at strike {option.strike_price}: {price}')
            if price < price_threshold:
                put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                put_to_buy = next((o for o in lower_options if o.strike_price <= insurance_strike_price), None)
                break
        
        for option in higher_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            # print(f'CALL price at strike {option.strike_price}: {price}')
            if price < price_threshold:
                call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                call_to_buy = next((o for o in higher_options if o.strike_price >= insurance_strike_price), None)
                break

        # print(f'Computed legs: {put_to_buy} {put_to_sell} {call_to_sell} {call_to_buy}')
        try:
            suggested_position = IronCondor(put_to_buy, put_to_sell, call_to_sell, call_to_buy)
            self.position_manager.set_position(suggested_position)
            self.positions = await self.sandbox_account.a_get_positions(self.session_sandbox)
        except Exception as e:
            # No need to set the position_manager to None as it already is per default
            print('Error building and testing order')
            print(e)


    def winnings(self):
        return (
            - self.get_puy_to_buy_price()
            + self.get_put_to_sell_price()
            + self.get_call_to_sell_price()
            - self.get_call_to_buy_price()
        ) * 100
    
    def is_strategy_available(self):
        return self.position_manager is not None
    


async def main():
    config = TTConfig(filename='tt.config')
    config_sandbox = TTConfig(filename='tt.sandbox.config')
    session = Session(config.username, config.password, is_test=not config.use_prod)
    session_sandbox = Session(config_sandbox.username, config_sandbox.password, is_test=not config_sandbox.use_prod)
    account_sandbox = Account.get_accounts(session_sandbox)[0]
    print(f'Account number: {account_sandbox.account_number}')

    strategist = await Strategist.create(session, session_sandbox, account_sandbox, 'SPX', 'SPXW')

    root = tk.Tk()
    root.title("Strategist Winnings")

    winnings_label = tk.Label(root, text="Calculating...", font=("Helvetica", 30))
    winnings_label.pack(pady=10)
    
    put_to_buy_label = tk.Label(root, text="Put to Buy: -", font=("Helvetica", 20))
    put_to_buy_label.pack()

    put_to_sell_label = tk.Label(root, text="Put to Sell: -", font=("Helvetica", 20))
    put_to_sell_label.pack()

    call_to_sell_label = tk.Label(root, text="Call to Sell: -", font=("Helvetica", 20))
    call_to_sell_label.pack()

    call_to_buy_label = tk.Label(root, text="Call to Buy: -", font=("Helvetica", 20))
    call_to_buy_label.pack()

    margin_label = tk.Label(root, text="Margin: -", font=("Helvetica", 30))
    margin_label.pack(pady=10)
    
    positions_label = tk.Label(root, text="Open Positions: 0", font=("Helvetica", 20))
    positions_label.pack(pady=10)
    
    order_button = tk.Button(root, text="Open Order", bg="green", fg="black", font=("Helvetica", 20))
    order_button.pack(pady=10)

    def toggle_order(strategist: Strategist):
        current_text = order_button.cget("text")
        if current_text == "Open Order":
            order_button.config(text="Close Order", bg="red")
        else:
            order_button.config(text="Open Order", bg="green")

    order_button.config(command=lambda: toggle_order(strategist))


    async def update_winnings(tick_interval: float = 0.1):
        try:
            while True:
                if strategist.is_strategy_available():
                    winnings_label.config(text=f"Winnings: ${strategist.winnings():.2f}")
                    if strategist.position_manager.position.put_sell:
                        put_to_sell_label.config(
                            text=f"Put to Sell ({strategist.position_manager.position.put_sell.symbol}): "
                                 f"${strategist.get_put_to_sell_price()}"
                        )
                    if strategist.position_manager.position.put_buy:
                        put_to_buy_label.config(
                            text=f"Put to Buy ({strategist.position_manager.position.put_buy.symbol}): "
                                 f"${strategist.get_puy_to_buy_price()}"
                        )
                    if strategist.position_manager.position.call_buy:
                        call_to_buy_label.config(
                            text=f"Call to Buy ({strategist.position_manager.position.call_buy.symbol}): "
                                 f"${strategist.get_call_to_buy_price()}"
                        )
                    if strategist.position_manager.position.call_sell:
                        call_to_sell_label.config(
                            text=f"Call to Sell ({strategist.position_manager.position.call_sell.symbol}): "
                                 f"${strategist.get_call_to_sell_price()}"
                        )
                    if strategist.positions:
                        positions_label.config(
                            text=f"Open Positions: "
                                 f"{len(strategist.positions)}"
                        )
                    margin_label.config(
                        text=f"Margin required: {(f'${strategist.position_manager.margin_requirement_no_wait():.2f}') if strategist.position_manager.margin_requirement_no_wait() is not None else 'N/A'}"
                    )
                else:
                    winnings_label.config(text="Wait for strategy to initialize...")
                await asyncio.sleep(tick_interval)
        except asyncio.CancelledError:
            pass

    async def tkinter_update():
        while True:
            root.update()
            await asyncio.sleep(0.1)

    # Start the update coroutines
    asyncio.create_task(update_winnings())
    await tkinter_update()

    await strategist.live_prices.close_channel()
    session.destroy()

if __name__ == '__main__':
    asyncio.run(main())
