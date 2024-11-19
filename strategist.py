import asyncio
import tkinter as tk
from datetime import date, timedelta
from dataclasses import dataclass
from typing import List
from decimal import Decimal

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote
from tastytrade import Session, Account
from tastytrade.instruments import Option, Equity, OptionType
from tastytrade.instruments import get_option_chain
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

from lib import TTConfig

from live_prices import LivePrices, TastytradeWrapper


@dataclass
class Strategist:
    live_prices: LivePrices
    underlying_symbol: str
    root_symbol: str
    options: List[Option]
    put_to_sell: Option | None = None
    put_to_buy: Option | None = None
    call_to_sell: Option | None = None
    call_to_buy: Option | None = None
    margin: float | None = None


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

        reference_price = (live_prices.quotes[underlying_symbol].bidPrice + live_prices.quotes[underlying_symbol].askPrice) / 2
        print(f'{underlying_symbol} is at {reference_price}')

        options = get_option_chain(session_sandbox, root_symbol)[date.today() + timedelta(days=0)]

        self = cls(live_prices, underlying_symbol, root_symbol, options)
        
        # Do an initial run so we have a strategy straight-away. Also populates the LivePrices more than required so we don't have to await it again in the continuous loop
        await self._build_strategy(session_sandbox, account_sandbox)
        # Start the continuous build options loop
        asyncio.create_task(self._run_build_strategy(session_sandbox, account_sandbox))
        
        return self

        
    async def _run_build_strategy(self, session: Session, account: Account, update_interval: int = 3):
        while True:
            await self._build_strategy(session, account)
            await asyncio.sleep(update_interval)

    def get_reference_price(self):
        return (self.live_prices.quotes[self.underlying_symbol].bidPrice + self.live_prices.quotes[self.underlying_symbol].askPrice) / 2
    
    def get_put_to_sell_price(self):
        return self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
    
    def get_puy_to_buy_price(self):
        return self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
    
    def get_call_to_sell_price(self):
        return self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
    
    def get_call_to_buy_price(self):
        return self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice

    async def _build_strategy(self, session: Session, account: Account, search_interval: int = 500, price_threshold: float = 3.5, insurance_offset: int = 30):
        reference_price_locked = self.get_reference_price()
        lower_options = [option for option in self.options if reference_price_locked - search_interval <= option.strike_price and option.strike_price <= reference_price_locked and option.option_type == OptionType.PUT]
        lower_options.sort(key=lambda o: o.strike_price, reverse=True)
        higher_options = [option for option in self.options if reference_price_locked <= option.strike_price and option.strike_price <= reference_price_locked + search_interval and option.option_type == OptionType.CALL]
        higher_options.sort(key=lambda o: o.strike_price)

        lower_streamer_symbols = [o.streamer_symbol for o in lower_options]
        higher_streamer_symbols = [o.streamer_symbol for o in higher_options]

        # This will be super fast except the first time
        await self.live_prices.add_symbols(lower_streamer_symbols + higher_streamer_symbols)

        # Logic for selecting put and call options based on the price threshold
        for option in lower_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            # print(f'PUT price at strike {option.strike_price}: {price}')
            if price < price_threshold:
                self.put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                self.put_to_buy = next((o for o in lower_options if o.strike_price <= insurance_strike_price), None)
                break
        
        for option in higher_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            # print(f'CALL price at strike {option.strike_price}: {price}')
            if price < price_threshold:
                self.call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                self.call_to_buy = next((o for o in higher_options if o.strike_price >= insurance_strike_price), None)
                break
        
        order = self._build_order()
        try:
            response = account.place_order(session, order, dry_run=True)  # a test order
            self.margin = response.buying_power_effect.change_in_buying_power
        except Exception as e:
            print(f"Error updating/getting margin: {e}")
            self.margin = None

    
    def _build_order(self) -> NewOrder:
        leg_put_buy_to_open = self.put_to_buy.build_leg(Decimal(1), OrderAction.BUY_TO_OPEN)
        leg_put_sell_to_open = self.put_to_sell.build_leg(Decimal(1), OrderAction.SELL_TO_OPEN)
        leg_call_sell_to_open = self.call_to_sell.build_leg(Decimal(1), OrderAction.SELL_TO_OPEN)
        leg_call_buy_to_open = self.call_to_buy.build_leg(Decimal(1), OrderAction.BUY_TO_OPEN)

        return NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[leg_put_buy_to_open, leg_put_sell_to_open, leg_call_sell_to_open, leg_call_buy_to_open],  # you can have multiple legs in an order
            price=Decimal('-10')  # limit price, $10/share debit for a total value of $50
        )


    def winnings(self):
        return (
            -self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
            + self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
            + self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
            - self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice
        ) * 100
    
    def is_strategy_available(self):
        legs_are_available = self.put_to_buy is not None and \
            self.put_to_sell is not None and \
            self.call_to_sell is not None and \
            self.call_to_buy is not None
        leg_prices_are_available = legs_are_available and self.put_to_buy.streamer_symbol in self.live_prices.quotes and \
            self.put_to_sell.streamer_symbol in self.live_prices.quotes and \
            self.call_to_sell.streamer_symbol in self.live_prices.quotes and \
            self.call_to_buy.streamer_symbol in self.live_prices.quotes
        return legs_are_available and leg_prices_are_available


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

    async def update_winnings(tick_interval: float = 0.1):
        try:
            while True:
                if strategist.is_strategy_available():
                    winnings_label.config(text=f"Winnings: ${strategist.winnings():.2f}")
                    if strategist.put_to_sell:
                        put_to_sell_label.config(
                            text=f"Put to Sell ({strategist.put_to_sell.symbol}): "
                                 f"${strategist.get_put_to_sell_price()}"
                        )
                    if strategist.put_to_buy:
                        put_to_buy_label.config(
                            text=f"Put to Buy ({strategist.put_to_buy.symbol}): "
                                 f"${strategist.get_puy_to_buy_price()}"
                        )
                    if strategist.call_to_buy:
                        call_to_buy_label.config(
                            text=f"Call to Buy ({strategist.call_to_buy.symbol}): "
                                 f"${strategist.get_call_to_buy_price()}"
                        )
                    if strategist.call_to_sell:
                        call_to_sell_label.config(
                            text=f"Call to Sell ({strategist.call_to_sell.symbol}): "
                                 f"${strategist.get_call_to_sell_price()}"
                        )
                    margin_label.config(
                        text=f"Margin required: {(f'${strategist.margin:.2f}') if strategist.margin is not None else 'N/A'}"
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
