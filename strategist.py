import asyncio
import tkinter as tk
from datetime import date
from dataclasses import dataclass

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote
from tastytrade import Session, Account
from tastytrade.instruments import Option, Equity

from lib import TTConfig, TTOption, TTOptionSide

from live_prices import LivePrices, TastytradeWrapper

from lib import TTConfig, TTApi, TTOption, TTOptionSide, TTOrder, TTTimeInForce, TTPriceEffect, TTOrderType, TTLegAction, TTInstrumentType


@dataclass
class Strategist:
    live_prices: LivePrices
    underlying_streamer_symbol: str
    options_interval: int
    options_streamer_symbols: list[str]
    put_to_sell: TTOption | None = None
    put_to_buy: TTOption | None = None
    call_to_sell: TTOption | None = None
    call_to_buy: TTOption | None = None
    margin: float | None = None
    api: TTApi | None = None


    @classmethod
    async def create(
        cls,
        session: Session,
        underlying_symbol: str,
        options_interval: int
    ):
        streamer_symbols = await TastytradeWrapper.get_streamer_symbols_equities(session, [underlying_symbol])
        underlying_streamer_symbol = streamer_symbols[0]
        live_prices = await LivePrices.create(session, streamer_symbols)

        reference_price = (live_prices.quotes[underlying_streamer_symbol].bidPrice + live_prices.quotes[underlying_streamer_symbol].askPrice) / 2
        print(f'{underlying_streamer_symbol} is at {reference_price}')

        self = cls(live_prices, underlying_streamer_symbol, options_interval, [])

        self.api = TTApi(TTConfig(filename='tt.sandbox.config'))
        if not self.login_and_validate():
            print("Problem at login")
            return
        
        # Do an initial run so we have a strategy straight-away. Also populates the LivePrices more than required so we don't have to await it again in the continuous loop
        await self._build_strategy(session, search_interval=100)
        # Start the continuous build options loop
        asyncio.create_task(self._run_build_strategy(session))
        
        return self

    def login_and_validate(self):
        if not self.api.login():
            print("Login failed!")
            return False

        print("Login successful")

        if not self.api.validate():
            print("Validation failed!")
            return False

        print("Validation successful")

        if not self.api.fetch_accounts():
            print("Failed to fetch accounts!")
            return False

        print("Accounts fetched successfully")
        self.api.fetch_account_balance()
        return True
    
    @staticmethod
    def convert_option_to_tt_option(option):
        return TTOption(option.root_symbol, option.expiration_date.strftime('%y%m%d'), TTOptionSide.CALL if option.option_type == 'C' else TTOptionSide.PUT, int(option.strike_price))
        
    async def _run_build_strategy(self, session: Session, update_interval: int = 3):
        while True:
            await self._build_strategy(session, search_interval=50)
            await asyncio.sleep(update_interval)

    def get_reference_price(self):
        return (self.live_prices.quotes[self.underlying_streamer_symbol].bidPrice + self.live_prices.quotes[self.underlying_streamer_symbol].askPrice) / 2
    
    def get_put_to_sell_price(self):
        return self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
    
    def get_puy_to_buy_price(self):
        return self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
    
    def get_call_to_sell_price(self):
        return self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
    
    def get_call_to_buy_price(self):
        return self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice

    async def _build_strategy(self, session: Session, search_interval: int = 100, price_threshold: float = 3.5, insurance_offset: int = 30):
        today = date.today()
        dd = str(today.day).zfill(2)
        mm = str(today.month).zfill(2)
        yy = str(today.year)[-2:]
        exp_date = yy + mm + dd
        nearest_strike_price = round(self.get_reference_price() / self.options_interval) * self.options_interval
        lower_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price - search_interval, -self.options_interval)]
        higher_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price + search_interval, self.options_interval)]
        
        lower_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.PUT, strike) for strike in lower_strikes]
        higher_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.CALL, strike) for strike in higher_strikes]

        lower_streamer_symbols = [Option.occ_to_streamer_symbol(o.symbol) for o in lower_tt_options]
        higher_streamer_symbols = [Option.occ_to_streamer_symbol(o.symbol) for o in higher_tt_options]

        # This will be super fast except the first time
        await self.live_prices.add_symbols(lower_streamer_symbols + higher_streamer_symbols)

        # Logic for selecting put and call options based on the price threshold
        for option in lower_tt_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                self.put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                self.put_to_buy = TTOption('SPXW', exp_date, TTOptionSide.PUT, insurance_strike_price)
                break

        for option in higher_tt_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                self.call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                self.call_to_buy = TTOption('SPXW', exp_date, TTOptionSide.CALL, insurance_strike_price)
                break
        
        order = TTOrder(TTTimeInForce.GTC, 0.05, TTPriceEffect.DEBIT, TTOrderType.LIMIT)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.put_to_sell.symbol, 1, TTLegAction.STO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.put_to_buy.symbol, 1, TTLegAction.BTO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.call_to_sell.symbol, 1, TTLegAction.STO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.call_to_buy.symbol, 1, TTLegAction.BTO)
        
        try:
            order_response = self.api.simple_order(order)
            margin_required = float(order_response["buying-power-effect"]["change-in-buying-power"])
            self.margin = margin_required
        except Exception as e:
            print(f"Error updating/getting margin: {e}")
            self.margin = None
        
        await self.live_prices.add_symbols([self.call_to_buy.streamer_symbol, self.put_to_buy.streamer_symbol])

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
    session = Session(config.username, config.password, is_test=not config.use_prod)
    strategist = await Strategist.create(session, 'SPX', 5)

    root = tk.Tk()
    root.title("Strategist Winnings")

    winnings_label = tk.Label(root, text="Calculating...", font=("Helvetica", 16))
    winnings_label.pack(pady=10)
    
    put_to_buy_label = tk.Label(root, text="Put to Buy: -", font=("Helvetica", 12))
    put_to_buy_label.pack()

    put_to_sell_label = tk.Label(root, text="Put to Sell: -", font=("Helvetica", 12))
    put_to_sell_label.pack()

    call_to_sell_label = tk.Label(root, text="Call to Sell: -", font=("Helvetica", 12))
    call_to_sell_label.pack()

    call_to_buy_label = tk.Label(root, text="Call to Buy: -", font=("Helvetica", 12))
    call_to_buy_label.pack()

    margin_label = tk.Label(root, text="Margin: -", font=("Helvetica", 16))
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
                        text=f"Margin required: {('$' + strategist.margin) if strategist.margin is not None else 'N/A'}"
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
